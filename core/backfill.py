"""
历史数据批量回溯 — 对「API」数据源机器（含 mqtt 机器配了 API 回溯的）从基准日期到当前全量补拉历史。

触发：项目管理页「⏳ 回溯历史」按钮（单个项目）或「批量回溯」（全部项目）。
任务在后台线程执行，前端轮询 /api/backfill/status/<job_id> 看进度和结果。

哪些机器可回溯：
- data_source_type == 'api'  → 用机器自己的 data_source_config 拉历史；
- backfill_method == 'api'（如 mqtt 机器）→ 用 backfill_config（站点级）拉历史，
  格式映射复用机器 data_source_config.normalizer（单一来源在 normalizer 文件）。

批量策略（机器/项目多时的效率）：
- 同一套企业配置（单企业部署）下的可回溯机器合并走**平台批量接口**
  （前置转换文件的 build_batch_request / parse_batch_response，如鑫钻 POST /data/batch），
  一次请求拉多台，按 dataId 分发逐台记账；
- 平台不支持批量接口时回退到逐台单查（fetch_range）。

幂等设计（回答"再点一次是覆盖还是提示"）：
- **不覆盖**：按「机器 + 来源 + 时间戳」去重，已入账的记录直接跳过；
- 重复执行只会补上次没拉到的，全部已存在时结果就是「导入 0 条，跳过 N 条」；
- 历史 raw_data 永不改写，保证核查可追溯。

基准日期优先级：请求参数 > 机器配置 backfill_since > 项目 baseline_date（计入期开始日）。
"""

import json
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from .project_manager import list_all_machines

log_lock = threading.Lock()
_jobs = {}  # {job_id: {"status": "running"/"done", "started_at": ..., "finished_at": ..., "results": [...]}}


# ── 回溯任务持久化（backfill_jobs 表）—— 重启后仍能查历史任务 ──

def _ensure_backfill_jobs_table():
    from .project_manager import get_db
    try:
        conn = get_db()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backfill_jobs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id        TEXT    NOT NULL,
                project_id    INTEGER,                 -- NULL = 全局批量回溯
                baseline_date TEXT,
                granularity   TEXT,
                status        TEXT    DEFAULT 'running',  -- running / done
                started_at    TEXT,
                finished_at   TEXT,
                total         INTEGER DEFAULT 0,
                imported      INTEGER DEFAULT 0,
                skipped       INTEGER DEFAULT 0,
                failed        INTEGER DEFAULT 0,
                progress      TEXT,                     -- JSON
                results       TEXT,                     -- JSON
                created_at    TEXT    DEFAULT (datetime('now','localtime'))
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_backfill_jobs_project "
                     "ON backfill_jobs(project_id, id)")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[backfill] 建 backfill_jobs 表失败: {e}")


def _db_insert_job(job_id, project_id, baseline_date, granularity, total):
    from .project_manager import get_db
    try:
        conn = get_db()
        conn.execute(
            """INSERT INTO backfill_jobs
               (job_id, project_id, baseline_date, granularity, status, started_at, total)
               VALUES (?, ?, ?, ?, 'running', datetime('now','localtime'), ?)""",
            (job_id, project_id, baseline_date, granularity, total))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[backfill] 登记回溯任务失败: {e}")


def _db_update_job(job_id, **fields):
    from .project_manager import get_db
    if not fields:
        return
    try:
        conn = get_db()
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE backfill_jobs SET {sets} WHERE job_id = ?",
                     list(fields.values()) + [job_id])
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[backfill] 更新回溯任务 {job_id} 失败: {e}")


def _backfill_api_config(mach: dict) -> dict | None:
    """返回这台机器用于 API 回溯的配置（site_id/粒度 + normalizer），不可回溯返回 None。"""
    def _load(v):
        try:
            return json.loads(v or "{}")
        except json.JSONDecodeError:
            return {}

    ds = _load(mach.get("data_source_config"))
    normalizer = _load(mach.get("normalizer_config"))
    if normalizer.get("hook"):
        ds["normalizer"] = normalizer
    if mach.get("data_source_type") == "api":
        return ds
    if mach.get("backfill_method") == "api":
        bf = _load(mach.get("backfill_config"))
        if ds.get("normalizer"):
            bf["normalizer"] = ds["normalizer"]
        return bf
    return None


def _new_summary(mach: dict) -> dict:
    return {"machine_id": mach["id"], "machine_name": mach.get("name", ""),
            "project_id": mach["project_id"],
            "project_name": mach.get("project_name", ""),
            "imported": 0, "skipped": 0, "failed": 0, "msg": ""}


def _supports_batch(hook) -> bool:
    """平台是否实现了批量接口（覆盖了 build_batch_request）。"""
    from normalizers.base_normalizer import BaseDataNormalizer
    return type(hook).build_batch_request is not BaseDataNormalizer.build_batch_request


def start_backfill(project_id: int = None, baseline_date: str = None,
                   granularity: str = None) -> str:
    """
    启动一个回溯任务（后台线程，立即返回 job_id）。

    参数:
        project_id: 只回溯这个项目；None = 所有可回溯机器
        baseline_date: 基准日期 "YYYY-MM-DD[ HH:MM:SS]"，None = 用项目 baseline_date
        granularity: "hour"/"day"/"month"/"normal"，None = 用机器配置 record_time_type
    """
    machines = [m for m in list_all_machines()
                if m.get("data_source_type") == "api" or m.get("backfill_method") == "api"]
    if project_id is not None:
        machines = [m for m in machines if m["project_id"] == project_id]
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "status": "running",
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": None,
        "total": len(machines),
        "results": [],
        "project_id": project_id,
    }
    # 持久化登记，供历史任务列表查询
    _ensure_backfill_jobs_table()
    _db_insert_job(job_id, project_id, baseline_date, granularity, len(machines))
    t = threading.Thread(target=_run_job,
                         args=(job_id, machines, baseline_date, granularity),
                         daemon=True)
    t.start()
    return job_id


def get_job(job_id: str) -> dict:
    return _jobs.get(job_id)


def list_jobs(project_id=None, limit=50):
    """列出回溯任务（历史 + 运行中），可按项目过滤。返回倒序（最新在前）。"""
    from .project_manager import get_db
    jobs = []
    try:
        conn = get_db()
        if project_id is not None:
            rows = conn.execute(
                "SELECT * FROM backfill_jobs WHERE project_id = ? ORDER BY id DESC LIMIT ?",
                (project_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM backfill_jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        for r in rows:
            d = dict(r)
            if d["status"] == "running" and d["job_id"] not in _jobs:
                d["status"] = "interrupted"
                _db_update_job(d["job_id"], status="interrupted")
            try:
                d["progress"] = json.loads(d["progress"]) if d["progress"] else None
            except Exception:
                d["progress"] = None
            d.pop("results", None)  # 结果明细太大，列表不返回；看详情走 status 接口
            jobs.append(d)
    except Exception as e:
        print(f"[backfill] 查回溯任务列表失败: {e}")

    # 用内存运行中任务的实时状态覆盖（内存最新，数据库可能滞后一块）
    for jid, j in _jobs.items():
        if project_id is not None and j.get("project_id") != project_id:
            continue
        hit = next((x for x in jobs if x.get("job_id") == jid), None)
        if hit:
            hit["status"] = j.get("status", hit["status"])
            hit["progress"] = j.get("progress", hit.get("progress"))
            hit["finished_at"] = j.get("finished_at", hit.get("finished_at"))
        else:
            jobs.insert(0, {
                "job_id": jid, "project_id": j.get("project_id"),
                "baseline_date": j.get("baseline_date"),
                "granularity": j.get("granularity"),
                "status": j.get("status"), "started_at": j.get("started_at"),
                "finished_at": j.get("finished_at"), "total": j.get("total", 0),
                "progress": j.get("progress"),
                "imported": j.get("imported", 0), "skipped": j.get("skipped", 0),
                "failed": j.get("failed", 0),
            })
    return jobs


def _run_job(job_id: str, machines: list, baseline_date: str, granularity: str):
    from .data_collector import DataCollector

    # 回溯期间暂停这些机器的采集：否则采集器会在回溯写入的时间范围内触发
    # 断档兜底、写 gap_reset 记录把该段减排量清零。
    DataCollector._backfilling.update(m["id"] for m in machines)
    try:
        results = _backfill_machines(machines, baseline_date, granularity, DataCollector,
                                     job_id=job_id)
    except Exception as exc:
        results = [{**_new_summary(m), "failed": 1, "msg": f"回溯中断: {exc}"}
                   for m in machines]
    finally:
        DataCollector._backfilling.difference_update(m["id"] for m in machines)

    with log_lock:
        _jobs[job_id]["results"].extend(results)
        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 持久化最终结果
    tot = [0, 0, 0]
    for r in results:
        tot[0] += r.get("imported", 0)
        tot[1] += r.get("skipped", 0)
        tot[2] += r.get("failed", 0)
    _db_update_job(job_id,
                   status="done",
                   finished_at=_jobs[job_id]["finished_at"],
                   imported=tot[0], skipped=tot[1], failed=tot[2],
                   results=json.dumps(results, ensure_ascii=False))


def _refresh_totals(project_ids):
    """Recalculate lifetime and pending figures after completed history imports."""
    from .project_manager import get_db
    from .reduction_ledger import refresh_totals, reconcile_machine
    for pid in set(project_ids):
        conn = get_db()
        try:
            machines = [r[0] for r in conn.execute(
                "SELECT id FROM project_machines WHERE project_id=?", (pid,)).fetchall()]
        finally:
            conn.close()
        for machine_id in machines:
            reconcile_machine(machine_id, apply=True)
        conn = get_db()
        try:
            refresh_totals(conn, pid)
            conn.commit()
        finally:
            conn.close()


def _time_chunks(begin: datetime, end: datetime, days: int):
    """按平台可接受的窗口切分，下一块从上一块终点后一秒开始。"""
    if days < 1:
        raise ValueError("chunk_days 必须大于零")
    current = begin
    span = timedelta(days=days)
    while current <= end:
        chunk_end = min(current + span, end)
        yield current, chunk_end
        current = chunk_end + timedelta(seconds=1)


def _parallel_fetch_ordered(chunks, fetch, workers=2, *,
                            retry_delay=300, max_attempts=3, on_retry=None):
    """Fetch up to workers windows; retry a failed window before advancing it.

    A failed request never advances the saved window. Other submitted windows may
    finish while it cools down, but ingestion still happens in time order.
    """
    workers = min(max(int(workers), 1), 8)
    pending = deque()

    def fetch_with_retry(bounds):
        for attempt in range(1, max_attempts + 1):
            try:
                return fetch(bounds)
            except Exception as exc:
                if attempt >= max_attempts:
                    raise
                if on_retry:
                    on_retry(bounds, attempt, exc, retry_delay)
                time.sleep(max(0, retry_delay))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for chunk in chunks:
            pending.append((chunk, pool.submit(fetch_with_retry, chunk)))
            if len(pending) >= workers:
                item, future = pending.popleft()
                try:
                    yield item, future.result(), None
                except Exception as exc:
                    yield item, None, exc
        while pending:
            item, future = pending.popleft()
            try:
                yield item, future.result(), None
            except Exception as exc:
                yield item, None, exc


def _retry_progress(job_id, bounds, attempt, exc, delay, machine_name=""):
    if not job_id or job_id not in _jobs:
        return
    retry_at = (datetime.now() + timedelta(seconds=delay)).strftime("%Y-%m-%d %H:%M:%S")
    old = _jobs[job_id].get("progress") or {}
    progress = {**old, "retrying": True, "retry_at": retry_at,
                "retry_attempt": attempt + 1, "retry_max": 3,
                "current": bounds[0].strftime("%Y-%m-%d"),
                "machine_name": machine_name,
                "retry_error": str(exc)[:300]}
    _jobs[job_id]["progress"] = progress
    _db_update_job(job_id, progress=json.dumps(progress, ensure_ascii=False))


def _backfill_machines(machines: list, baseline_date: str, granularity: str, collector,
                       job_id: str = None) -> list:
    """回溯一批机器：优先走批量接口，不支持则逐台单查。返回逐台摘要列表。"""
    if not machines:
        return []
    from adapters.api_adapter import ApiAdapter

    summaries = {m["id"]: _new_summary(m) for m in machines}

    # 用第一台机器构造 adapter 拿 hook/aliases/凭据
    config = _backfill_api_config(machines[0])
    if config is None:
        for m in machines:
            summaries[m["id"]]["msg"] = "该机器未配置 API 回溯"
        return [summaries[m["id"]] for m in machines]
    adapter = ApiAdapter(config)
    if not adapter.connect():
        for m in machines:
            summaries[m["id"]]["msg"] = f"配置校验失败: {adapter._last_error}"
        return [summaries[m["id"]] for m in machines]
    hook = adapter._normalizer.hook

    # 平台不支持批量 → 逐台单查
    def _batch_signature(machine):
        machine_config = dict(_backfill_api_config(machine) or {})
        machine_config.pop("site_id", None)
        machine_config.pop("request", None)
        machine_config.pop("headers", None)
        machine_config.pop("aliases", None)
        return machine_config

    if (not _supports_batch(hook) or any(
            _batch_signature(m) != _batch_signature(machines[0])
            for m in machines)):
        # 每台机器请求不同就逐机执行，避免第一台机器的配置覆盖其他机器。
        return [_backfill_one(m, baseline_date, granularity, collector, job_id=job_id)
                for m in machines]

    # 统一时间窗：弹窗基准日期 → 否则组内最早 since
    begin = _earliest_begin(machines, baseline_date)
    end = datetime.now()
    if begin is None:
        for m in machines:
            summaries[m["id"]]["msg"] = "未指定基准日期（弹窗填 / 机器 backfill_since / 项目基准日期）"
        return [summaries[m["id"]] for m in machines]
    if begin >= end:
        for m in machines:
            summaries[m["id"]]["msg"] = "基准日期晚于当前时间"
        return [summaries[m["id"]] for m in machines]

    if granularity in ("hour", "day", "month", "normal"):
        config["record_time_type"] = granularity

    chunk_days = max(1, int(config.get("chunk_days") or getattr(hook, "backfill_chunk_days", 0) or 28))
    workers = int(config.get("backfill_workers") or 2)
    fill = bool(int(config.get("fill", 0)))

    # site_id → machine 映射
    data_id_map = {}
    for m in machines:
        cfg = _backfill_api_config(m) or {}
        sid = str(cfg.get("site_id") or "").strip()
        if not sid:
            summaries[m["id"]]["msg"] = "回溯配置缺少 site_id"
            continue
        data_id_map[sid] = m

    if not data_id_map:
        return [summaries[m["id"]] for m in machines]

    data_ids = list(data_id_map.keys())

    total_days = max(1, (end - begin).days)
    def fetch_chunk(bounds):
        local_adapter = ApiAdapter(config)
        results, errors = local_adapter.fetch_batch(data_ids, *bounds)
        by_id = {str(r.get("dataId")): r for r in (results or [])}
        pending_errors = list(errors or [])
        for attempt in (1, 2):
            retry_ids = [str(e.get("dataId")) for e in pending_errors if e.get("dataId") is not None]
            if not retry_ids:
                break
            _retry_progress(job_id, bounds, attempt,
                            RuntimeError("部分气站请求失败"), 300, "批量设备")
            time.sleep(300)
            retry_results, pending_errors = local_adapter.fetch_batch(retry_ids, *bounds)
            for result in retry_results or []:
                by_id[str(result.get("dataId"))] = result
            pending_errors = list(pending_errors or [])
        return list(by_id.values()), pending_errors

    for (cur, chunk_end), response, error in _parallel_fetch_ordered(
            _time_chunks(begin, end, chunk_days), fetch_chunk, workers,
            on_retry=lambda bounds, attempt, exc, delay: _retry_progress(
                job_id, bounds, attempt, exc, delay, "批量设备")):
        if error:
            for m in machines:
                summaries[m["id"]]["failed"] += 1
                summaries[m["id"]]["msg"] = f"批量拉取失败: {error}"
            continue
        results, errors = response
        # 成功结果按 dataId 分发
        for res in (results or []):
            m = data_id_map.get(str(res.get("dataId")))
            if not m:
                continue
            _ingest_series(m, hook, adapter._aliases, res, fill, collector, summaries[m["id"]])
        # 失败的 dataId
        for err in (errors or []):
            m = data_id_map.get(str(err.get("dataId")))
            if m:
                summaries[m["id"]]["failed"] += 1
                summaries[m["id"]]["msg"] = err.get("message", "批量查询失败")
        # 实时进度（前端轮询能看到，而不是干等 results 全部完成）
        if job_id and job_id in _jobs:
            done_days = (chunk_end - begin).days
            imported = sum(s["imported"] for s in summaries.values())
            _jobs[job_id]["progress"] = {
                "done_days": done_days, "total_days": total_days,
                "current": chunk_end.strftime("%Y-%m-%d"),
                "imported": imported,
            }
            # 同步进度到数据库（历史任务列表也能看到进行中任务的阶段）
            _db_update_job(job_id, imported=imported,
                           progress=json.dumps(_jobs[job_id]["progress"], ensure_ascii=False))

    # 逐条入账时跳过了项目累计重算，这里统一重算一次
    _refresh_totals({m["project_id"] for m in machines})
    return [summaries[m["id"]] for m in machines]


def _batch_store(mach, rows, source="auto"):
    """回溯批量记账：一个连接 + 一个事务 + 复用公式实例，按时间升序批量入账一批记录。

    对齐 collect_and_store(prev_before=True, gap_check=False, update_totals=False) 的语义：
    - 首条作基线（carbon_reduction=0, cumulative=0）
    - 非首条：prev 取时间戳更早的最近一条，公式算增量、累计接着加
    避免逐条 collect_and_store 的「每次开连接 + 读机器/项目 + 查公式实例 + 每条 commit」开销。

    返回 (imported, skipped, failed, first_err)。
    """
    import json
    from .project_manager import get_db, get_project
    from .carbon_calculator import CarbonCalculator
    import config as cfg

    machine_id = mach["id"]
    proj_id = mach["project_id"]
    proj = get_project(proj_id)
    conn = get_db()
    conn.execute("BEGIN IMMEDIATE")
    state = conn.execute(
        "SELECT p.lifecycle_status AS project_status,m.lifecycle_status AS machine_status "
        "FROM carbon_projects p JOIN project_machines m ON m.project_id=p.id "
        "WHERE p.id=? AND m.id=?", (proj_id, machine_id)).fetchone()
    if not state or state["project_status"] != "active" or state["machine_status"] != "active":
        conn.rollback()
        conn.close()
        raise ValueError("项目已作废，回溯停止写入")
    imported = skipped = failed = 0
    first_err = None
    try:
        if machine_id not in CarbonCalculator._instances:
            CarbonCalculator.init_machine_formula(
                mach, (proj or {}).get("hook_name", ""), cfg.VCER_API_URL)

        for row in rows:
            ts = row.get("timestamp")
            if not ts:
                continue
            # 幂等去重（同 machine+source+timestamp 已存在则跳过）
            if conn.execute(
                "SELECT 1 FROM monitoring_data WHERE machine_id=? AND source=? AND timestamp=? LIMIT 1",
                (machine_id, source, ts)).fetchone():
                skipped += 1
                continue
            try:
                prev_row = conn.execute(
                    "SELECT timestamp, cumulative_reduction, raw_data FROM monitoring_data "
                    "WHERE machine_id=? AND source=? AND timestamp<? "
                    "ORDER BY timestamp DESC, id DESC LIMIT 1",
                    (machine_id, source, ts)).fetchone()
                if prev_row is None:
                    # 首条作基线，不计减排
                    bd = {"baseline": True, "note": "首条读数作为基线，不计减排量"}
                    conn.execute(
                        "INSERT INTO monitoring_data (project_id,machine_id,timestamp,raw_data,"
                        "carbon_reduction,cumulative_reduction,breakdown,source) "
                        "VALUES (?,?,?,?,0,0,?,?)",
                        (proj_id, machine_id, ts, json.dumps(row, ensure_ascii=False),
                         json.dumps(bd, ensure_ascii=False), source))
                else:
                    prev_cumulative = prev_row["cumulative_reduction"] or 0
                    prev_flow = prev_power = 0.0
                    try:
                        prev_raw = json.loads(prev_row["raw_data"]) if prev_row["raw_data"] else {}
                        prev_flow = float(prev_raw.get("cumulative_flow", 0))
                        prev_power = float(prev_raw.get("cumulative_power", 0))
                    except (json.JSONDecodeError, ValueError, TypeError):
                        pass
                    cumulative_data = {"prev_flow": prev_flow, "prev_power": prev_power,
                                       "cumulative_reduction": prev_cumulative}
                    result = CarbonCalculator.compute(machine_id, row, cumulative_data)
                    conn.execute(
                        "INSERT INTO monitoring_data (project_id,machine_id,timestamp,raw_data,"
                        "carbon_reduction,cumulative_reduction,breakdown,source) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (proj_id, machine_id, ts, json.dumps(row, ensure_ascii=False),
                         result["carbon_reduction"], result["cumulative_reduction"],
                         json.dumps(result.get("breakdown", {}), ensure_ascii=False), source))
                imported += 1
            except Exception as e:
                failed += 1
                if first_err is None:
                    first_err = f"{ts}: {e}"
        conn.commit()
    finally:
        conn.close()
    return imported, skipped, failed, first_err


def _ingest_series(mach, hook, aliases, result, fill, collector, summary):
    """把一次查询结果（含 series）批量入账到某台机器。"""
    from adapters.api_adapter import merge_history_points
    per_field = {}
    time_types = set()
    for field, alias in (aliases or {}).items():
        pts = []
        for rec in hook.parse_history_response(alias, result):
            if rec.get("recordTimeType"):
                time_types.add(str(rec["recordTimeType"]))
            convert = getattr(hook, "convert_api_with_apart", hook.convert_api)
            mapped = convert(alias, rec)
            if mapped is None:
                continue
            _f, v, ts = mapped[:3]
            try:
                pts.append((ts, float(v), mapped[3] if len(mapped) > 3 else None))
            except (TypeError, ValueError):
                continue
        per_field[field] = pts
    rows = merge_history_points(per_field, fill=fill)
    if len(time_types) == 1:
        record_type = next(iter(time_types))
        for row in rows:
            row["record_time_type"] = record_type
    imported, skipped, failed, first_err = _batch_store(mach, rows)
    summary["imported"] += imported
    summary["skipped"] += skipped
    summary["failed"] += failed
    if summary["imported"] == 0 and summary["skipped"] > 0:
        summary["msg"] = "历史数据已存在，无需回溯"
    elif first_err:
        summary["msg"] = f"部分记录入账失败: {first_err}"
    elif summary["imported"] > 0:
        summary["msg"] = f"导入 {summary['imported']} 条"


def _backfill_one(mach: dict, baseline_date: str, granularity: str, collector,
                  job_id: str = None) -> dict:
    """回溯一台机器（单机路径，批量接口不可用时的兜底）。"""
    from adapters.api_adapter import ApiAdapter
    base = _new_summary(mach)
    try:
        config = _backfill_api_config(mach)
        if config is None:
            base["msg"] = "该机器未配置 API 回溯"
            return base
        if not config.get("site_id"):
            base["msg"] = "回溯配置缺少 site_id（站点 ID）"
            return base
        begin = _earliest_begin([mach], baseline_date)
        end = datetime.now()
        if begin is None:
            base["msg"] = "未指定基准日期"
            return base
        if begin >= end:
            base["msg"] = "基准日期晚于当前时间"
            return base
        if granularity in ("hour", "day", "month", "normal"):
            config["record_time_type"] = granularity
        adapter = ApiAdapter(config)
        if not adapter.connect():
            base["msg"] = f"配置校验失败: {adapter._last_error}"
            return base
        chunk_days = max(1, int(config.get("chunk_days") or getattr(
            adapter._normalizer.hook, "backfill_chunk_days", 0) or 28))
        workers = int(config.get("backfill_workers") or 2)
        first_ts = last_ts = None
        first_err = None

        def fetch_chunk(bounds):
            # 自动采集可限制单次记录数；回溯按块全部保留，避免只入账末尾 500 条。
            chunk_config = dict(config, max_records_per_run=10**9)
            local_adapter = ApiAdapter(chunk_config)
            rows = local_adapter.fetch_range(*bounds)
            if local_adapter._last_error:
                raise RuntimeError(local_adapter._last_error)
            return rows

        total_days = max(1, (end - begin).days)
        for _bounds, rows, error in _parallel_fetch_ordered(
                _time_chunks(begin, end, chunk_days), fetch_chunk, workers,
                on_retry=lambda bounds, attempt, exc, delay: _retry_progress(
                    job_id, bounds, attempt, exc, delay, mach.get("name", ""))):
            if job_id and job_id in _jobs:
                progress = {"done_days": (_bounds[1] - begin).days,
                            "total_days": total_days,
                            "current": _bounds[1].strftime("%Y-%m-%d"),
                            "machine_name": mach.get("name", ""),
                            "imported": base["imported"], "retrying": False}
                _jobs[job_id]["progress"] = progress
                _db_update_job(job_id, progress=json.dumps(progress, ensure_ascii=False),
                               imported=base["imported"], failed=base["failed"])
            if error:
                base["failed"] += 1
                if first_err is None:
                    first_err = str(error)
                continue
            if not rows:
                continue
            if first_ts is None:
                first_ts = rows[0]["timestamp"]
            last_ts = rows[-1]["timestamp"]
            imported, skipped, failed, store_err = _batch_store(mach, rows)
            base["imported"] += imported
            base["skipped"] += skipped
            base["failed"] += failed
            if store_err and first_err is None:
                first_err = store_err
        if base["imported"] == 0 and base["skipped"] > 0:
            base["msg"] = "历史数据已存在，无需回溯"
        elif base["imported"]:
            base["msg"] = f"{first_ts} ~ {last_ts}，导入 {base['imported']} 条"
        else:
            base["msg"] = "基准日期至今没有历史数据"
        if first_err:
            base["msg"] += f"；部分失败: {first_err}"
        if base["imported"]:
            _refresh_totals([mach["project_id"]])
        return base
    except Exception as e:
        base["failed"] += 1
        base["msg"] = f"回溯异常: {e}"
        return base


def _earliest_begin(machines: list, baseline_date: str):
    """组内最早基准日期（弹窗指定优先，否则各机器 backfill_since / 项目 baseline_date 最早）。"""
    earliest = None
    for m in machines:
        cfg = _backfill_api_config(m) or {}
        since = (baseline_date or "").strip() or cfg.get("backfill_since") \
                or m.get("baseline_date") or ""
        since = since.strip()
        if not since:
            from .project_manager import get_project
            proj = get_project(m["project_id"]) or {}
            since = (proj.get("baseline_date") or "").strip()
        if not since:
            continue
        dt = _parse_dt(since)
        if dt and (earliest is None or dt < earliest):
            earliest = dt
    return earliest


def _parse_dt(s: str):
    """'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM[:SS]' → datetime；失败 None。"""
    s = str(s).strip().replace("T", " ").replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None
