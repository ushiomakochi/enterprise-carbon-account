"""
历史数据导入核心 — CSV / Excel / API 导入共用。

两种减排量计算方式（可逐行混用）：
- 直填：行内提供 carbon_reduction 值，直接采用。
- 公式：提供公式原始字段（如 cumulative_flow / cumulative_power），
  按时间顺序用 hook 公式算增量减排量并累计（与实时采集一致）。
"""

import json
import re
import math
from datetime import datetime
from datetime import datetime

from .project_manager import get_db

# 原始字段中文名（用于模板表头 + 前端提示；缺省回退英文名）
RAW_FIELD_LABELS = {
    "timestamp": "时间",
    "carbon_reduction": "碳减排量",
    "cumulative_flow": "压缩空气输出量",
    "cumulative_power": "累计耗电量",
    "cumulative_steam_output": "累计蒸汽产出量",
    "cumulative_energy_actual": "累计实际能耗",
    "cumulative_heat_output": "累计供热量",
    "cumulative_electricity": "累计耗电量",
    "cumulative_runtime": "累计运行时长",
    "cumulative_generation": "累计发电量",
    "cumulative_heat_recovered": "累计回收热量",
}

# 原始字段的**单位** —— 导入历史数据最容易搞错的就是这个
# （kWh 还是 MWh？m³ 还是 L？）。取值与各公式自己的参数说明同源
# （hooks/*.py 的 get_parameter_descriptions()），也就是公式实际拿去算的那个口径。
#
# ⚠️ 这里写的是**录入单位**，不是公式内部换算后的单位：
# 例如 solar_pv 要求填 kWh，代码内部再 /1000 转成 MWh 参与计算；
# energy_saving_motor 同理。只要按这里的单位填，就不会差 1000 倍。
RAW_FIELD_UNITS = {
    "cumulative_flow": "m³",              # 空压站：累计产气量
    "cumulative_power": "kWh",            # 空压站：累计耗电量
    "cumulative_steam_output": "吨",       # 生物质锅炉：累计蒸汽产出量
    "cumulative_biomass_consumed": "吨",   # 生物质锅炉：累计生物质消耗量（可选）
    "cumulative_energy_actual": "kWh",    # 节能电机/变频器：累计实际用电量
    "cumulative_heat_output": "MJ",       # 热泵：累计热输出
    "cumulative_electricity": "kWh",      # 热泵：累计耗电量
    "cumulative_runtime": "小时",          # LED照明：累计运行时间
    "cumulative_generation": "kWh",       # 光伏：累计发电量
    "cumulative_heat_recovered": "MJ",    # 余热回收：累计回收热量
    "carbon_reduction": "tCO₂e",          # 直填模式：本期减排量
    "timestamp": "YYYY-MM-DD HH:MM:SS",   # 时间
}

_TS_ALIASES = {"时间", "日期", "date", "time", "timestamp"}
_CR_ALIASES = {"碳减排量", "减排量", "carbon_reduction", "reduction"}


def get_required_raw_fields(project):
    """从公式注册表读取原始字段（不初始化实例、不触发网络请求）。"""
    from .carbon_calculator import CarbonCalculator
    hook_name = project.get("hook_name", "")
    formula_cls = CarbonCalculator._formulas_registry.get(hook_name)
    if not formula_cls:
        raise Exception(f"未找到公式: '{hook_name}'")
    return list(getattr(formula_cls, "required_raw_fields", []) or [])


def resolve_machine(project, machine_id=None):
    """确定这批数据落到哪台机器：显式指定优先，否则用项目的主机器。"""
    from .project_manager import get_machine, ensure_project_machine
    if machine_id:
        m = get_machine(machine_id)
        if not m:
            raise Exception(f"机器 {machine_id} 不存在")
        if m["project_id"] != project["id"]:
            raise Exception(f"机器 {machine_id} 不属于项目 #{project['id']}")
        return m
    return ensure_project_machine(project["id"])


def get_formula(machine, hook_name):
    """初始化并返回**机器**公式实例与其 required_raw_fields。

    公式（hook_name）挂在项目上，参数（hook_params）挂在机器上 —— 所以两个都要。
    """
    import config as cfg
    from .carbon_calculator import CarbonCalculator
    mid = machine["id"]
    if mid not in CarbonCalculator._instances:
        CarbonCalculator.init_machine_formula(machine, hook_name, cfg.VCER_API_URL)
    formula = CarbonCalculator.get_instance(mid)
    if not formula:
        raise Exception(f"机器 #{mid} 未初始化公式，无法导入")
    return formula, list(getattr(formula, "required_raw_fields", []) or [])


def normalize_timestamp(ts):
    """把各种时间写法规范化为 'YYYY-MM-DD HH:MM:SS'；无效返回 None。"""
    s = str(ts or "").strip().replace("T", " ").replace("/", "-")
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", s):
        s += " 00:00:00"
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d{2}$", s):
        s += ":00"
    if not re.match(r"^\d{4}-\d{1,2}-\d{1,2} \d{1,2}:\d{2}:\d{2}$", s):
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def header_to_field(header):
    """表头 → 字段名。支持 '中文(field)'、'中文(field) [单位]' 或直接 'field'，
    以及常见中文别名。

    末尾的单位是可选的（模板里会带上，用户自己删掉或改写成别的写法也要认）。
    """
    h = str(header or "").strip()
    m = re.search(r"\(([A-Za-z_][A-Za-z0-9_]*)\)\s*(?:\[[^\]]*\]|（[^）]*）)?\s*$", h)
    if m:
        return m.group(1)
    if h in _TS_ALIASES:
        return "timestamp"
    if h in _CR_ALIASES:
        return "carbon_reduction"
    return h


# 模板里那一行示例的「时间」列内容。写成一句人话，同时充当"跳过本行"的标记 ——
# 见 build_template_rows 的说明。
_EXAMPLE_ROW_MARKER = "（示例行，请删除或覆盖本行后再导入）"


def field_header(f):
    """一个字段的表头文字：`中文名(field) [单位]`。

    单位直接写在表头里（而不是另起一列）—— 用户删列/调列序都不会让单位和数据错位，
    CSV 和 Excel 里看到的都一样。`header_to_field` 认得这个格式。
    """
    label = RAW_FIELD_LABELS.get(f, f)
    unit = RAW_FIELD_UNITS.get(f)
    return f"{label}({f}) [{unit}]" if unit else f"{label}({f})"


def build_template_rows(project):
    """返回模板二维数组（首行表头 + 1 行示例），以及 raw_fields。

    表头带单位，例如：`压缩空气输出量(cumulative_flow) [m³]`。

    ⚠️ 示例行的「时间」故意写成一句说明（不是合法时间戳），并且
    `parse_table` 会**认出并跳过它**。原因：原来示例行写的是合法时间
    （2023-01-31 23:59:59）且各列为空 → 用户要是不删它，它会被当成**真数据**导入；
    而它所有读数都是 0，一旦成为该机器的第一条（基线），
    用户真正的第一条数据就会去减这个 0 —— 等于把整表读数记成减排量。
    这是 `collect_and_store` 里那条「首条=基线」的补丁**唯一能被绕过**的路子。
    """
    raw_fields = get_required_raw_fields(project)
    header = [field_header("timestamp")]
    for f in raw_fields:
        header.append(field_header(f))
    header.append(field_header("carbon_reduction"))
    # 示例行顺便当"填法说明"：碳减排量那列是最容易误解的一格 ——
    # 它留空和填了走的是两条完全不同的路（见 import_rows）。
    # 本行会被 parse_table 静默跳过，所以写多长都不影响导入。
    note = ("留空 = 按左边的读数用公式算；"
            "填了 = 直接采用你填的值、公式不再参与（该行也就没有基准线/项目排放分解）")
    example = [_EXAMPLE_ROW_MARKER] + [""] * len(raw_fields) + [note]
    return [header, example], raw_fields


def parse_table(header_row, data_rows, raw_fields):
    """把 [表头], [数据行] 解析成 [{timestamp, <raw_fields>, carbon_reduction?}]。"""
    col = {}
    for i, h in enumerate(header_row):
        field = header_to_field(h)
        if field:
            col[field] = i

    ts_idx = col.get("timestamp")
    cr_idx = col.get("carbon_reduction")
    if ts_idx is None:
        raise Exception("未找到时间列（表头需含 timestamp 或「时间」）")

    rows = []
    errors = []

    def cell(dr, idx):
        return dr[idx] if idx is not None and idx < len(dr) else ""

    for rno, dr in enumerate(data_rows, start=2):
        # 模板自带的示例行：静默跳过（它不是错误，只是没被覆盖掉的样板）
        if _EXAMPLE_ROW_MARKER in str(cell(dr, ts_idx) or ""):
            continue
        ts = normalize_timestamp(cell(dr, ts_idx))
        if not ts:
            errors.append(f"第{rno}行时间格式无效: {cell(dr, ts_idx) or '(空)'}")
            continue
        rec = {"timestamp": ts}
        for f in raw_fields:
            rec[f] = cell(dr, col.get(f))
        cr = cell(dr, cr_idx).strip()
        if cr:
            rec["carbon_reduction"] = cr
        rows.append(rec)
    return rows, errors


def _warn_if_boundary_gap(project_id, machine, prev_ts, first_ts):
    """本批首行与库里上一条隔得太久时，写一条告警（**只提醒，不改任何计算**）。

    为什么不像采集那样直接改成"新起点"：导入数据的**粒度由用户决定** ——
    按月导入时相邻两行本来就隔 ~720 小时，按固定阈值一刀切会让整个序列永远算不出。
    而 MQTT 是自动连续采集，几十秒一条，长间隔一定是异常，所以那边可以直接重基线。
    这里只把事实摆出来（增量会横跨多大一段），由人判断该段是否已被核查过。
    """
    try:
        from .data_collector import _gap_hours, _gap_reset_hours, _raise_gap_alert
        gap_h = _gap_hours(prev_ts, first_ts)
        if gap_h is None or gap_h <= _gap_reset_hours():
            return
        conn = get_db()
        try:
            _raise_gap_alert(conn, project_id, machine.get("id"),
                             machine.get("name"), gap_h, prev_ts, first_ts)
        finally:
            conn.close()
    except Exception as e:
        print(f"[data_import] 断档提醒失败（不影响导入）: {e}")


def import_rows(project, rows, source="import", machine_id=None):
    """逐行计算减排量（直填/公式）并去重写入 monitoring_data。返回 (imported, skipped)。

    落库的每行都带 machine_id：去重、累计起点、增量起点全部**按机器**算 ——
    两台机器的累计读数是两套，混在一起算增量会得出天文数字。
    """
    machine = resolve_machine(project, machine_id)
    machine_id = machine["id"]
    formula, raw_fields = get_formula(machine, project.get("hook_name", ""))
    project_id = project["id"]
    validated = []
    for index, row in enumerate(rows, 1):
        row = dict(row)
        row["timestamp"] = normalize_timestamp(row.get("timestamp"))
        if not row["timestamp"]:
            raise ValueError(f"第{index}行时间无效")
        direct = row.get("carbon_reduction") not in (None, "")
        fields = ["carbon_reduction"] if direct else raw_fields
        for field in fields:
            try:
                number = float(row.get(field) or 0)
            except (TypeError, ValueError):
                raise ValueError(f"第{index}行 {field} 必须为有效数字") from None
            if not math.isfinite(number):
                raise ValueError(f"第{index}行 {field} 必须为有限数字")
        validated.append(row)
    rows = sorted(validated, key=lambda r: r["timestamp"])

    conn = get_db()
    imported = 0
    skipped = 0
    try:
        existing = {
            r[0] for r in conn.execute(
                "SELECT timestamp FROM monitoring_data WHERE machine_id = ? AND source = ?",
                (machine_id, source)).fetchall()
        }

        # 上一条记录（时间早于本批首行）的原始值与累计减排量，作为公式增量起点
        first_ts = rows[0]["timestamp"] if rows else None
        prev_raw = {}
        prev_cum = 0.0
        # 这台机器在库里有没有更早的记录？（没有 → 本批首行要当基线，见下）
        has_prev = False
        prev_ts = None
        if first_ts:
            prev = conn.execute(
                "SELECT timestamp, raw_data, cumulative_reduction FROM monitoring_data "
                "WHERE machine_id = ? AND timestamp < ? "
                "ORDER BY timestamp DESC, id DESC LIMIT 1",
                (machine_id, first_ts)).fetchone()
            if prev:
                has_prev = True
                prev_ts = prev["timestamp"]
                prev_cum = float(prev["cumulative_reduction"] or 0)
                # ── 断档提醒（**只提醒，不改计算**）──
                # 本批首行如果与库里上一条隔了很久，就意味着这一行"承接"了整段空档：
                # 增量 = 断档期间的全部产量。而这段很可能已经在别处核查过了
                # → 重复计算。这里不擅自改成基线，因为导入数据的**粒度是用户决定的**：
                # 按月导入时相邻两行本来就隔 ~720 小时，一刀切会让整个序列永远算不出东西。
                # 所以只写一条告警让人看见，由人判断这段该不该计入。
                _warn_if_boundary_gap(project_id, machine, prev_ts, first_ts)
                try:
                    prev_raw = json.loads(prev["raw_data"]) if prev["raw_data"] else {}
                    if not isinstance(prev_raw, dict):
                        prev_raw = {}
                except (json.JSONDecodeError, TypeError):
                    prev_raw = {}

        for row in rows:
            if row["timestamp"] in existing:
                skipped += 1
                continue

            raw_data = {}
            for f in raw_fields:
                try:
                    raw_data[f] = float(row.get(f) or 0)
                except (TypeError, ValueError):
                    raw_data[f] = 0.0

            value = row.get("carbon_reduction")
            cr_str = str(value).strip() if value not in (None, "") else ""
            if cr_str:
                # 直填模式
                try:
                    cr = float(cr_str)
                except (TypeError, ValueError):
                    cr = 0.0
                prev_cum += cr
                breakdown = dict(raw_data)
                breakdown["direct_amount"] = True
            elif not has_prev:
                # 该机器**库里一条更早的记录都没有** → 本批首行只作基线，不计减排量。
                # 累计读数本身不含"期间"：拿它减 0 等于把这个仪表有史以来的全部产量
                # 都记到本行，若仪表早就走过一大截（项目建在装表之前）会一次性虚增
                # 且永久留在累计里。基线行同样要落库，它是下一行的增量起点。
                cr = 0.0
                breakdown = {"baseline": True,
                             "note": "该机器首条读数作为基线，不计减排量"}
            else:
                # 公式模式：把上一条原始值映射为 prev_*，交给公式算增量
                cumulative_data = {"cumulative_reduction": prev_cum}
                for f in raw_fields:
                    suffix = f[11:] if f.startswith("cumulative_") else f
                    cumulative_data["prev_" + suffix] = prev_raw.get(f, 0.0)
                result = formula.calculate(raw_data, cumulative_data)
                cr = float(result.get("carbon_reduction", 0) or 0)
                prev_cum = float(result.get("cumulative_reduction", prev_cum + cr) or 0)
                breakdown = result.get("breakdown", {})

            conn.execute(
                """INSERT INTO monitoring_data
                   (project_id, machine_id, timestamp, raw_data, carbon_reduction,
                    cumulative_reduction, breakdown, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (project_id, machine_id, row["timestamp"],
                 json.dumps(raw_data, ensure_ascii=False),
                 round(cr, 6), round(prev_cum, 6),
                 json.dumps(breakdown, ensure_ascii=False), source)
            )
            existing.add(row["timestamp"])
            imported += 1
            prev_raw = raw_data
            has_prev = True

        from .reduction_ledger import refresh_totals
        refresh_totals(conn, project_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if imported:
        from .reduction_ledger import reconcile_machine
        reconcile_machine(machine_id, apply=True)
    return imported, skipped
