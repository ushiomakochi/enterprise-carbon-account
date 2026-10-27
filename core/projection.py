"""
项目十年逐年预估编排。

**统一口径（2026-09-18 定，与备案案例逐项验算一致）：**

计入期切成「首年段 + 中间整年 + 末年段」——

- **首年段** `[计入期起始日 A, A年12月31日]` = **实际产生的值（真实值）**，
  从 `monitoring_data` 汇总：减排量取 `carbon_reduction` 列，
  基准线排放/项目排放取 `breakdown` 里的 `baseline_emissions_tco2` / `project_emissions_tco2`。
- **其余各段都是预测值**，且一律按首年段折算：

      该段值 = 首年段值 ÷ 首年段天数 × 该段天数

  基准线、项目排放、减排量三者各算各的。

**这套逻辑贯穿封面 / 表 B.2 / 表 B.3 / 表 B.4 / 审定备案碳减排量，是同一个来源。**
备案案例（计入期 2022-05-13 起 10 年，首年段 233 天）验算：
193.03÷233×365 = 302.39、638.77÷233×365 = 1000.65、445.74÷233×365 = 698.26，
与案例表 B 完全一致。

**与生成报告的时间无关**：首年段是固定的 A ~ A年末，不会随时间漂移。
（旧实现按「报告表单填的区间天数」当分母，区间填成十年就被稀释 49 倍，
 出现封面「总减排量 30.61」而表 B.4「合计 61700.83」并存的矛盾。）

负责:
- build_credit_period_segments: 算分段（首年段实测 + 其余折算），并给出年均/总量
- compute_projection / get_projection: 同上，返回逐年行（供表 B.2/B.3/B.4 与封面共用）
- save_projection: 把逐年预估写回 carbon_projects.projection_10yr
"""

import json
from datetime import date, datetime, timedelta


def _parse_date(s):
    """把 YYYY-MM-DD / YYYY/MM/DD / YYYY年M月D日 解析成 date；失败返回 None。"""
    if isinstance(s, date):
        return s
    s = str(s or "").strip()
    if not s:
        return None
    import re
    m = re.fullmatch(r'(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})日?', s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _add_years(d, n):
    """加 n 年；2/29 落到非闰年时退到 2/28。"""
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def credit_period_bounds(project: dict, years: int = 10):
    """返回 (计入期起始日, 计入期末尾日)。起始日取 project.baseline_date。"""
    start = _parse_date(project.get("baseline_date"))
    if not start:
        return None, None
    return start, _add_years(start, years) - timedelta(days=1)


def _segment_bounds(start, end):
    """按自然年切段：首年段（可能不完整）+ 中间整年 + 末年段（可能不完整）。

    返回 [(label, start_date, end_date, calc_days)]。
    calc_days 是**折算用的天数**：
    - 首年段/末年段 = 实际日历天数（含首含尾）
    - 中间整年 = 固定 365（备案案例口径：2024、2028 是闰年也按 365 算，
      这样「年减排量 × 年数」就是十年总量，与案例表 B 一致）
    """
    segs = []
    cur = start
    while cur <= end:
        y = cur.year
        seg_end = min(date(y, 12, 31), end)
        is_full_year = (cur == date(y, 1, 1) and seg_end == date(y, 12, 31))
        calc_days = 365 if is_full_year else (seg_end - cur).days + 1
        segs.append((f"{y}年", cur, seg_end, calc_days))
        cur = seg_end + timedelta(days=1)
    return segs


def project_machines(project: dict) -> list:
    """取项目的机器列表（按 sort_order, id 排序）。查不到就返回空列表。"""
    try:
        from core.project_manager import get_project_machines
        pid = project.get("id")
        return get_project_machines(pid) if pid else []
    except Exception:
        return []


def _has_unassigned_rows(project_id) -> bool:
    """项目里是否还有 machine_id 为空的历史行（迁移漏网时才会为真）。"""
    if not project_id:
        return False
    from core.project_manager import get_db
    conn = get_db()
    try:
        return conn.execute(
            "SELECT 1 FROM monitoring_data WHERE project_id = ? AND machine_id IS NULL LIMIT 1",
            (project_id,)
        ).fetchone() is not None
    except Exception:
        return False
    finally:
        conn.close()


def _machine_groups(project: dict):
    """把项目切成若干「汇总分组」——每台机器一组，外加（万一有的）未分配组。

    返回 [(key, name, machine_id, unassigned_only)]：
      - 项目一台机器都没有 → 一个整项目分组（等价改造前的口径）
      - 有机器 → 每台一组；若还存在 machine_id IS NULL 的历史行，再补一组。
        少了这一组，那些行会在报告里凭空消失（机器数一变，数就对不上了）。
    """
    pid = project.get("id")
    machines = project_machines(project)
    if not machines:
        return [("__all__", "", None, False)]

    groups = [(m["id"], m.get("name") or f"机器{m['id']}", m["id"], False) for m in machines]
    if _has_unassigned_rows(pid):
        groups.append(("__unassigned__", "未分配", None, True))
    return groups


def _actual_totals(project_id, start, end, machine_id=None, unassigned_only=False):
    """汇总 [start, end] 区间内的三个实测值。

    返回 (baseline, project, reduction, records)；
    减排量取 carbon_reduction 列，基准线/项目排放取 breakdown 里的字段。

    machine_id 给定时只统计那台机器；unassigned_only 只统计 machine_id IS NULL 的行；
    两者都不给 = 整个项目（含未分配机器的历史行）。
    """
    if not project_id or not start or not end:
        return 0.0, 0.0, 0.0, 0
    where = "project_id = ? AND timestamp >= ? AND timestamp <= ?"
    params = [project_id, f"{start.isoformat()} 00:00:00", f"{end.isoformat()} 23:59:59"]
    if unassigned_only:
        where += " AND machine_id IS NULL"
    elif machine_id is not None:
        where += " AND machine_id = ?"
        params.append(machine_id)

    from core.project_manager import get_db
    conn = get_db()
    try:
        row = conn.execute(
            f"""SELECT COUNT(*) AS cnt,
                       COALESCE(SUM(carbon_reduction), 0) AS red,
                       COALESCE(SUM(json_extract(breakdown, '$.baseline_emissions_tco2')), 0) AS base,
                       COALESCE(SUM(json_extract(breakdown, '$.project_emissions_tco2')), 0) AS proj
                FROM monitoring_data
                WHERE {where}""",
            params,
        ).fetchone()
        if not row:
            return 0.0, 0.0, 0.0, 0
        return (float(row["base"] or 0), float(row["proj"] or 0),
                float(row["red"] or 0), int(row["cnt"] or 0))
    except Exception:
        return 0.0, 0.0, 0.0, 0
    finally:
        conn.close()


def build_credit_period_segments(project: dict, years: int = 10) -> dict:
    """算出计入期各段的预估（首年段实测 + 其余按首年段折算）。

    返回::

        {
          "rows": [{year, start, end, days, baseline_emission, project_emission,
                    reduction, is_actual, period_years}, ...],
          "period_years": 10,
          "first_segment_days": 233,
          "has_first_segment_data": True,
          "first_segment": {"start": "2022-05-13", "end": "2022-12-31"},
          "annual_reduction": 302.39,     # 首年段减排量 ÷ 首年段天数 × 365
          "total_reduction": 3023.89,     # 各段减排量之和
          "total_baseline": 10006.46,
          "total_project": 6982.58,
        }

    计入期起始日缺失、或首年段没有任何监测数据时，`rows` 仍返回分段骨架
    （值全为 0、`has_first_segment_data=False`），由调用方决定是提示还是留空。

    **多机器**：每台机器各自汇总首年段、各自按首年段折算，行内的 project 级
    三个值是各机器之和；每行的 `machines` 列出各机器这一年的值（报告表按列取）。
    单机器时结果与改造前**完全一致**（一个分组，和就是它自己）。
    """
    start, end = credit_period_bounds(project, years)
    if not start or not end:
        return {"rows": [], "period_years": years, "first_segment_days": 0,
                "has_first_segment_data": False, "first_segment": None,
                "annual_reduction": 0.0, "total_reduction": 0.0,
                "total_baseline": 0.0, "total_project": 0.0,
                "error": "项目缺少基准日期（计入期起始日），无法预估"}

    segs = _segment_bounds(start, end)
    pid = project.get("id")
    d1 = segs[0][3]

    # 每台机器各自汇总首年段实测值。汇总值**先各自 round 到 4 位**，
    # 项目行再由这些已 round 的值相加 —— 这样报告表里「合计列」
    # （各机器列显示值之和）与行内的项目值逐格对得上，不会差 0.01。
    groups = _machine_groups(project)
    first = {}       # key → 已 round 到 4 位的首年段值（供逐行相加）
    first_raw = {}   # key → 未 round 的原值（供项目级汇总，避免二次取整）
    for key, _name, mid, unassigned in groups:
        b1, p1, r1, cnt = _actual_totals(pid, segs[0][1], segs[0][2], mid, unassigned)
        first[key] = (round(b1, 4), round(p1, 4), round(r1, 4), cnt)
        first_raw[key] = (b1, p1, r1, cnt)
    has_data = any(v[3] > 0 for v in first.values())
    # 项目级首年段合计 —— 注意**别**直接用循环变量 r1/cnt：多机器时它们
    # 只剩最后一台的值（单机器时才是对的）。这里显式汇总。
    first_cnt_total = sum(v[3] for v in first_raw.values())
    first_r_total = sum(v[2] for v in first_raw.values())

    rows = []
    for i, (label, s, e, calc_days) in enumerate(segs):
        # 预测值 = 首年段值 ÷ 首年段天数 × 该段折算天数
        k = (calc_days / d1) if d1 else 0.0
        per_machine = []
        tb = tp = tr = 0.0
        for key, name, mid, _unassigned in groups:
            b1, p1, r1, cnt = first[key]
            if i == 0:
                b, p, r = b1, p1, r1
            else:
                b, p, r = round(b1 * k, 4), round(p1 * k, 4), round(r1 * k, 4)
            tb += b
            tp += p
            tr += r
            per_machine.append({
                "id": mid,
                "name": name,
                "baseline_emission": b,
                "project_emission": p,
                "reduction": r,
                "records": cnt,
            })
        rows.append({
            "year": i + 1,
            "label": label,
            "start": s.isoformat(),
            "end": e.isoformat(),
            "days": calc_days,          # 折算用天数（整年固定 365）
            "calendar_days": (e - s).days + 1,
            "baseline_emission": round(tb, 4),
            "project_emission": round(tp, 4),
            "reduction": round(tr, 4),
            "is_actual": i == 0,
            "period_years": years,
            # 每台机器这一年的值（报告表 B.2/B.3/B.4 多机器时一台一列）
            "machines": per_machine,
        })

    total_r = sum(x["reduction"] for x in rows)
    total_b = sum(x["baseline_emission"] for x in rows)
    total_p = sum(x["project_emission"] for x in rows)
    return {
        "rows": rows,
        "period_years": years,
        "first_segment_days": d1,
        "has_first_segment_data": has_data,
        "first_segment": {"start": segs[0][1].isoformat(), "end": segs[0][2].isoformat()},
        "first_segment_records": first_cnt_total,
        # 年均一律取「合计 ÷ 计入期年数」，保证 年均 × 年数 == 合计 ——
        # 这样封面「年均减排量」和表 B.4「计入期内年均值」是同一个数，
        # 审核的人拿年均乘年数就能对上合计。
        "annual_reduction": round(total_r / years, 4) if years else 0.0,
        "annual_baseline": round(total_b / years, 4) if years else 0.0,
        "annual_project": round(total_p / years, 4) if years else 0.0,
        # 首年段折算出来的「整年率」，仅供对照，不作为对外数字
        "first_segment_annual": round(first_r_total / d1 * 365, 4) if d1 else 0.0,
        "total_reduction": round(total_r, 4),
        "total_baseline": round(total_b, 4),
        "total_project": round(total_p, 4),
    }


def compute_projection(project: dict, years: int = 10) -> list:
    """按统一口径算十年逐年预估，返回 list[dict]（首年段实测 + 其余折算）。

    每项含 {year, start, end, days, baseline_emission, project_emission,
            reduction, is_actual, period_years}。
    """
    return build_credit_period_segments(project, years=years).get("rows") or []


def get_projection(project: dict, years: int = 10) -> list:
    """优先用已存 projection_10yr，否则实时计算。返回 list[dict]。

    ⚠️ **多机器（≥2 台）的项目不吃固化的那份**：projection_10yr 是「一项目一设备」
    时代存下来的单份逐年值，装不下「一台一列」。而且机器数一旦变了，当初固化
    的数本来也不再成立 —— 硬套会跟 B.2/B.3/B.4 的多机器表对不上。
    所以多机器一律按当前机器实时算；单机器维持原有「固化优先」语义不变。
    """
    stored = project.get("projection_10yr")
    if stored and len(project_machines(project)) < 2:
        try:
            rows = json.loads(stored) if isinstance(stored, str) else stored
            if isinstance(rows, list) and rows:
                return rows
        except Exception:
            pass
    return compute_projection(project, years=years)


def save_projection(project_id: int, rows: list) -> dict:
    """把逐年预估写回 carbon_projects.projection_10yr。"""
    from core.project_manager import get_db
    from datetime import datetime

    if not isinstance(rows, list):
        return {"status": "失败", "msg": "rows 必须是数组"}

    conn = get_db()
    try:
        conn.execute(
            "UPDATE carbon_projects SET projection_10yr = ?, updated_at = ? WHERE id = ?",
            (
                json.dumps(rows, ensure_ascii=False),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                project_id,
            ),
        )
        conn.commit()
        return {"status": "成功", "msg": "十年预估已保存"}
    except Exception as e:
        return {"status": "失败", "msg": str(e)}
    finally:
        conn.close()
