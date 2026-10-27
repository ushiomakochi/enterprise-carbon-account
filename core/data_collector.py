"""
数据采集调度器 — 定时从数据源拉取数据，触发碳减排计算，存储结果。

工作流程:
1. 对每台有效**机器**，根据它自己的 data_source_type 初始化对应的适配器
2. 定时采集数据 → 调用 CarbonCalculator.compute(machine_id) → 存储到 monitoring_data
3. 更新项目累计减排量（= 该项目各机器累计值之和）
4. 检查告警规则

**为什么状态都按机器键**：一个项目可以有多台设备，每台一份数据源配置、
一个 MQTT 主题。适配器、防抖窗口、取数状态如果按项目存，B 机器的消息
会被 A 机器刚开的防抖窗口吞掉，累计值也会跨机器串味。
"""

import json
import logging
import threading
import time
from datetime import datetime

from .project_manager import (get_db, list_projects, list_all_machines,
                              get_machine, get_project, project_cumulative)
from .carbon_calculator import CarbonCalculator

log = logging.getLogger("collector")


def _gap_reset_hours():
    """断档重基线阈值（小时）。0 或负数 = 关闭该保护。"""
    try:
        from config import COLLECT_GAP_RESET_HOURS
        return int(COLLECT_GAP_RESET_HOURS)
    except Exception:
        return 168


def _gap_hours(prev_ts, cur_ts):
    """两条记录之间隔了多少小时；解析不出来返回 None（宁可不判，也不要误判）。"""
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        a = datetime.strptime(str(prev_ts)[:19], fmt)
        b = datetime.strptime(str(cur_ts)[:19], fmt)
        return (b - a).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None


def _auto_bridge_enabled():
    """断档是否自动兜底（扣掉已核查量、只补记未核查的部分）。默认开。"""
    try:
        from config import COLLECT_GAP_AUTO_BRIDGE
        return bool(int(COLLECT_GAP_AUTO_BRIDGE))
    except Exception:
        return True


def _collector_formula(mach, proj, api_url):
    """取（必要时初始化）该机器缓存的公式实例。"""
    from .carbon_calculator import CarbonCalculator
    mid = mach["id"]
    if mid not in CarbonCalculator._instances:
        CarbonCalculator.init_machine_formula(mach, proj.get("hook_name", ""), api_url)
    return CarbonCalculator.get_instance(mid)


def _raw_fields(proj):
    """该公式要求哪些原始字段（用于把上一条读数映射成 prev_*）。"""
    try:
        from .data_import import get_required_raw_fields
        return get_required_raw_fields(proj)
    except Exception:
        return []


def _raise_gap_alert(conn, proj_id, machine_id, machine_name, gap_h, prev_ts, cur_ts,
                     message=None):
    """断档时写一条告警（rule_id 为空 = 系统提示，不是规则触发）。

    必须留痕：断档要么被自动兜底补记了、要么被当成新起点少算了 ——
    两种都是**自动发生的账面动作**，用户要能从「告警」页签看到。
    """
    msg = message or (f"机器「{machine_name or machine_id}」断档 {gap_h:.1f} 小时"
                      f"（{prev_ts} → {cur_ts}），已按新起点处理，该段产量未计入减排量。"
                      f"若这段未被核查过、需要计入，请导入这一段数据。")
    try:
        conn.execute(
            """INSERT INTO alert_log (rule_id, project_id, alert_message)
               VALUES (NULL, ?, ?)""",
            (proj_id, msg))
        conn.commit()
    except Exception as e:
        log.warning("[采集器] 写断档告警失败（不影响采集）: %s", e)
    log.warning("[采集器] %s", msg)


def _get_adapter(source_type):
    """惰性导入适配器（避免启动时加载 paho/requests，加快应用启动）。"""
    if source_type == "mqtt":
        from adapters.mqtt_adapter import MQTTAdapter
        return MQTTAdapter
    if source_type == "api":
        from adapters.api_adapter import ApiAdapter
        return ApiAdapter
    from adapters.manual_adapter import ManualAdapter
    return ManualAdapter


class DataCollector:
    """数据采集调度器"""

    # ⚠️ 下面这几个字典全部按 **machine_id** 键（不是 project_id）：
    #    同一个项目里的两台机器各有各的适配器、防抖窗口和取数状态。
    _adapters = {}          # {machine_id: adapter_instance}
    _running = False
    _thread = None
    _interval = 300         # 默认采集间隔（秒）
    _last_collect_time = {}  # MQTT 实时收集防抖: {machine_id: timestamp}
    _last_cleanup = None     # 上次清理时间
    _fetch_ok = {}           # {machine_id: 上次是否取到数据} —— 用于「无数据」只在状态变化时告警
    _last_api_fetch = {}     # API 通道节流: {machine_id: 上次成功拉取时间戳} —— 按平台 poll_interval 拉，不跟全局 tick
    _last_machine_ids = None  # 上次参与采集的机器 id 列表 —— 用于集合变化时才记日志
    _continuity_probing = set()  # 定向回查独立持有暂停标记，避免干扰普通回溯
    _backfilling = set()     # 正在回溯的 machine_id 集合 —— 回溯期间暂停这些机器的采集，避免断档兜底误触发

    @classmethod
    def start(cls, interval: int = None):
        """启动后台采集线程"""
        if cls._running:
            return

        from config import COLLECTION_INTERVAL_SECONDS
        cls._interval = interval or COLLECTION_INTERVAL_SECONDS
        cls._running = True
        cls._thread = threading.Thread(target=cls._run_loop, daemon=True)
        cls._thread.start()
        # 旧数据清理放到后台线程，不阻塞主线程启动
        threading.Thread(target=cls._cleanup_old_data, daemon=True).start()
        log.info("[采集器] 已启动，采集间隔: %ss", cls._interval)

    @classmethod
    def stop(cls):
        """停止后台采集"""
        cls._running = False
        # 断开所有适配器
        for adapter in cls._adapters.values():
            try:
                adapter.disconnect()
            except Exception:
                pass
        cls._adapters.clear()
        log.info("[采集器] 已停止")

    @classmethod
    def _record_exists(cls, machine_id: int, source: str, timestamp) -> bool:
        """同机器、同来源、同时间戳的记录是否已存在。

        历史 API 的时间窗每轮都和上一轮重叠，靠这个去重保证
        定时拉历史是幂等的 —— 重复拉同一窗口不会产生重复数据。
        """
        if not timestamp:
            return False
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT 1 FROM monitoring_data "
                "WHERE machine_id = ? AND source = ? AND timestamp = ? LIMIT 1",
                (machine_id, source, timestamp)).fetchone()
            return row is not None
        except Exception:
            return False
        finally:
            conn.close()

    @classmethod
    def drop_adapter(cls, machine_id: int):
        """作废某台机器缓存的适配器（前端改了数据源配置后调用）。

        下一个采集 tick 会按新配置重建 —— 否则旧 MQTT 连接继续按
        老 broker/topic/前置转换订阅，改了配置也不生效。
        """
        adapter = cls._adapters.pop(machine_id, None)
        if adapter:
            try:
                adapter.disconnect()
            except Exception:
                pass
            log.info("[采集器] 机器 #%s 适配器已作废，将按新配置重建", machine_id)
        cls._fetch_ok.pop(machine_id, None)
        cls._last_collect_time.pop(machine_id, None)
        cls._last_api_fetch.pop(machine_id, None)

    @classmethod
    def _run_loop(cls):
        """采集主循环"""
        while cls._running:
            try:
                cls._collect_all()
            except Exception as e:
                log.error("[采集器] 采集异常: %s", e, exc_info=True)
            # 每天清理一次过期数据
            cls._cleanup_old_data()
            time.sleep(cls._interval)

    @classmethod
    def _cleanup_old_data(cls):
        """清理超过保留期的监测数据"""
        from config import DATA_RETENTION_DAYS
        if DATA_RETENTION_DAYS <= 0:
            return  # 0 或负数表示永不过期

        now = time.time()
        # 至少间隔 24 小时才再次清理
        if cls._last_cleanup and now - cls._last_cleanup < 86400:
            return
        cls._last_cleanup = now

        try:
            conn = get_db()
            # 已作废项目和机器是只读档案，不参与运行数据保留期清理。
            cutoff = f"-{DATA_RETENTION_DAYS} days"
            rows = conn.execute(
                """SELECT d.id,d.project_id,d.machine_id FROM monitoring_data d
                   JOIN carbon_projects p ON p.id=d.project_id
                   LEFT JOIN project_machines m ON m.id=d.machine_id
                   WHERE d.created_at < datetime('now','localtime',?)
                     AND p.lifecycle_status='active'
                     AND (d.machine_id IS NULL OR m.lifecycle_status='active')""",
                (cutoff,)
            ).fetchall()
            count = len(rows)
            if count:
                ids = [r["id"] for r in rows]
                project_ids = {r["project_id"] for r in rows}
                machine_ids = {r["machine_id"] for r in rows if r["machine_id"] is not None}
                conn.executemany("DELETE FROM monitoring_data WHERE id=?", [(i,) for i in ids])
                conn.commit()
                from .reduction_ledger import reconcile_machine, refresh_totals
                for machine_id in machine_ids:
                    reconcile_machine(machine_id, apply=True)
                totals_conn = get_db()
                try:
                    for project_id in project_ids:
                        refresh_totals(totals_conn, project_id)
                    totals_conn.commit()
                finally:
                    totals_conn.close()
                log.info("[采集器] 清理 %s 条过期监测数据（>%s天）", count, DATA_RETENTION_DAYS)
            conn.close()
        except Exception as e:
            log.warning("[采集器] 清理过期数据失败: %s", e)

    @classmethod
    def _make_mqtt_callback(cls, machine_id: int):
        """
        为 MQTT 适配器创建实时数据回调。
        MQTT 每收到一条消息就调用此回调，立即触发采集+计算，无需等待轮询。
        内置 5 秒防抖，避免高频消息导致重复计算。

        ⚠️ 防抖按**机器**独立计时：防抖窗口要是按项目共用，
        1 号机的消息会在这 5 秒里把 2 号机的消息一并吞掉。
        """
        def on_data(raw_data: dict, timestamp: str):
            now = time.time()
            last = cls._last_collect_time.get(machine_id, 0)
            if now - last < 5:
                return  # 5 秒内已处理过，跳过
            cls._last_collect_time[machine_id] = now
            try:
                cls.collect_and_store(machine_id, raw_data, source="auto", timestamp=timestamp)
                log.debug("[采集器·MQTT] 机器 #%s 实时采集完成", machine_id)
            except Exception as e:
                log.warning("[采集器·MQTT] 机器 #%s 实时采集失败: %s", machine_id, e)
        return on_data

    @classmethod
    def _collect_all(cls):
        """对所有有效机器执行一次采集（每台机器按自己的数据源配置拉数）"""
        machines = list_all_machines()
        non_manual = [m for m in machines if m.get("data_source_type", "manual") != "manual"]

        # 只在「参与采集的机器集合」变化时记一条，不每 tick 刷。
        # 原来这条每 tick 一条、而且消息里罗列全部项目 id ——
        # 100 个项目时等于每 tick 写一条 1500 字符的日志。
        try:
            now_ids = sorted(m["id"] for m in non_manual)
            if now_ids != cls._last_machine_ids:
                cls._last_machine_ids = now_ids
                kinds = sorted({m.get("data_source_type", "?") for m in non_manual})
                log.info("[采集器] 参与采集的机器变为 %s 台（数据源类型：%s）",
                         len(now_ids), "、".join(kinds) or "无")
        except Exception:
            pass

        for mach in machines:
            mach_id = mach["id"]
            proj_id = mach["project_id"]
            source_type = mach.get("data_source_type", "manual")

            # 跳过手动模式（数据靠录入/导入）
            if source_type == "manual":
                continue

            # 回溯期间暂停该机器的采集：否则采集器会在回溯写入的时间范围内
            # 触发断档兜底，写一条 gap_reset 记录把这段减排量清零。
            if mach_id in cls._backfilling or mach_id in cls._continuity_probing:
                continue

            # 初始化或获取适配器
            if mach_id not in cls._adapters:
                adapter_cls = _get_adapter(source_type)
                if not adapter_cls:
                    log.warning("[采集器] 未知数据源类型: %s", source_type,
                                extra={"project_id": proj_id})
                    continue
                try:
                    config = json.loads(mach.get("data_source_config", "{}") or "{}")
                except json.JSONDecodeError:
                    config = {}
                try:
                    normalizer = json.loads(mach.get("normalizer_config") or "{}")
                except (ValueError, TypeError):
                    normalizer = {}
                if normalizer.get("hook"):
                    config["normalizer"] = normalizer
                # MQTT 适配器传入实时回调，收到消息即触发采集
                if source_type == 'mqtt':
                    adapter = adapter_cls(config, on_data=cls._make_mqtt_callback(mach_id))
                else:
                    adapter = adapter_cls(config)
                log.info("[采集器] 机器 #%s（%s）初始化 %s 适配器...",
                         mach_id, mach.get("name", ""), source_type,
                         extra={"project_id": proj_id})
                if adapter.connect():
                    cls._adapters[mach_id] = adapter
                    log.info("[采集器] 机器 #%s %s 适配器连接成功", mach_id, source_type,
                             extra={"project_id": proj_id})
                else:
                    log.warning("[采集器] 机器 #%s %s 适配器连接失败，跳过", mach_id, source_type,
                                extra={"project_id": proj_id})
                    continue

            adapter = cls._adapters[mach_id]

            # 历史 API 适配器：一次拉一个时间窗的**一批**记录，逐条入账。
            # 与 MQTT/REST 的单条 fetch_latest 不同，它天然带补断档能力。
            if hasattr(adapter, "fetch_history"):
                # API 通道节流：多久拉一次由平台声明（前置转换文件的 poll_interval_seconds），
                # 而不是全局 60 秒 tick —— 否则对 API 平台每分钟重复请求、还拉不到新点。
                interval = getattr(adapter, "poll_interval_seconds", 3600)
                if time.time() - cls._last_api_fetch.get(mach_id, 0) < interval:
                    continue
                try:
                    records = adapter.fetch_history()
                    # 平台正常响应（含空结果）才算「拉过一次」，异常时下个 tick 快速重试
                    cls._last_api_fetch[mach_id] = time.time()
                except Exception as e:
                    log.warning("[采集器] 机器 #%s 历史拉取失败: %s", mach_id, e,
                                extra={"project_id": proj_id})
                    continue
                if not records:
                    if cls._fetch_ok.get(mach_id, True):
                        cls._fetch_ok[mach_id] = False
                        log.warning("[采集器] 机器 #%s 历史窗口内无数据", mach_id,
                                    extra={"project_id": proj_id})
                    continue
                if not cls._fetch_ok.get(mach_id, True):
                    cls._fetch_ok[mach_id] = True
                    log.info("[采集器] 机器 #%s 数据已恢复", mach_id, extra={"project_id": proj_id})
                log.info("[采集器] 机器 #%s 历史拉取 %s 条，开始逐条入账",
                         mach_id, len(records), extra={"project_id": proj_id})
                stored = 0
                for rec in records:
                    ts = rec.get("timestamp")
                    if cls._record_exists(mach_id, "auto", ts):
                        continue  # 时间窗重叠，上一轮已入账，跳过
                    try:
                        cls.collect_and_store(mach_id, rec, source="auto", timestamp=ts,
                                              prev_before=True, update_totals=False)
                        stored += 1
                    except Exception as e:
                        log.warning("[采集器] 机器 #%s 历史记录 %s 入账失败: %s",
                                    mach_id, ts, e, extra={"project_id": proj_id})
                if stored:
                    from .reduction_ledger import reconcile_machine
                    reconcile_machine(mach_id, apply=True)
                log.info("[采集器] 机器 #%s 本次入账 %s/%s 条", mach_id, stored, len(records),
                         extra={"project_id": proj_id})
                continue

            # 获取数据
            log.debug("[采集器] 机器 #%s 拉取数据...", mach_id)
            raw_data = adapter.fetch_latest()
            if not raw_data:
                # 「取不到数据」只在**状态变化**时记一条 —— 传感器一离线就每 tick
                # 一条 WARNING，是仅次于成功日志的第二大噪声源（实测单项目就占全表 45%）。
                # 持续离线期间保持静默，恢复时再记一条，两头都不丢。
                if cls._fetch_ok.get(mach_id, True):
                    cls._fetch_ok[mach_id] = False
                    log.warning("[采集器] 机器 #%s 未获取到数据（可能传感器未就绪），"
                                "在恢复前不再重复告警", mach_id, extra={"project_id": proj_id})
                continue

            if not cls._fetch_ok.get(mach_id, True):
                cls._fetch_ok[mach_id] = True
                log.info("[采集器] 机器 #%s 数据已恢复", mach_id, extra={"project_id": proj_id})

            # 成功取数走 DEBUG：这是每机器每 tick 一条的主噪声源，
            # 不该占 INFO（落库阈值是 INFO）
            log.debug("[采集器] 机器 #%s 获取到数据: %s",
                      mach_id, json.dumps(raw_data, ensure_ascii=False)[:200])

            # 执行计算并存储
            try:
                cls.collect_and_store(mach_id, raw_data, source="auto")
                log.debug("[采集器] 机器 #%s 采集+计算完成", mach_id)
            except Exception as e:
                log.error("[采集器] 机器 %s 计算失败: %s", mach_id, e,
                          exc_info=True, extra={"project_id": proj_id})

    @classmethod
    def collect_one(cls, machine_id: int) -> dict:
        """
        手动触发单台机器的采集+计算（用于测试和调试）。

        返回:
            dict: {"status": "成功"/"失败", "result": {...}, "msg": "..."}
        """
        import config as cfg

        mach = get_machine(machine_id)
        if not mach:
            return {"status": "失败", "msg": f"机器 {machine_id} 不存在"}
        if mach.get("lifecycle_status") != "active":
            return {"status": "失败", "msg": "机器已作废，不能继续采集"}

        source_type = mach.get("data_source_type", "manual")
        if source_type == "manual":
            return {"status": "失败", "msg": "该机器为手动录入模式，无法自动采集。请使用录入数据功能"}

        # 初始化适配器（每次重建以使用最新配置）
        if machine_id in cls._adapters:
            try:
                cls._adapters[machine_id].disconnect()
            except Exception:
                pass
            del cls._adapters[machine_id]

        adapter_cls = _get_adapter(source_type)
        if not adapter_cls:
            return {"status": "失败", "msg": f"未知数据源类型: {source_type}"}
        try:
            config = json.loads(mach.get("data_source_config", "{}") or "{}")
        except json.JSONDecodeError:
            config = {}
        try:
            normalizer = json.loads(mach.get("normalizer_config") or "{}")
        except (ValueError, TypeError):
            normalizer = {}
        if normalizer.get("hook"):
            config["normalizer"] = normalizer
        # MQTT 适配器传入实时回调
        if source_type == 'mqtt':
            adapter = adapter_cls(config, on_data=cls._make_mqtt_callback(machine_id))
        else:
            adapter = adapter_cls(config)
        if not adapter.connect():
            detail = getattr(adapter, '_last_error', '') or '请检查配置和网络'
            return {"status": "失败", "msg": f"{source_type} 适配器连接失败: {detail}"}
        cls._adapters[machine_id] = adapter

        # 历史 API：一次拉一个时间窗的批量记录，逐条入账（与定时采集同一条链）
        if hasattr(adapter, "fetch_history"):
            try:
                records = adapter.fetch_history()
            except Exception as e:
                return {"status": "失败", "msg": f"历史拉取失败: {e}"}
            if not records:
                detail = getattr(adapter, '_last_error', '') or '时间窗内没有数据'
                return {"status": "失败", "msg": f"未获取到数据: {detail}"}
            stored, skipped, first_err = 0, 0, None
            for rec in records:
                ts = rec.get("timestamp")
                if cls._record_exists(machine_id, "auto", ts):
                    skipped += 1
                    continue
                try:
                    cls.collect_and_store(machine_id, rec, source="auto", timestamp=ts,
                                          prev_before=True, update_totals=False)
                    stored += 1
                except Exception as e:
                    if first_err is None:
                        first_err = f"{ts}: {e}"
            if stored:
                from .reduction_ledger import reconcile_machine
                reconcile_machine(machine_id, apply=True)
            msg = f"入账 {stored} 条"
            if skipped:
                msg += f"（跳过已存在 {skipped} 条）"
            if first_err:
                msg += f"，部分失败: {first_err}"
            return {"status": "成功", "result": {"stored": stored}, "msg": msg}

        raw_data = adapter.fetch_latest()
        if not raw_data:
            detail = getattr(adapter, '_last_error', '') or '数据源可能尚未产生数据'
            return {"status": "失败", "msg": f"未获取到数据: {detail}"}

        try:
            result = cls.collect_and_store(machine_id, raw_data, source="auto")
            return {"status": "成功", "result": result, "msg": "采集成功"}
        except Exception as e:
            return {"status": "失败", "msg": f"计算失败: {str(e)}"}

    @classmethod
    def _store_baseline_row(cls, conn, machine_id: int, proj_id: int, raw_data: dict,
                            source: str, timestamp: str, reason: str = None,
                            prev_ts: str = None, update_totals: bool = True) -> dict:
        """落一条「基线行」：本期减排量记 0，累计也记 0。

        见 collect_and_store 里那段说明。基线行的作用是给下一条当 prev，
        所以原始读数要原样存进 raw_data（下一次靠它算增量）。

        两种触发情形：
          · 该机器第一条读数（本来就没有"上一条"，无话可说）
          · **断档超阈值**（reason 非空）—— 这种必须写告警留痕，见 _raise_gap_alert

        不跑阈值告警：这一条什么都没测到（本来就不是一个"期间"），
        拿 0 去撞 `lt`（小于阈值）类规则只会误报。
        但断档这件事本身要告警，所以单独写一条。
        """
        note = reason or "首条读数作为基线，不计减排量"
        has_later = bool(conn.execute(
            "SELECT 1 FROM monitoring_data WHERE machine_id=? AND timestamp>? LIMIT 1",
            (machine_id, timestamp)).fetchone())
        prior_cumulative = 0.0
        if prev_ts:
            prior = conn.execute(
                "SELECT cumulative_reduction FROM monitoring_data WHERE machine_id=? "
                "AND timestamp=? ORDER BY id DESC LIMIT 1", (machine_id, prev_ts)).fetchone()
            prior_cumulative = float(prior["cumulative_reduction"] or 0) if prior else 0.0
        # gap_reset 是给「断档兜底」工具认的**机器可读标记**（tools/bridge_gap.py）：
        # 带这个标记的行 = 这里发生过断档、原始读数已存下、减排量暂记 0。
        # 别改成靠解析 note 文字，那样一改文案就失效。
        bd = {"baseline": True, "note": note}
        if reason:
            bd["gap_reset"] = True
        conn.execute(
            """INSERT INTO monitoring_data
               (project_id, machine_id, timestamp, raw_data, carbon_reduction,
                cumulative_reduction, breakdown, source)
               VALUES (?, ?, ?, ?, 0, ?, ?, ?)""",
            (
                proj_id,
                machine_id,
                timestamp,
                json.dumps(raw_data, ensure_ascii=False),
                prior_cumulative,
                json.dumps(bd, ensure_ascii=False),
                source,
            )
        )
        if reason:
            m = conn.execute("SELECT name FROM project_machines WHERE id = ?",
                             (machine_id,)).fetchone()
            _raise_gap_alert(conn, proj_id, machine_id,
                             (m["name"] if m else ""), _gap_hours(prev_ts, timestamp) or 0,
                             prev_ts, timestamp)
        # 基线贡献 0，但项目累计是各机器之和，重算一遍保证口径一致
        # update_totals=False（批量回溯）时跳过，最后统一重算
        if update_totals:
            from .reduction_ledger import refresh_totals
            refresh_totals(conn, proj_id)
        conn.commit()
        conn.close()
        if has_later and update_totals:
            from .reduction_ledger import reconcile_machine
            reconcile_machine(machine_id, apply=True)
        log.info("[采集器] 机器 #%s 作为基线(不计减排)，时间 %s，原因: %s",
                 machine_id, timestamp, note, extra={"project_id": proj_id})
        return {"carbon_reduction": 0.0, "cumulative_reduction": prior_cumulative,
                "breakdown": {"baseline": True, "note": note}, "is_baseline": True}

    @classmethod
    def _store_bridge_row(cls, conn, machine_id: int, proj_id: int, raw_data: dict,
                          source: str, timestamp: str, decision: dict,
                          prev_ts: str, gap_h: float,
                          prev_cumulative: float = 0.0,
                          update_totals: bool = True) -> dict:
        """落一条「断档兜底补记行」。

        断档期间的读数差 = 已核查段 + 未核查段。已核查那部分扣掉（它的量已经在
        核查记录里了），只把**未核查的部分**记成减排量 —— 见 core/gap_bridge.py。

        同时写告警留痕：这是一次**自动的账面调整**，不是原始采集值，
        报告里靠 breakdown.bridge 标记能看出来。
        """
        booked = float(decision.get("booked") or 0)
        has_later = bool(conn.execute(
            "SELECT 1 FROM monitoring_data WHERE machine_id=? AND timestamp>? LIMIT 1",
            (machine_id, timestamp)).fetchone())
        bd = decision.get("breakdown") or {}
        # cumulative_reduction 是「该机器自己的累计」，必须**接着上一条往下加**。
        # 曾经这里直接写成 booked，于是断档兜底一落库，这台机器的累计就从
        # （断档前的累计）掉成（本次补记量）—— project_cumulative() 取的是每台机器
        # 最新一行的累计，项目累计随之被抹掉，还会进报告和总览。
        # 而这条行又成了下一条读数的 prev，prev_cumulative 从此一直是错的，永不自愈。
        cumulative = round(float(prev_cumulative or 0) + booked, 6)
        bd["cumulative_note"] = (f"本行累计 = 断档前累计 {float(prev_cumulative or 0):.6f} "
                                 f"+ 补记 {booked:.6f} = {cumulative:.6f}")
        conn.execute(
            """INSERT INTO monitoring_data
               (project_id, machine_id, timestamp, raw_data, carbon_reduction,
                cumulative_reduction, breakdown, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (proj_id, machine_id, timestamp,
             json.dumps(raw_data, ensure_ascii=False),
             round(booked, 6), cumulative,
             json.dumps(bd, ensure_ascii=False), source))

        if update_totals:
            from .reduction_ledger import refresh_totals
            refresh_totals(conn, proj_id)
        conn.commit()

        m = conn.execute("SELECT name FROM project_machines WHERE id = ?",
                         (machine_id,)).fetchone()
        _raise_gap_alert(conn, proj_id, machine_id, (m["name"] if m else ""),
                         gap_h, prev_ts, timestamp,
                         message=("机器「%s」断档 %s → %s（%.1f 小时）。%s"
                                  % ((m["name"] if m else machine_id), prev_ts, timestamp,
                                     gap_h, decision.get("reason") or "")))
        log.info("[采集器] 机器 #%s 断档兜底：补记 %.4f tCO₂e（%s）",
                 machine_id, booked, decision.get("reason"),
                 extra={"project_id": proj_id})
        conn.close()
        if has_later and update_totals:
            from .reduction_ledger import reconcile_machine
            reconcile_machine(machine_id, apply=True)
        return {"carbon_reduction": round(booked, 6),
                # 必须和上面 INSERT 的 cumulative 一致（= 断档前累计 + 补记），
                # 不能写 round(booked,6)：那样调用方拿到的累计比库里的小，
                # 而且这条返回值会喂给下一条读数当 prev，越滚越错。
                "cumulative_reduction": cumulative,
                "breakdown": bd, "is_bridge": True}

    @classmethod
    def collect_and_store(cls, machine_id: int, raw_data: dict,
                          source: str = "auto", timestamp: str = None,
                          prev_before: bool = False, gap_check: bool = True,
                          update_totals: bool = True) -> dict:
        """
        采集一台机器的一条数据，计算减排量，存储到数据库。

        参数:
            machine_id: 机器 ID
            raw_data: 原始数据
            source: 数据来源 ("auto" / "manual")
            timestamp: 时间戳（None=当前时间）
            prev_before: True = 「上期」取**时间戳早于本条**的最近一条
                         （批量回溯倒序补历史用：不找全局最新，避免新数据
                         被当成旧记录的"上期"方向做反）；
                         False（默认）= 全局最新一条，实时采集语义
            gap_check: False = 跳过断档重置/兜底分支（批量回溯用：
                       回溯序列内部相邻记录的差值就是期间排放量，
                       月度记录间隔远超阈值，开了会把每期都当断档清零）
            update_totals: False = 不重算项目累计/不跑告警（批量回溯逐条入账用，
                       最后统一重算一次，避免每条都全表扫导致 O(n²)）

        返回:
            dict: 计算结果
        """
        import config as cfg

        mach = get_machine(machine_id)
        if not mach:
            raise Exception(f"机器 {machine_id} 不存在")
        proj_id = mach["project_id"]
        if mach.get("lifecycle_status") != "active":
            raise ValueError("机器已作废，停止采集")
        proj = get_project(proj_id)
        if not proj:
            raise Exception(f"机器 {machine_id} 所属项目 {proj_id} 不存在")

        # ── 输入校验：raw_data 必须是 dict ──
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except json.JSONDecodeError:
                raise Exception(
                    f"raw_data 不是有效的 JSON 字符串: {str(raw_data)[:200]}"
                )
        if not isinstance(raw_data, dict):
            raise Exception(
                f"raw_data 类型错误，期望 dict，实际: {type(raw_data).__name__}"
            )
        if not raw_data:
            raise Exception("raw_data 不能为空")

        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        from .data_import import normalize_timestamp
        normalized_timestamp = normalize_timestamp(timestamp)
        if normalized_timestamp is None:
            raise ValueError("监测时间无效，请使用真实日期 YYYY-MM-DD HH:MM:SS")
        timestamp = normalized_timestamp
        import math
        formula_cls = CarbonCalculator._formulas_registry.get(proj.get("hook_name"))
        for field in getattr(formula_cls, "required_raw_fields", ()):
            if field in raw_data:
                try:
                    finite = math.isfinite(float(raw_data[field]))
                except (TypeError, ValueError):
                    finite = False
                if not finite:
                    raise ValueError(f"{field} 必须为有限数字")

        # 获取上期累计数据 —— **按机器查**：
        # 累计流量/电量是设备自己的读数，跨机器取上一条会让增量算成天文数字。
        #
        # prev_before=True（批量回溯）：只认**早于本条**的最近一条 —— 回溯是从
        # 基准日期往现在补，若库里已有更新的实时数据，"全局最新"比本条还新，
        # 方向做反会把增量算成负数；严格小于本条时间戳才是正确的前驱。
        conn = get_db()
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT p.lifecycle_status AS project_status,m.lifecycle_status AS machine_status "
            "FROM carbon_projects p JOIN project_machines m ON m.project_id=p.id "
            "WHERE p.id=? AND m.id=?", (proj_id, machine_id)).fetchone()
        if not active or active["project_status"] != "active" or active["machine_status"] != "active":
            conn.rollback()
            conn.close()
            raise ValueError("项目已作废，停止写入监测数据")
        prev_row = conn.execute(
            """SELECT timestamp, carbon_reduction, cumulative_reduction, raw_data
               FROM monitoring_data WHERE machine_id=? AND timestamp<?
               ORDER BY timestamp DESC,id DESC LIMIT 1""",
            (machine_id, timestamp)).fetchone()

        # ── 该机器的**第一条**读数：只作基线，不计减排量 ──
        #
        # 累计读数（累计流量/电量）本身不含"期间"信息：一次读数无法定义一段期间。
        # 拿它减 0 等于把仪表有史以来的全部产量都记到本期 —— 若项目建在装表之前
        # （仪表早就走到 500 万 m³），第一条就凭空多算一大笔，而且**永久留在累计里**
        # （后续每条都对，没人会看出来）。首条的正确身份是"起点"，不是"本期"。
        #
        # 代价最多是一个采样间隔的量（设备基本从 0 起），可以忽略；
        # 换来的是不会有一次性虚增。基线行**必须落库** —— 它是下一条的 prev，
        # 不存的话第二条又变成"第一条"，永远算不出增量。
        #
        # 这只是每台机器一辈子一次的事件：prev_row 是**从库里**查的，
        # 服务重启不会重新基线化。
        if prev_row is None:
            return cls._store_baseline_row(conn, machine_id, proj_id, raw_data,
                                           source, timestamp, update_totals=update_totals)

        # ── 断档保护：间隔太长时，这条只当**新起点**，不跟上一条做差 ──
        # 累计读数做差只能表达"一个正常采集间隔"。中间断了很久（换核查周期、
        # 系统停机、设备离线数周）时，那条差值等于"断档期间的全部产量"，
        # 而这段时间很可能已经在别处核查过了 —— 直接算进来就是重复计算，
        # 而且会永久留在累计里（后续每条都对，没人看得出来）。
        # 触发时写告警+日志，不是静默丢弃：真要计入就按提示导入那一段。
        gap_h = _gap_hours(prev_row["timestamp"], timestamp)
        # _gap_reset_hours() 的约定是「0 或负数 = 关闭该保护」（见它的 docstring 与
        # config.COLLECT_GAP_RESET_HOURS 的注释）。但判断式 originally 只写了
        # `gap_h > 阈值`，配成 0 之后**任何正间隔**（正常 60 秒一条也算）都会落进断档分支，
        # 等于把正常做差整个关掉而不是关掉保护。所以这里必须先挡一道。
        _gap_limit = _gap_reset_hours()
        # gap_check=False 是批量回溯场景：回溯序列内部相邻记录的差值就是
        # 期间排放量，月度记录间隔远超断档阈值，走断档分支会把每期清零。
        if gap_check and _gap_limit > 0 and gap_h is not None and gap_h > _gap_limit:
            # 断档了。默认**自动兜底**：看这段空档有没有覆盖到已核查的期间，
            # 有就把已核查量扣掉、只补记未核查的部分（core/gap_bridge.py）。
            # 取不到核查记录时会自动退回"只作新起点"，绝不会重复计算。
            decision = None
            if _auto_bridge_enabled():
                try:
                    from .gap_bridge import bridge_decision
                    decision = bridge_decision(
                        proj, mach, prev_row, raw_data, timestamp,
                        _collector_formula(mach, proj, cfg.VCER_API_URL),
                        _raw_fields(proj))
                except Exception as e:
                    log.warning("[采集器] 断档兜底计算失败，退回新起点: %s", e)
                    decision = None

            if decision and decision.get("mode") == "bridge":
                return cls._store_bridge_row(conn, machine_id, proj_id, raw_data,
                                             source, timestamp, decision,
                                             prev_row["timestamp"], gap_h,
                                             prev_row["cumulative_reduction"],
                                             update_totals=update_totals)

            return cls._store_baseline_row(
                conn, machine_id, proj_id, raw_data, source, timestamp,
                reason=((decision or {}).get("reason")
                        or (f"距上一条已 {gap_h:.1f} 小时（超过 "
                            f"{_gap_reset_hours()} 小时阈值），本条作为新起点，"
                            f"断档期间的产量未计入；若这段需要计入，请导入该段数据")),
                prev_ts=prev_row["timestamp"], update_totals=update_totals)

        prev_flow = 0
        prev_power = 0
        prev_cumulative = 0
        prev_cumulative = prev_row["cumulative_reduction"] or 0
        try:
            prev_raw = json.loads(prev_row["raw_data"]) if prev_row["raw_data"] else {}
            prev_flow = float(prev_raw.get("cumulative_flow", 0))
            prev_power = float(prev_raw.get("cumulative_power", 0))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        cumulative_data = {
            "prev_flow": prev_flow,
            "prev_power": prev_power,
            "cumulative_reduction": prev_cumulative,
        }

        # 确保该机器的公式已初始化（公式名取自项目，参数取自机器）
        if machine_id not in CarbonCalculator._instances:
            CarbonCalculator.init_machine_formula(mach, proj.get("hook_name", ""),
                                                  cfg.VCER_API_URL)

        # 执行计算
        result = CarbonCalculator.compute(machine_id, raw_data, cumulative_data)

        # 存储到数据库
        conn.execute(
            """INSERT INTO monitoring_data
               (project_id, machine_id, timestamp, raw_data, carbon_reduction,
                cumulative_reduction, breakdown, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                proj_id,
                machine_id,
                timestamp,
                json.dumps(raw_data, ensure_ascii=False),
                result["carbon_reduction"],
                result["cumulative_reduction"],
                json.dumps(result.get("breakdown", {}), ensure_ascii=False),
                source,
            )
        )

        # 更新项目全历史累计减排量 —— 是该项目所有机器累计值之和，
        # 不能拿刚采的这台直接覆盖，否则多机器项目只会剩下最后一台的量。
        # update_totals=False（批量回溯逐条入账）时跳过，最后统一重算一次。
        if update_totals:
            from .reduction_ledger import refresh_totals
            refresh_totals(conn, proj_id)
        late = conn.execute("SELECT 1 FROM monitoring_data WHERE machine_id=? "
                            "AND timestamp>? LIMIT 1", (machine_id, timestamp)).fetchone()
        conn.commit()
        conn.close()
        if late and update_totals:
            from .reduction_ledger import reconcile_machine
            reconcile_machine(machine_id, apply=True)

        # 检查告警（告警规则是项目级的，按项目评估）；批量回溯时最后统一处理
        if update_totals:
            from .alert_engine import AlertEngine
            AlertEngine.check(proj_id, result)

        return result
