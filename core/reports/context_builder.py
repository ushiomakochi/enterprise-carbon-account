"""
报告数据上下文构建器。

负责从数据库、公式引擎、外部 API 等数据源收集所有可能被报告模板
使用的数据，组装成一个"大而全"的 context dict。

设计原则:
- 构建完整上下文（fat context），具体用哪些由模板通过 Jinja2 决定
- 模板中通过 {% if pdd %} 等条件块控制特定报告类型的内容显隐
- 新增报告类型只需添加对应的数据收集方法和模板文件
"""

import os
import json
import re
from datetime import datetime


def _collect_interval_seconds():
    """采集间隔（秒），取自 config.COLLECTION_INTERVAL_SECONDS。"""
    try:
        import config
        return int(config.COLLECTION_INTERVAL_SECONDS)
    except Exception:
        return 60


def _collect_interval_text():
    """采集间隔的人话描述，供模板直接写进「监测频次与记录要求」。

    秒级原样显示，能整除成分钟的换成分钟（3600 → 60 分钟），
    免得模板里出现「采集间隔 3600 秒」这种别扭说法。
    """
    sec = _collect_interval_seconds()
    if sec <= 0:
        return "连续"
    if sec % 3600 == 0:
        return f"{sec // 3600} 小时"
    if sec % 60 == 0 and sec >= 60:
        return f"{sec // 60} 分钟"
    return f"{sec} 秒"


def _fmt_date_cn(s):
    """把 YYYY-MM-DD 转成「YYYY年MM月DD日」；其它格式（含 '待定'）原样返回。"""
    if not s:
        return ""
    s = str(s).strip()
    m = re.fullmatch(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        # 月日不补零：2022年5月13日（与备案案例一致）
        return f"{int(m.group(1)):04d}年{int(m.group(2))}月{int(m.group(3))}日"
    return s


class ReportContextBuilder:
    """报告上下文构建器 — 从各种数据源收集数据，组装成 context dict。"""

    # ── 报告类型标签映射（可被 templates/reports/docx/_labels.json 覆盖）──
    TYPE_LABELS = {
        "verification": "核查报告",
        "pdd": "项目设计文档（PDD）",
    }

    @classmethod
    def build(cls, project: dict, report_type: str = "verification",
              start_date: str = "", end_date: str = "",
              extra_placeholders: list = None) -> dict:
        """
        构建报告模板所需的完整上下文数据。

        参数:
            project: 项目字典（来自 carbon_projects 表）
            report_type: 报告类型 key
            start_date: 核查/报告起始日期 (YYYY-MM-DD)，默认近30天
            end_date: 核查/报告终止日期 (YYYY-MM-DD)，默认今天
            extra_placeholders: 额外的自定义占位符列表
                [{"name": "...", "value": "..."}, ...]
                这些值会以顶层 key 注入 context，模板可直接 {{ name }} 引用。

        返回:
            dict: 完整上下文，可直接传给 DocxReportBuilder.render()
        """
        now = datetime.now()

        # 默认日期范围：近30天
        if not end_date:
            end_date = now.strftime("%Y-%m-%d")
        if not start_date:
            from datetime import timedelta
            start_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")

        # 企业信息
        enterprise = cls._get_enterprise_config()

        # 监测数据
        monitoring = cls._get_monitoring_data(
            project["id"], start_date, end_date
        )

        # 钩子参数（取自项目主机器 —— 参数已按机器存放）
        hook_params = cls._parse_hook_params(project)

        # 机器清单（多机器时模板可写「本项目含 N 台机器」；
        # B.2/B.3/B.4 的表头由表格生成器按机器数自己决定）
        machines = cls._project_machines(project["id"])

        # 公式信息
        formula = cls._get_formula_info(project)

        # 排放源（由公式提供）
        emission_sources = cls._get_emission_sources(project, monitoring)

        # 计入期起止（A~B）。和表 B.2/B.3/B.4 的年份切分同源，都来自
        # build_credit_period_segments —— 别再让封面的「计入期起止时间」
        # 显示报告表单里填的日期区间，那样会和表 B 的年份对不上。
        crediting_start = crediting_end = ""
        try:
            from core.projection import credit_period_bounds
            cs, ce = credit_period_bounds(project)
            if cs and ce:
                crediting_start = _fmt_date_cn(cs.isoformat())
                crediting_end = _fmt_date_cn(ce.isoformat())
        except Exception:
            pass

        # 组装上下文
        context = {
            # ── 报告元信息 ──
            "report": {
                "type": report_type,
                "type_label": cls._get_type_label(report_type),
                "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "generated_date": _fmt_date_cn(now.strftime("%Y-%m-%d")),
                # 报表落款用「今天」，与 report.start_date/end_date 同格式（2026年09月17日）
                "today": _fmt_date_cn(now.strftime("%Y-%m-%d")),
                # 报告表单填的日期区间（核查报告=本核算期；PDD=仅作数据窗口）
                "start_date": _fmt_date_cn(start_date),
                "end_date": _fmt_date_cn(end_date),
                # 项目计入期 A~B，与表 B 的年份同源。PDD 封面「计入期起止时间」用这个。
                "crediting_start": crediting_start,
                "crediting_end": crediting_end,
                "crediting_range": (f"{crediting_start}-{crediting_end}"
                                    if crediting_start and crediting_end else ""),
                "generator": "通用碳账户管理系统 v1.0",
                # 年度核查报告编号（需在设置中开启；未开启为空字符串）
                "annual_code": cls.get_annual_code(project, report_type),
            },

            # ── 企业信息 ──
            "enterprise": {
                "name": enterprise.get("enterprise_name", ""),
                "id": enterprise.get("enterprise_id", ""),
                "logo_path": enterprise.get("report_logo_path", ""),
            },

            # ── 项目基本信息 ──
            "project": {
                "name": project.get("project_name", ""),
                "code": project.get("project_code") or "（未分配）",
                "type": project.get("project_type", project.get("hook_name", "")),
                "summary": project.get("project_summary", ""),
                "baseline_date": _fmt_date_cn(project.get("baseline_date", "")),
                "hook_name": project.get("hook_name", ""),
                "hook_label": formula.get("label", ""),
                # 所有钩子参数扁平化，模板可直接 {{ project.xxx }}
                **{k: str(v) if v is not None else "" for k, v in hook_params.items()},
                # ⚠️ 必须放在上面那行展开之后：钩子参数里若有同名 key 会把它盖掉。
                # _build_code_env 靠它拿项目 id 注入 get_reduction()，
                # 缺了它 range_reduction 之类的 code 预设会报 NameError。
                "id": project.get("id"),
                # 同上，放最后：机器数别被同名钩子参数盖掉
                "machine_count": len(machines),
                # 多机器提示语（模板可选用；单机器为空串，不打扰原版面）
                "machine_summary": (
                    "本项目含 %d 台设备：%s" % (
                        len(machines), "、".join(m.get("name") or "" for m in machines))
                    if len(machines) >= 2 else ""
                ),
            },

            # ── 机器清单（多设备项目）──
            "machines": [
                {"id": m.get("id"), "name": m.get("name"),
                 "data_source_type": m.get("data_source_type")}
                for m in machines
            ],

            # ── 碳资产数据 ──
            "carbon_assets": {
                "total": project.get("total_carbon_assets", 0) or 0,
                "available_gross": project.get("available_assets_gross", 0) or 0,
                "available_net": project.get("available_assets_net", 0) or 0,
                "traded": project.get("traded_amount", 0) or 0,
                "cumulative_reduction": project.get("lifetime_reduction", 0) or 0,
                "pending_verification": project.get("pending_verification_cumulative"),
                "pending_status": project.get("pending_status", "unknown"),
                "annual_estimate": project.get("estimated_annual_reduction", 0) or 0,
            },

            # ── 项目阶段 ──
            "stages": cls._build_stages(project),

            # ── 监测数据 ──
            "monitoring": monitoring,

            # ── 公式/方法学 ──
            "formula": formula,

            # ── 排放源清单（动态表） ──
            "emission_sources": emission_sources,

            # ── 声明 ──
            "declarations": [
                "本报告依据自愿型碳减排项目（VCER）管理办法生成。",
                "报告中的数据来源于项目运营监测数据和第三方审定/核查机构。",
                "碳减排量核算方法遵循 ISO 14064-2 标准。",
            ],

            # ── PDD 特有数据（所有报告都构建，PDD 模板用 {% if pdd %} 控制显隐）──
            "pdd": cls._build_pdd_context(project, formula, monitoring, hook_params),

            # ── 钩子参数原值（保留 key-value 形式，供模板遍历）──
            "hook_params": [
                {"key": k, "value": str(v) if v is not None else ""}
                for k, v in hook_params.items()
            ],

            # ── 全局自定义占位符 ──
            # 从 _custom_placeholders.json 加载用户定义的变量
            # 模板中可 {{ custom.<name> }} 或简写 {{ <name> }} 引用
            **cls._load_custom_placeholders(),
        }

        # ── 预设信息（含 code 类型：可引用上方所有 context 对象）──
        # 先构建基础 context，再展开预设，使 code 类型能访问 report/project/碳资产等
        code_env = cls._build_code_env(context, hook_params)
        context["preset"] = cls._load_presets_context(env=code_env)
        context["project_preset"] = cls._load_project_presets_context(
            project["id"], project, env=code_env)

        # ── 收集 table 类型预设的表格数据（渲染后注入真实表格）──
        cls._inject_table_data(context, project)

        # ── 合并额外占位符（模板级别的自定义占位符，来自 DB）──
        if extra_placeholders:
            for item in extra_placeholders:
                if isinstance(item, dict) and item.get("name"):
                    context[item["name"]] = str(item.get("value", ""))

        return context

    @classmethod
    def get_annual_code(cls, project: dict, report_type: str = "verification") -> str:
        """推导年度核查报告编号。

        规则（仅核查报告 verification 适用，其余类型返回 ""）：
            第 1 份年度核查报告 → 项目编号（如 GDES-VCER-036-1）
            第 k 份（k≥2）       → 项目编号-(k-1)（如 GDES-VCER-036-1-1）

        k = max(本系统该项目已生成的核查报告数, VCER 平台该项目核查已通过轮数) + 1
        编号为生成时纯推导，不占号、不落库。开关（设置-年度核查报告编号递增）
        关闭或项目无编号时返回 ""。
        """
        if report_type != "verification":
            return ""
        project_code = (project.get("project_code") or "").strip()
        if not project_code:
            return ""
        try:
            cfg = cls._get_enterprise_config()
            if not cfg or not cfg.get("annual_code_enabled"):
                return ""
        except Exception:
            return ""

        local_count = 0
        platform_rounds = 0

        # 本系统该项目已生成的核查报告数
        try:
            from core.project_manager import get_db
            conn = get_db()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM reports "
                    "WHERE project_id = ? AND report_type = 'verification'",
                    (project.get("id"),)
                ).fetchone()
                local_count = row["cnt"] if row else 0
            finally:
                conn.close()
        except Exception:
            pass

        # VCER 平台该项目核查（阶段3）已通过轮数
        try:
            query_key = project.get("vcer_query_key")
            if query_key:
                import config as app_config
                # 状态白名单必须和 core/gap_bridge.py 共用一份（core/vcer_client.py
                # 的 PASSED_STATUSES）。这里多认一种写法，那边就会少扣一整轮核证量。
                from core.vcer_client import VCERClient, is_passed_status
                client = VCERClient(app_config.VCER_API_URL,
                                    cfg.get("api_access_key") if cfg else None)
                data = client.get_stage_rounds(query_key, 3)
                rounds = []
                if isinstance(data, dict):
                    rounds = data.get("rounds") or data.get("data") or []
                elif isinstance(data, list):
                    rounds = data
                platform_rounds = sum(
                    1 for r in rounds
                    if isinstance(r, dict) and is_passed_status(r.get("status"))
                )
        except Exception:
            pass

        k = max(local_count, platform_rounds) + 1
        return project_code if k <= 1 else f"{project_code}-{k - 1}"

    # ══════════════════════════════════════════════════════════
    # 数据收集子方法
    # ══════════════════════════════════════════════════════════

    @classmethod
    def _get_enterprise_config(cls) -> dict:
        """获取企业配置"""
        try:
            from core.project_manager import get_enterprise_config
            cfg = get_enterprise_config()
            if cfg:
                return cfg
        except Exception:
            pass
        return {}

    @classmethod
    def _get_monitoring_data(cls, project_id: int, start_date: str, end_date: str) -> dict:
        """获取指定日期范围内的监测数据摘要"""
        from core.project_manager import get_db

        conn = get_db()
        try:
            # 统计汇总
            row = conn.execute(
                """SELECT COUNT(*) as cnt,
                   COALESCE(SUM(carbon_reduction), 0) as total_reduction
                   FROM monitoring_data
                   WHERE project_id = ? AND timestamp >= ? AND timestamp <= ?""",
                (project_id, f"{start_date} 00:00:00", f"{end_date} 23:59:59")
            ).fetchone()

            # 最新记录
            latest = conn.execute(
                """SELECT * FROM monitoring_data
                   WHERE project_id = ? AND timestamp >= ? AND timestamp <= ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (project_id, f"{start_date} 00:00:00", f"{end_date} 23:59:59")
            ).fetchone()

            # 每日明细（供图表使用）
            daily_rows = conn.execute(
                """SELECT DATE(timestamp) as date,
                   COALESCE(SUM(carbon_reduction), 0) as daily_reduction,
                   COUNT(*) as records
                   FROM monitoring_data
                   WHERE project_id = ? AND timestamp >= ? AND timestamp <= ?
                   GROUP BY DATE(timestamp)
                   ORDER BY date""",
                (project_id, f"{start_date} 00:00:00", f"{end_date} 23:59:59")
            ).fetchall()

            total_records = row["cnt"] if row else 0
            total_reduction = row["total_reduction"] or 0 if row else 0

            # 计算天数
            try:
                d1 = datetime.strptime(start_date, "%Y-%m-%d")
                d2 = datetime.strptime(end_date, "%Y-%m-%d")
                days = max((d2 - d1).days + 1, 1)
            except Exception:
                days = 30

            daily_avg = total_reduction / days if days > 0 else 0
            annual_estimate = daily_avg * 365

            # 解析最新 breakdown
            breakdown_dict = {}
            if latest and latest["breakdown"]:
                try:
                    bd = latest["breakdown"]
                    if isinstance(bd, str):
                        bd = json.loads(bd)
                    if isinstance(bd, dict):
                        breakdown_dict = bd
                except Exception:
                    pass

            # ── 十年预估总量（PDD 封面 / 审定备案碳减排量）──
            #
            # ⚠️ 口径说明：这里**绝不**能用「报告表单填的区间天数」当分母。
            # 表单在 PDD/审定里填的是十年计入期，拿它当分母会把日均稀释几十倍
            # （2026-01-01~2036-12-31 那次：33.69 ÷ 4018 × 365 × 10 = 30.61）。
            #
            # 优先级：
            #   ① 阶段一已固化的 pending_validation_10yr —— 申报过的数任何人不能改写
            #   ② 已固化的 projection_10yr 各行之和
            #   ③ 统一口径实时算：首年段实测值 + 其余各段按「首年段值 ÷ 首年段天数 × 该段天数」
            #   ④ 都取不到 → 0，并置 ten_year_total_ready=False，让上层提示「首年段没有数据」
            ten_year_total = 0.0
            ten_year_ready = False
            pre_estimated = 0.0
            try:
                proj_row = conn.execute(
                    "SELECT baseline_date, estimated_annual_reduction, "
                    "       pending_validation_10yr, projection_10yr "
                    "FROM carbon_projects WHERE id = ?",
                    (project_id,)
                ).fetchone()

                # ── 多机器（≥2 台）：十年总量 = **各机器分段之和** ──
                # 固化的 pending_validation_10yr 是「一项目一设备」时代存下的
                # 单份值，装不下多台机器；而且机器数一变，那份数本来也不再成立。
                # 所以多机器一律按统一口径实时算（每台各自首年段折算再相加，
                # 见 core/projection.py），与 B.2/B.3/B.4 的多机器表同源。
                if len(cls._project_machines(project_id)) >= 2:
                    from core.projection import build_credit_period_segments
                    seg = build_credit_period_segments({
                        "id": project_id,
                        "baseline_date": (proj_row["baseline_date"] if proj_row else "") or "",
                    })
                    if seg.get("has_first_segment_data"):
                        ten_year_total = seg["total_reduction"]
                        pre_estimated = seg["annual_reduction"]
                        ten_year_ready = True

                if proj_row and not ten_year_ready:
                    # ⚠️ 这三步一律用 `not in (None, "")` 判「有没有填」，
                    # **不能**用 `if proj_row[...]:` —— 0 是合法值：
                    # 阶段一申报十年总量填 0（设备已装未投运、或本期声明无减排）
                    # 会固化出一个 0，用真值判断会把它当成"没填"，
                    # 一路跳到「预估年减排量×10」或实时折算，封面就印出一个
                    # 跟申报值完全不符的大数，还会被 reports.parameters 当成
                    # 权威值回灌到 _ten_year_total_for。
                    # app.py::_ten_year_total_for 的同一链条就是这么写的，两边必须一致
                    # （tools/_test_machines_migration.py 有「封面十年总量 == 接口」的断言）。
                    if proj_row["pending_validation_10yr"] not in (None, ""):
                        ten_year_total = float(proj_row["pending_validation_10yr"])
                        pre_estimated = ten_year_total / 10.0
                        ten_year_ready = True
                    elif proj_row["projection_10yr"]:
                        # 有固化的逐年表就认它，**哪怕合计合法地为 0**
                        # （首年段没数据时会固化成全 0 骨架，封面必须也是 0，
                        #   才能和表 B.2/B.3/B.4 的「合计 0.00」对得上）
                        ten_year_total = cls._projection_total(proj_row["projection_10yr"])
                        pre_estimated = ten_year_total / 10.0
                        ten_year_ready = True
                    elif proj_row["estimated_annual_reduction"] not in (None, ""):
                        ten_year_total = float(proj_row["estimated_annual_reduction"]) * 10
                        pre_estimated = ten_year_total / 10.0
                        ten_year_ready = True
                if not ten_year_ready:
                    from core.projection import build_credit_period_segments
                    seg = build_credit_period_segments({
                        "id": project_id,
                        "baseline_date": (proj_row["baseline_date"] if proj_row else "") or "",
                    })
                    if seg.get("has_first_segment_data"):
                        ten_year_total = seg["total_reduction"]
                        pre_estimated = seg["annual_reduction"]
                        ten_year_ready = True
            except Exception:
                pass

            # 构建 monitoring 上下文字典，将 breakdown 中的 key 扁平化注入
            # 这样模板可以直接用 {{ monitoring.consumed_power }} 等引用
            monitoring_ctx = {
                "total_records": total_records,
                "total_reduction": round(total_reduction, 6),
                "total_reduction_fmt": f"{total_reduction:.2f}",
                "days": days,
                "daily_avg": round(daily_avg, 6),
                "daily_avg_fmt": f"{daily_avg:.4f}",
                "annual_estimate": round(annual_estimate, 4),
                "annual_estimate_fmt": f"{annual_estimate:.2f}",
                "pre_estimated_reduction": round(pre_estimated, 4),
                "pre_estimated_reduction_fmt": f"{pre_estimated:.2f}" if pre_estimated else "0.00",
                "ten_year_total": round(ten_year_total, 2),
                "ten_year_total_fmt": f"{ten_year_total:.2f}",
                # False = 计入期首年段没有监测数据，预估值无从算起（不是「算出来是 0」）。
                # 模板/前端据此提示，别把 0 当成真实值。
                "ten_year_ready": ten_year_ready,
                # 采集间隔（取自 config.COLLECTION_INTERVAL_SECONDS）。
                # B.7「监测频次与记录要求」那几格要写具体秒数，写死会跟配置脱节。
                "collect_interval_seconds": _collect_interval_seconds(),
                "collect_interval_text": _collect_interval_text(),
                # 注意：上面 annual_estimate 仍是「本区间折算年减排量」（核查报告用），
                # 与 pre_estimated_reduction（十年口径的年均）不是一回事，别混用。
                "latest": dict(latest) if latest else None,
                "latest_breakdown": breakdown_dict,
                "breakdown_items": [
                    {"key": k, "value": str(v)}
                    for k, v in breakdown_dict.items()
                ],
                "daily_data": [
                    {"date": r["date"], "reduction": r["daily_reduction"] or 0,
                     "records": r["records"]}
                    for r in (daily_rows or [])
                ],
            }

            # ── 计算累计值：汇总所有 breakdown 中的可累加数值字段 ──
            # 模板需要的是整个核算期的累计值（消耗电量、压缩空气量、基准线排放等），
            # 而每条 monitoring 记录的 breakdown 是单次增量。这里汇总全部记录。
            cumulative_sums = {}  # {key: sum}
            runtime_start = None
            runtime_end = None
            try:
                all_breakdowns = conn.execute(
                    """SELECT timestamp, breakdown FROM monitoring_data
                       WHERE project_id = ? AND timestamp >= ? AND timestamp <= ?
                       ORDER BY timestamp""",
                    (project_id, f"{start_date} 00:00:00", f"{end_date} 23:59:59")
                ).fetchall()
                if all_breakdowns:
                    runtime_start = all_breakdowns[0]["timestamp"]
                    runtime_end = all_breakdowns[-1]["timestamp"]
                for row_bd in (all_breakdowns or []):
                    try:
                        bd_item = row_bd["breakdown"]
                        if isinstance(bd_item, str):
                            bd_item = json.loads(bd_item)
                        if isinstance(bd_item, dict):
                            for bk, bv in bd_item.items():
                                if isinstance(bv, (int, float)):
                                    cumulative_sums[bk] = cumulative_sums.get(bk, 0) + bv
                    except Exception:
                        pass
            except Exception:
                pass

            # ── 将 breakdown key 扁平化注入 + 累计值映射到语义化名称 ──
            # 1) 首先注入最新一条 breakdown 的原始 key → {{ monitoring.delta_power_kwh }} 等
            for k, v in breakdown_dict.items():
                safe_key = k.strip().replace(" ", "_").replace("-", "_")
                if safe_key and safe_key not in monitoring_ctx:
                    if isinstance(v, (int, float)):
                        monitoring_ctx[safe_key] = v
                        monitoring_ctx[safe_key + "_fmt"] = f"{v:.2f}"
                    else:
                        monitoring_ctx[safe_key] = str(v) if v else ""

            # 2) 注入累计值，并映射到语义化名称（供企业模板使用）
            # breakdown key → 语义化名称映射
            _cumulative_aliases = {
                "delta_power_kwh": "consumed_power",        # 消耗电量 (kWh)
                "delta_flow_m3": "compressed_air_volume",   # 压缩空气量 (m³)
                "baseline_emissions_tco2": "baseline_emissions",  # 基准线排放量
                "project_emissions_tco2": "project_emissions",    # 项目排放量
            }
            for raw_key, alias in _cumulative_aliases.items():
                total_val = cumulative_sums.get(raw_key, 0)
                monitoring_ctx[alias] = round(total_val, 2)
                monitoring_ctx[alias + "_fmt"] = f"{total_val:.2f}"

            # 3) 运行时长（分钟）— 首末条记录的时间差
            if runtime_start and runtime_end:
                try:
                    t1 = datetime.strptime(runtime_start, "%Y-%m-%d %H:%M:%S")
                    t2 = datetime.strptime(runtime_end, "%Y-%m-%d %H:%M:%S")
                    runtime_minutes = int((t2 - t1).total_seconds() / 60)
                    monitoring_ctx["runtime"] = runtime_minutes
                    monitoring_ctx["runtime_fmt"] = f"{runtime_minutes}"
                except Exception:
                    monitoring_ctx["runtime"] = 0
                    monitoring_ctx["runtime_fmt"] = "0"
            else:
                monitoring_ctx["runtime"] = 0
                monitoring_ctx["runtime_fmt"] = "0"

            return monitoring_ctx
        finally:
            conn.close()

    @classmethod
    def _project_machines(cls, project_id) -> list:
        """取项目机器列表（查不到返回 []）。"""
        try:
            from core.project_manager import get_project_machines
            return get_project_machines(project_id) if project_id else []
        except Exception:
            return []

    @classmethod
    def _primary_machine(cls, project: dict) -> dict:
        """取项目的**主机器**（排序第一台）。查不到返回 {}。"""
        try:
            from core.project_manager import get_primary_machine
            pid = project.get("id")
            return (get_primary_machine(pid) if pid else None) or {}
        except Exception:
            return {}

    @classmethod
    def _parse_hook_params(cls, project: dict) -> dict:
        """解析项目的钩子参数。

        多机器改造后参数按机器存放（项目级那列降级为 legacy），报告读的是
        **主机器**那份 —— 同一项目共用一套方法学参数，模板里的
        {{ project.dew_point }} 这类标量以主机器为代表；各机器逐台的值
        由 B.2/B.3/B.4 的表体按列呈现。
        """
        raw = None
        machine = cls._primary_machine(project)
        if machine:
            raw = machine.get("hook_params")
        if not raw:
            # 回退：项目还没建机器 / 主机器没配参数时，读项目级 legacy 列
            raw = project.get("hook_params", "{}")
        try:
            if isinstance(raw, str):
                raw = json.loads(raw)
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
        return {}

    @classmethod
    def _get_formula_info(cls, project: dict) -> dict:
        """获取公式信息"""
        result = {
            "name": project.get("hook_name", ""),
            "label": "",
            "text": "",
            "version": "",
            "description": "",
            "parameters": {},
        }

        hook_name = project.get("hook_name", "")
        if not hook_name:
            return result

        try:
            from core.carbon_calculator import CarbonCalculator

            # 获取公式类元信息
            formula_cls = CarbonCalculator._formulas_registry.get(hook_name)
            if formula_cls:
                result["label"] = getattr(formula_cls, "formula_label", hook_name)
                result["version"] = getattr(formula_cls, "formula_version", "")
                result["description"] = getattr(formula_cls, "description", "")

            # 获取实例的计算公式文本（公式实例按机器缓存，取主机器的那个）
            machine = cls._primary_machine(project)
            mid = machine.get("id")
            if not (mid and mid in CarbonCalculator._instances):
                try:
                    import config
                    CarbonCalculator.init_project_formula(project, config.VCER_API_URL)
                    mid = (cls._primary_machine(project) or {}).get("id")
                except Exception:
                    mid = None
            if mid:
                info = CarbonCalculator.get_formula_info(mid)
                result["text"] = info.get("formula", "")
                result["parameters"] = info.get("parameters", {})

            # 基准线因子（API 获取，如 z_b / w_b），供模板 {{ formula.z_b }} 等引用
            try:
                from core.carbon_calculator import CarbonCalculator
                formula = CarbonCalculator.get_instance(mid) if mid else None
                if formula:
                    factors = formula.get_baseline_factors()
                    if isinstance(factors, dict):
                        for k, v in factors.items():
                            result[k] = v
                            result.setdefault("baseline", {})[k] = v
            except Exception:
                pass

            # 电网排放因子
            try:
                import config
                # ⚠️ 用 is None 判「没配」而不是真值判断：电网排放因子填 0 是合法值
                # （全绿电供应 / 做 what-if），`or` 会把它换成默认 0.5777。
                # schema 里写明了「NULL=使用默认值」，所以哨兵是 NULL 不是 0。
                _ef = project.get("ef_grid_override")
                result["ef_grid"] = config.EF_GRID_DEFAULT if _ef is None else _ef
            except Exception:
                result["ef_grid"] = 0.5777

        except Exception:
            pass

        # 确保 text 不为空
        if not result["text"]:
            result["text"] = cls._get_formula_text_fallback(project)

        return result

    @classmethod
    def _get_formula_text_fallback(cls, project: dict) -> str:
        """回退：从公式类获取公式文本"""
        hook_name = project.get("hook_name", "")
        if not hook_name:
            return ""
        try:
            from core.carbon_calculator import CarbonCalculator
            formula_cls = CarbonCalculator._formulas_registry.get(hook_name)
            if formula_cls:
                # 创建临时实例获取公式文本
                params = cls._parse_hook_params(project)
                try:
                    import config
                    temp = formula_cls(params, config.VCER_API_URL)
                    return temp.get_formula_text()
                except Exception:
                    pass
        except Exception:
            pass
        return ""

    @classmethod
    def _get_emission_sources(cls, project: dict, monitoring: dict) -> list:
        """
        获取排放源清单（动态表格数据）。

        优先使用公式实例的 get_emission_sources_for_report() 方法，
        如果没有实现则使用默认推断。
        """
        hook_name = project.get("hook_name", "")
        if not hook_name:
            return cls._default_emission_sources(project, monitoring)

        try:
            from core.carbon_calculator import CarbonCalculator
            # 排放源清单用主机器那份 breakdown（清单是项目口径的，一台代表即可）
            mid = (cls._primary_machine(project) or {}).get("id")

            if mid and mid in CarbonCalculator._instances:
                formula = CarbonCalculator._instances[mid]
                if hasattr(formula, "get_emission_sources_for_report"):
                    sources = formula.get_emission_sources_for_report(monitoring)
                    if sources:
                        return sources
        except Exception:
            pass

        # 回退：用公式文本生成默认排放源
        return cls._default_emission_sources(project, monitoring)

    @classmethod
    def _default_emission_sources(cls, project: dict, monitoring: dict) -> list:
        """当公式未提供排放源时，生成默认的通用排放源表"""
        formula_info = cls._get_formula_info(project)
        latest = monitoring.get("latest_breakdown", {})

        # 从 breakdown 推断排放源
        if latest:
            return [{
                "seq": 1,
                "name": project.get("project_type", project.get("hook_name", "碳减排项目")),
                "activity_data": latest.get("formula", str(latest)),
                "emission_factor": f"{formula_info.get('ef_grid', 0.5777)} tCO₂e/MWh",
                "emission_amount": f"{monitoring.get('total_reduction', 0):.4f}",
            }]

        return [{
            "seq": 1,
            "name": project.get("project_type", project.get("hook_name", "碳减排项目")),
            "activity_data": "见监测数据",
            "emission_factor": "见计算公式",
            "emission_amount": f"{monitoring.get('total_reduction', 0):.4f}",
        }]

    @classmethod
    def _build_stages(cls, project: dict) -> list:
        """构建项目5阶段状态"""
        stage_names = {
            1: "项目申请", 2: "项目审定", 3: "项目核查",
            4: "项目交易", 5: "碳减排量抵消",
        }
        stages = []
        for s in range(1, 6):
            status = "已通过" if project.get(f"stage{s}_passed") else "未提交"
            stages.append({
                "num": s,
                "name": stage_names.get(s, f"阶段{s}"),
                "status": status,
                "is_completed": status in ("已通过", "已完成"),
            })
        return stages

    @classmethod
    def _load_custom_placeholders(cls) -> dict:
        """加载全局自定义占位符并返回可解包到 context 的 dict。

        同时注入两个层级：
        - custom: 命名空间 dict → 模板用 {{ custom.<name> }}
        - 顶层 key: 简写 → 模板可直接 {{ <name> }}
        """
        try:
            from core.custom_placeholders import get_custom_placeholders_context
            return get_custom_placeholders_context()
        except Exception:
            return {"custom": {}}

    @classmethod
    def _load_presets_context(cls, env=None) -> dict:
        """加载企业预设信息（text 值，不含 image InlineImage 对象）。"""
        try:
            from core.presets import get_presets_context
            return get_presets_context(doc=None, env=env)
        except Exception:
            return {}

    @staticmethod
    def _projection_total(raw):
        """已固化的 projection_10yr（JSON 字符串/列表）中减排量的合计；无有效数据返回 0。"""
        if not raw:
            return 0.0
        try:
            rows = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(rows, list):
                return 0.0
            return round(sum(float(r.get("reduction", 0) or 0) for r in rows if isinstance(r, dict)), 2)
        except Exception:
            return 0.0

    @classmethod
    def _inject_table_data(cls, context: dict, project: dict):
        """收集 table 类型预设的 (headers, rows)，供 DocxReportBuilder 渲染后注入真实表格。"""
        try:
            from core.project_presets import load_all_project_presets, _build_table_data
            table_data = {}
            for p in load_all_project_presets(project["id"]):
                if p.get("type") != "table":
                    continue
                headers, rows, summary = _build_table_data(p.get("value", ""), project)
                if headers:
                    table_data[p["key"]] = {"headers": headers, "rows": rows,
                                            "summary_rows": list(summary or [])}
            context["_table_data"] = table_data
        except Exception:
            context["_table_data"] = {}

    @classmethod
    def _load_project_presets_context(cls, project_id: int, project: dict, env=None) -> dict:
        """加载项目预设信息（含 text/richtext/computed/code，不含 image InlineImage）。"""
        try:
            from core.project_presets import get_project_presets_context
            return get_project_presets_context(project_id, project, env=env)
        except Exception:
            return {}

    @staticmethod
    def _build_code_env(context: dict, hook_params: dict) -> dict:
        """构建 code 类型预设 / 控制台执行环境：顶层对象 + hook_params 裸变量。"""
        env = dict(context)
        # hook_params 裸变量（dew_point、has_oil 等），不覆盖顶层对象名
        for k, v in (hook_params or {}).items():
            if k not in env:
                env[k] = v if v is not None else ""
        env.setdefault("project_dict", context.get("project", {}))
        # 注入 get_reduction(start, end)：按当前项目查任意日期区间减排量
        try:
            from core.reduction import make_get_reduction
            pid = (context.get("project") or {}).get("id")
            if pid and "get_reduction" not in env:
                env["get_reduction"] = make_get_reduction(pid)
        except Exception:
            pass
        return env

    @classmethod
    def get_preset_inline_images(cls) -> dict:
        """获取 image 类型预设的绝对路径映射，供 DocxReportBuilder 创建 InlineImage。

        返回:
            dict: {"logo": "E:/.../data/preset_images/logo.png", ...}
        """
        try:
            from core.presets import load_presets, get_image_absolute_path
            presets = load_presets()
            result = {}
            for p in presets:
                if p.get("type") == "image" and p.get("value"):
                    abs_path = get_image_absolute_path(p["value"])
                    if abs_path and os.path.exists(abs_path):
                        result[p["key"]] = abs_path
            return result
        except Exception:
            return {}

    @classmethod
    def get_project_preset_inline_images(cls, project_id: int) -> dict:
        """获取 image 类型项目预设的绝对路径映射。

        返回:
            dict: {"site_photo": "E:/.../data/project_images/xxx.png", ...}
        """
        try:
            from core.project_presets import get_project_preset_inline_images
            return get_project_preset_inline_images(project_id)
        except Exception:
            return {}

    @classmethod
    def _build_pdd_context(cls, project: dict, formula: dict,
                           monitoring: dict, hook_params: dict) -> dict:
        """
        构建 PDD（项目设计文档）特有数据。

        这些数据在所有报告中都会被构建，但只有 PDD 模板会使用。
        其他模板通过 {% if pdd %} 控制显隐。
        """
        # 十年逐年预估（优先存库，否则公式实时算），同时注入 monitoring.yearly_projection
        projection = cls._build_projection(project, monitoring)

        return {
            # ── 方法学 ──
            "methodology": {
                "reference": "CDM ACM0002 — 可再生能源发电并网",
                "applicability": (
                    f"本项目为{formula.get('label', project.get('hook_name', ''))}类型，"
                    f"满足方法学{formula.get('version', '')}的适用条件。"
                ),
                "formula_text": formula.get("text", ""),
                "parameters": [
                    {"key": k, "value": str(v)}
                    for k, v in formula.get("parameters", {}).items()
                ],
            },

            # ── 额外性论证 ──
            "additionality": {
                "barrier_analysis": cls._build_barrier_analysis(project),
                "common_practice": (
                    f"本项目采用{formula.get('label', '低碳')}技术，"
                    f"在行业内尚未成为普遍做法，具有额外性。"
                ),
                "investment_analysis": (
                    "项目投资内部收益率（IRR）低于行业基准值，"
                    "碳减排收益是项目可行的关键因素。"
                ),
            },

            # ── 环境影响评价 ──
            "environmental_impact": (
                f"项目实施后预计年减排{monitoring.get('annual_estimate', 0):.2f} tCO₂e，"
                f"对当地环境产生正面影响。项目建设及运营过程中不产生重大环境污染。"
            ),

            # ── 利益相关方意见 ──
            "stakeholder_comments": (
                "已通过适当方式征求项目所在地利益相关方的意见，"
                "未收到反对意见。"
            ),

            # ── 监测计划 ──
            "monitoring_plan": {
                "parameters": [
                    {"parameter": "碳减排量", "unit": "tCO₂e",
                     "frequency": "实时/定时采集", "method": "公式自动计算"},
                    {"parameter": "原始数据", "unit": "依公式而定",
                     "frequency": "依数据源配置", "method": "MQTT/REST API/手动录入"},
                ],
                "qa_qc": (
                    "数据采集系统定期校准，数据传输采用加密通道，"
                    "数据存储采用 SQLite 数据库并定期备份。"
                ),
            },

            # ── 计入期 ──
            "crediting_period": {
                "type": "固定计入期（10年）",
                "first_period": "10年（自计入期起始日起）",
                "max_periods": "—",
                "start_date": _fmt_date_cn(project.get("baseline_date", "待定")),
                "years": 10,
            },

            # ── 项目概览 ──
            "overview": {
                "project_type": project.get("project_type", project.get("hook_name", "")),
                "project_code": project.get("project_code", "（待分配）"),
                "baseline_scenario": (
                    f"在没有本项目的情况下，同等能源/资源需求将通过传统方式满足，"
                    f"产生相应的温室气体排放。"
                ),
                "project_scenario": (
                    f"通过实施{formula.get('label', '低碳')}项目，"
                    f"替代传统高排放方式，实现温室气体减排。"
                ),
            },

            # ── 十年逐年预估（基准线排放 / 项目排放 / 减排量）──
            "projection": projection,
        }

    @classmethod
    def _build_projection(cls, project: dict, monitoring: dict) -> list:
        """获取十年逐年预估，格式化日期与数值，并注入 monitoring.yearly_projection。"""
        rows = []
        try:
            from core import projection as proj_mod
            rows = proj_mod.get_projection(project) or []
        except Exception:
            rows = []

        formatted = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            baseline = r.get("baseline_emission", 0) or 0
            project_emi = r.get("project_emission", 0) or 0
            reduction = r.get("reduction", 0) or 0
            formatted.append({
                "year": r.get("year", ""),
                "start": _fmt_date_cn(r.get("start") or ""),
                "end": _fmt_date_cn(r.get("end") or ""),
                "baseline_emission": baseline,
                "baseline_emission_fmt": f"{baseline:.2f}",
                "project_emission": project_emi,
                "project_emission_fmt": f"{project_emi:.2f}",
                "reduction": reduction,
                "reduction_fmt": f"{reduction:.2f}",
            })

        monitoring["yearly_projection"] = formatted
        monitoring["yearly_projection_count"] = len(formatted)
        return formatted

    @classmethod
    def _build_barrier_analysis(cls, project: dict) -> str:
        """构建额外性屏障分析"""
        hook_name = project.get("hook_name", "")
        barriers = {
            "solar_pv": "光伏发电技术虽已成熟，但分布式项目在融资、并网等方面仍面临障碍。",
            "air_compressor": "高效空压站技术需要较高的初始投资，中小企业面临资金壁垒。",
            "waste_heat_recovery": "余热回收技术需要根据工况定制，技术适应性是主要障碍。",
            "energy_saving_motor": "高效电机/变频器改造成本较高，投资回收期是主要顾虑。",
            "biomass_boiler": "生物质燃料供应链尚未完善，燃料可获得性是主要障碍。",
            "led_lighting": "LED 虽已普及，但大规模替换仍面临初期资金压力。",
            "heat_pump": "热泵技术初始投资高，在低温环境下效率下降是技术障碍。",
        }
        return barriers.get(hook_name, "项目面临技术、资金或市场方面的障碍，具有额外性。")

    @classmethod
    def _get_type_label(cls, report_type: str) -> str:
        """获取报告类型的中文标签"""
        # 优先从模板发现中获取
        try:
            from . import get_report_type_label
            label = get_report_type_label(report_type)
            if label and label != report_type:
                return label
        except Exception:
            pass
        return cls.TYPE_LABELS.get(report_type, report_type)
