"""断档兜底 —— 空档期间若已被核查过，用「已核查量」顶掉，只补记未核查的部分。

**为什么需要它**

累计读数做差只能表达"一个正常采集间隔"。中间断了很久（项目换核查周期、
系统停机、设备离线数月）时，那条差值等于"断档期间的全部产量"，
而这段时间**很可能已经核查过了** —— 直接算进来就是重复计算，且永久留在累计里。

断档保护（`config.COLLECT_GAP_RESET_HOURS`）先把这条记 0：不重复算了，
但**未核查的那部分也一起丢了**。本模块补的就是丢掉的那部分：

    全段减排量 R_span = 空档起点读数 → 断档那条读数，按公式算
    已核查量 V        = 与空档期间**重叠**的「已通过」核查轮次核证量合计
    补记量            = R_span − V          ← 记在断档那条行上

**为什么能直接相减**：公式对读数差是**线性可加**的
（`Δ流量(全段) = Δ流量(已核查段) + Δ流量(待算段)`），所以减排量也直接减。
促进会只做审核、不另算一套，所以两边同口径。

**安全规则（重要）**：只有在**成功拿到核查记录**时才敢做相减。
拿不到（没配密钥 / 接口不通 / 返回异常）就退回"只当新起点"——
宁可少算也不重复算；少算的那部分可以用 `tools/bridge_gap.py` 手工补。

采集路径（`core/data_collector.py`）和命令行工具（`tools/bridge_gap.py`）
都走这里，别再写第二份。
"""

import json
import logging
import os
import shutil
from datetime import date, timedelta
from datetime import datetime as _dt

log = logging.getLogger("collector")


def _norm_date(s):
    """取 YYYY-MM-DD。"""
    return str(s or "").strip().replace("/", "-")[:10]


def _as_date(s):
    """YYYY-MM-DD → date；解析不出来返回 None。"""
    try:
        return date.fromisoformat(_norm_date(s))
    except (TypeError, ValueError):
        return None


def fetch_verified_rounds(project: dict):
    """拉该项目的**已通过**核查轮次。

    返回 (rounds, err)：rounds = [{round, start, end, amount, agency}]；
    err 非空表示**拿到的数据不可用**，调用方据此决定"不敢相减"（退回新起点记 0）。

    ⚠ err 的语义是「不可用」，不只是「调用失败」。下面三种都算不可用，
    因为它们都会让"该扣多少"算不出来，而**算不出来时绝不能当成 v=0 把整段补记** ——
    那正是重复计算。宁可少算（记 0），要计入就用 tools/bridge_gap.py --verified 手工补：

      1. 接口调不通 / 明确失败
      2. 返回里 `rounds` 不是列表，或**是空列表但响应明确表示失败**
      3. 已通过的轮次里**有任何一轮缺起止日期** —— 缺日期就没法判断它跟空档重不重叠

    来源：促进会 `/cn/api/vcer/stage_rounds?stage=3` —— 与前端「VCER」页签同一接口。
    状态白名单统一走 core.vcer_client.is_passed_status（与报告编号共用一份）。
    """
    key = (project or {}).get("vcer_query_key")
    if not key:
        return [], "项目没有配促进会查询密钥（vcer_query_key）"
    try:
        import config
        from core.vcer_client import VCERClient, is_passed_status
        resp = VCERClient(config.VCER_API_URL).get_stage_rounds(key, 3)
    except Exception as e:
        return [], f"调用促进会接口失败: {e}"

    if not isinstance(resp, dict):
        return [], f"促进会返回格式异常（{type(resp).__name__}），不敢按核查记录相减"

    ok_flag = bool(resp.get("success")) or str(resp.get("status") or "") == "成功"
    msg = resp.get("message") or resp.get("msg") or ""
    if not ok_flag:
        # 明确失败：即使它顺手带了一个空 rounds，也绝不能当成"确实没核查过"
        return [], f"促进会返回失败{('：' + str(msg)) if msg else ''}，不敢按核查记录相减"

    rounds = resp.get("rounds")
    if not isinstance(rounds, list):
        return [], f"促进会未返回核查轮次{('：' + str(msg)) if msg else '（返回格式异常）'}"

    out = []
    for r in rounds:
        if not isinstance(r, dict):
            continue
        if not is_passed_status(r.get("status")):
            continue
        try:
            amt = float(r.get("verified_reduction") or 0)
        except (TypeError, ValueError):
            continue
        start, end = _norm_date(r.get("verify_start_date")), _norm_date(r.get("verify_end_date"))
        if not _as_date(start) or not _as_date(end):
            # 缺起止日期 → 没法判断重不重叠。当 0 算就是重复计算，所以整批判不可用。
            return [], (f"第 {r.get('round')} 轮已通过核查缺少起止日期"
                        f"（verify_start_date={r.get('verify_start_date')!r}, "
                        f"verify_end_date={r.get('verify_end_date')!r}），"
                        f"无法判断与空档是否重叠，不敢相减")
        out.append({
            "round": r.get("round"),
            "start": start,
            "end": end,
            "amount": amt,
            "agency": r.get("agency_name") or "",
        })
    return out, None


def overlapping_rounds(rounds, span_start, span_end):
    """挑出与 [span_start, span_end] 有重叠的轮次（日期都是 YYYY-MM-DD，可直接比字符串）。

    注意：只要**沾到**就算命中。命中之后扣多少由 prorated_verified 决定 ——
    不能拿命中就直接扣整轮的核证量，见那里的说明。
    """
    ss, se = _norm_date(span_start), _norm_date(span_end)
    hit = []
    for r in rounds or []:
        rs, re_ = r.get("start") or "", r.get("end") or ""
        if not rs or not re_:
            continue
        if rs <= se and re_ >= ss:
            hit.append(r)
    return hit


def overlap_days(r, span_start, span_end):
    """该轮覆盖的日期里，有多少天落在空档内（闭区间，按天算）。"""
    rs, re_ = _as_date(r.get("start")), _as_date(r.get("end"))
    ss, se = _as_date(span_start), _as_date(span_end)
    if not (rs and re_ and ss and se):
        return 0
    a, b = max(rs, ss), min(re_, se)
    return (b - a).days + 1 if b >= a else 0


def prorated_verified(rounds, span_start, span_end):
    """算出该扣多少**已核查量** —— 按「落在空档内的天数 ÷ 该轮总天数」摊。

    返回 (v, detail)：detail 每项 {round, amount, days, total_days, ratio, prorated}，供对账。

    **为什么必须摊**：空档起点是"设备最后一条读数"，落在哪天是随机的。
    只按"沾到就算"整额扣，会出这种事：
        空档 2024-12-31 → 2026-09-24
        2024 轮覆盖 2024 全年（核证 30），但只有 12-31 这 1 天在空档内 → 却扣掉整 30
        2026 轮覆盖 2026 全年（核证 30），只有 267/365 天在空档内      → 却扣掉整 30
      结果全段 67.1 被扣成 60，应入账 45.07 只入账 7.1，少记 38；
      再极端些 booked ≤ 0，整段直接被丢掉、还报"已被核查量覆盖"。
    """
    ss, se = _norm_date(span_start), _norm_date(span_end)
    v, detail = 0.0, []
    for r in overlapping_rounds(rounds, ss, se):
        rs, re_ = _as_date(r.get("start")), _as_date(r.get("end"))
        if not (rs and re_):
            continue
        total_days = (re_ - rs).days + 1
        days = overlap_days(r, ss, se)
        if total_days <= 0 or days <= 0:
            continue
        ratio = min(1.0, days / total_days)
        amt = float(r.get("amount") or 0)
        part = round(amt * ratio, 6)
        v += part
        detail.append({"round": r.get("round"), "amount": amt,
                       "start": r.get("start"), "end": r.get("end"),
                       "days": days, "total_days": total_days,
                       "ratio": round(ratio, 6), "prorated": part})
    return round(v, 6), detail


def scale_breakdown(bd, ratio):
    """把基准线/项目排放按比例摊，保证「基准线 − 项目 = 减排量」仍然成立。

    核查记录只给"减排量"总额，没有基准线/项目排放的拆分，所以只能按比例摊。
    不摊（像直填模式那样留空）的话表 B.2/B.3 会变 0、和 B.4 对不平。
    """
    _SCALE_KEYS = ("baseline_emissions_tco2", "project_emissions_tco2",
                   "delta_flow_m3", "delta_power_kwh", "delta_heat_mj",
                   "delta_energy_kwh", "delta_steam_tons", "delta_generation_kwh",
                   "delta_heat_gj", "delta_hours", "delta_electricity_kwh",
                   "delta_heat_recovered_mj", "delta_heat_recovered_gj")
    out = dict(bd or {})
    for k in _SCALE_KEYS:
        try:
            if bd.get(k) is not None:
                out[k] = round(float(bd[k]) * ratio, 6)
        except (TypeError, ValueError):
            pass
    return out


def bridge_decision(project: dict, machine: dict, prev_row, cur_raw: dict,
                    cur_ts: str, formula, raw_fields, verified_manual=None) -> dict:
    """算断档这条读数该怎么记。**不写库**，只给结论。

    这是断档补记**唯一**的口径实现：采集器自动兜底、命令行工具、界面接口都走它。
    别在别处再写一份 —— 两边一分叉就会算出两个数。

    参数 ``verified_manual``：手工指定的「已核查量」，给了就**跳过促进会接口**
    （等价命令行工具的 ``--verified``）。用于拉不到核查记录、或已知平台数据不对时。

    返回::

        {
          "mode": "bridge" | "baseline",
          "booked": float,          # 要记的减排量（baseline 时为 0）
          "breakdown": {...},       # 要写进 breakdown 的内容
          "r_span": float,          # 全段减排量
          "verified": float,        # 扣掉的已核查量
          "verified_source": str,   # 'api' | 'manual'
          "matched": [...],         # 命中的核查轮次（对账用）
          "matched_detail": [...],  # 逐轮摊分明细
          "reason": str,            # 给告警/日志用的一句话
        }
    """
    out = {"mode": "baseline", "booked": 0.0, "breakdown": {}, "r_span": 0.0,
           "verified": 0.0, "verified_source": "api", "matched": [],
           "matched_detail": [], "reason": ""}

    # ── 1) 全段减排量（用空档两头的原始读数，按公式算）──
    try:
        prev_raw = json.loads(prev_row["raw_data"] or "{}") if prev_row else {}
        if not isinstance(prev_raw, dict):
            prev_raw = {}
    except (json.JSONDecodeError, TypeError):
        prev_raw = {}

    # 起点缺字段必须**拒绝桥接**，不能当 0。
    # 少了 cumulative_* 就等于"起点读数是 0"，算出来的全段减排量是
    # 这块表**装上去以来的全部产量**，然后会被原样写进库并永久留在累计里。
    # 常见的触发：中途换过公式 / 字段改过名 / 起点那行来自直填模式。
    missing = [f for f in (raw_fields or [])
               if f not in prev_raw or prev_raw.get(f) is None]
    if missing:
        out["reason"] = (f"空档起点那条读数的原始数据缺少字段 {missing}，"
                         f"当成 0 会把仪表历史产量全算进这一段，故本条只作新起点；"
                         f"若该段确需计入，用 tools/bridge_gap.py --verified 手工补")
        return out

    cumulative_data = {"cumulative_reduction": 0.0}
    for f in (raw_fields or []):
        suffix = f[11:] if f.startswith("cumulative_") else f
        try:
            cumulative_data["prev_" + suffix] = float(prev_raw.get(f) or 0)
        except (TypeError, ValueError):
            out["reason"] = (f"空档起点原始读数 {f}={prev_raw.get(f)!r} 不是数字，"
                             f"本条只作新起点")
            return out
    try:
        result = formula.calculate(cur_raw, cumulative_data)
    except Exception as e:
        out["reason"] = f"按公式算全段减排量失败（{e}），本条只作新起点"
        return out

    r_span = float(result.get("carbon_reduction") or 0)
    out["r_span"] = r_span
    if r_span <= 0:
        out["reason"] = ("全段读数差算不出正减排量，本条只作新起点"
                         "（断档期间的产量未计入）")
        return out

    # ── 2) 已核查量（拿不到就**不敢相减**，退回新起点）──
    if verified_manual is not None:
        # 手工指定：跳过促进会接口。摊分明细留空，调用方靠 verified_source 区分。
        try:
            v = float(verified_manual)
        except (TypeError, ValueError):
            out["reason"] = f"手工指定的已核查量 {verified_manual!r} 不是数字，本条只作新起点"
            return out
        if v < 0:
            out["reason"] = f"手工指定的已核查量 {v} 为负，本条只作新起点"
            return out
        matched, vdetail = [], []
        out["verified_source"] = "manual"
    else:
        rounds, err = fetch_verified_rounds(project)
        if err:
            out["reason"] = (f"取不到核查记录（{err}），本条只作新起点以免重复计算；"
                             f"若该段确实需要计入，可手工指定该段的已核查量后重试")
            return out
        # 按重叠天数比例扣，不是"沾到就扣整轮"（见 prorated_verified 的说明）
        matched = overlapping_rounds(rounds, prev_row["timestamp"], cur_ts)
        v, vdetail = prorated_verified(rounds, prev_row["timestamp"], cur_ts)
        if not vdetail:
            # 一笔都摊不出来 = 空档真的不在任何已核查期间内 → 整段都是未核查，全额补记，正确
            log.info("[断档兜底] 空档 %s → %s 不与任何已通过核查重叠，整段补记",
                     prev_row["timestamp"], cur_ts)

    out["verified"] = v
    out["matched"] = matched
    out["matched_detail"] = vdetail

    # ── 3) 补记量 ──
    booked = round(r_span - v, 6)
    if booked <= 0:
        out["reason"] = (f"空档期间已被核查量覆盖（全段 {r_span:.4f} ≤ 已核查 {v:.4f}），"
                         f"本条作新起点，无需补记")
        return out

    ratio = booked / r_span if r_span else 0.0
    bd = scale_breakdown(result.get("breakdown") or {}, ratio)
    bd["bridge"] = True
    bd["bridge_note"] = (f"断档兜底自动补记：全段 {r_span:.4f} − 已核查 {v:.4f} = {booked:.4f}；"
                         f"基准线/项目排放按比例摊自全段")
    bd["bridge_verified"] = round(v, 6)
    bd["bridge_span"] = round(r_span, 6)
    bd["bridge_rounds"] = [x.get("round") for x in matched]
    # 逐轮摊分明细：只按"沾到就扣整轮"扣的时代，这一项能一眼看出问题
    bd["bridge_matched_detail"] = vdetail

    out.update({
        "mode": "bridge",
        "booked": booked,
        "breakdown": bd,
        "reason": (f"断档期间与 {len(matched)} 轮已通过核查重叠，"
                   f"按重叠天数摊出的已核查量合计 {v:.4f}；按「全段 {r_span:.4f} − 已核查 {v:.4f}」"
                   f"自动补记 {booked:.4f} tCO₂e"),
    })
    return out


# ══════════════════════════════════════════════════════════
# 断档识别（从 tools/bridge_gap.py 搬过来，供界面接口复用）
# ══════════════════════════════════════════════════════════

def bd_of(row):
    """取一行的 breakdown 字典（解析不出来给空字典）。"""
    try:
        bd = json.loads(row["breakdown"]) if isinstance(row["breakdown"], str) else (row["breakdown"] or {})
    except (json.JSONDecodeError, TypeError):
        return {}
    return bd if isinstance(bd, dict) else {}


def is_gap_row(breakdown_raw):
    """这行是不是断档重基线产生的（含**已经兜底过**的）。

    三种都算，且互不冲突：
      · gap_reset=True   —— 断档保护刚写下、还没兜底
      · bridge=True      —— 已经兜底补记过（写回时保留了原来的 note）
      · 早期写法        —— 没有标记、只有"断档"字样的 note
    认「已兜底」的行，是为了能**重跑/更正**（比如已核查量填错了要重算）。
    """
    try:
        bd = json.loads(breakdown_raw) if isinstance(breakdown_raw, str) else (breakdown_raw or {})
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(bd, dict):
        return False
    # 已确认的历史补记仍保留可追溯入口；新版账本处理过的行不再按旧标记重复补记。
    if bd.get("bridge"):
        return True
    if any(bd.get(key) for key in (
            "sampling_suppressed", "excluded_invalid_reading", "reconciled",
            "continuity_confirmed", "reset_boundary", "meter_review", "boundary_review")):
        return False
    if bd.get("gap_reset"):
        return True
    note = str(bd.get("note") or "") + str(bd.get("gap_note") or "")
    return bd.get("baseline") is True and "断档" in note


_GAP_ROW_COLS = ("id, timestamp, raw_data, carbon_reduction, cumulative_reduction, "
                 "breakdown, source")


def _prev_row_of(conn, machine_id, row):
    """该行在同机器里紧邻的前一条（按 timestamp, id 排序），没有则 None。

    等价于"把整台机器的行按序排好之后取 i-1 那条"，但只查一条。
    """
    return conn.execute(
        f"""SELECT {_GAP_ROW_COLS} FROM monitoring_data
            WHERE machine_id = ?
              AND (timestamp < ? OR (timestamp = ? AND id < ?))
            ORDER BY timestamp DESC, id DESC LIMIT 1""",
        (machine_id, row["timestamp"], row["timestamp"], row["id"])).fetchone()


def find_gaps(conn, machine_id):
    """列出该机器的所有断档：每条含断档行本身，以及它前面那一条（空档起点）。

    返回 [{gap_row, prev_row, span_hours, bridged}]，按时间倒序。

    ⚠ 性能：**不要**先把这台机器的行全读进 Python 再逐行判（早先就是这么写的）。
    生产库里单台机器能到 9 万多行，每次进「项目管理」页都会卡好几秒。
    这里先用 SQL 粗筛出**可能**是断档的行（是个超集），再逐条：
      · 用 is_gap_row 做精确判定（权威标准还是 Python 这个函数，SQL 只负责少读点）
      · 用 _prev_row_of 只查它前面那一条，而不是把整列都捞出来
    """
    # 粗筛条件必须覆盖 is_gap_row 认的三种写法（gap_reset / bridge / 早期只有"断档"字样的 note），
    # 漏掉任何一种都会让那条断档在界面上凭空消失。
    rows = conn.execute(
        f"""SELECT {_GAP_ROW_COLS} FROM monitoring_data
            WHERE machine_id = ?
              AND (breakdown LIKE '%gap_reset%'
                   OR breakdown LIKE '%"bridge"%'
                   OR breakdown LIKE '%断档%')
            ORDER BY timestamp, id""",
        (machine_id,)).fetchall()

    gaps = []
    fmt = "%Y-%m-%d %H:%M:%S"
    for r in rows:
        r = dict(r)
        if not is_gap_row(r["breakdown"]):      # 精确判定
            continue
        prev = _prev_row_of(conn, machine_id, r)
        prev = dict(prev) if prev else None
        span_h = None
        if prev:
            try:
                span_h = (_dt.strptime(r["timestamp"][:19], fmt)
                          - _dt.strptime(prev["timestamp"][:19], fmt)).total_seconds() / 3600
            except (ValueError, TypeError):
                pass
        gaps.append({"gap_row": r, "prev_row": prev, "span_hours": span_h,
                     "bridged": bd_of(r).get("bridge") is True})
    return list(reversed(gaps))


def pick_gap(gaps, at=None, latest=False):
    """从 find_gaps 的结果里挑一条。返回 (gap, err)。"""
    if not gaps:
        return None, "该机器没有任何断档记录（没有需要兜底的空档）"
    if at:
        for g in gaps:
            if str(g["gap_row"]["timestamp"])[:19] == str(at)[:19]:
                return g, None
        return None, (f"没找到时间戳为 {at} 的断档：\n"
                      + "\n".join(f"    {g['gap_row']['timestamp']}" for g in gaps))
    if len(gaps) == 1:
        return gaps[0], None
    if latest:
        return gaps[0], None
    return None, ("该机器有多次断档，请指定其中一次：\n"
                  + "\n".join(f"    {g['gap_row']['timestamp']}  （空档 {g['span_hours']:.0f} 小时）"
                              for g in gaps))


def backup_db():
    """写库前整库备份到 data/backups/。返回备份路径，没得备份返回 None。"""
    import config
    src = config.DATABASE_PATH
    if not src or not os.path.exists(src):
        return None
    bdir = os.path.join(os.path.dirname(src), "backups")
    os.makedirs(bdir, exist_ok=True)
    dst = os.path.join(bdir, f"carbon_account_{_dt.now().strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copyfile(src, dst)
    return dst


# ══════════════════════════════════════════════════════════
# 界面用：预览 / 执行
# ══════════════════════════════════════════════════════════

def _steps_text(project, machine, prev_row, gap_row, span_hours):
    return [
        f"① 定位：项目「{project.get('project_name') or project.get('project_code') or project.get('id')}」"
        f"的机器 #{machine.get('id')}「{machine.get('name')}」",
        f"② 取空档：{prev_row['timestamp'] if prev_row else '（无起点）'} → "
        f"{gap_row['timestamp']}（{span_hours:.1f} 小时 ≈ {span_hours / 24:.1f} 天）",
        "③ 算全段：按公式对空档两头的原始读数做差，得到这一整段的减排量",
        "④ 扣已核查：从促进会拉「已通过」的核查轮次，按与空档的重叠天数摊出该扣多少",
        "⑤ 得补记：全段 − 已核查 = 这段里尚未被核查覆盖的部分",
        "⑥ 写回：只改「断档行」这一行；写库前自动备份整库",
        "⑦ 刷累计：按各机器最新累计重算项目累计（累计 = 断档前累计 + 本次补记）",
        "⑧ 留痕：写一条告警；行上打 bridge 标记，报告里能看出这是兜底补记、不是原始采集",
    ]


def preview_bridge(project: dict, machine: dict, gap: dict, formula, raw_fields,
                   verified_manual=None) -> dict:
    """给界面用的**干跑**：算出这次兜底会做什么。**绝不写库**。

    返回结构见 docstring —— 界面直接拿去渲染确认框：
    计算结果（①全段 ②已核查 ③补记）、替换结果（④before/after）、流程（⑤steps）。
    """
    gap_row, prev_row = gap.get("gap_row") or {}, gap.get("prev_row")
    span_hours = gap.get("span_hours") or 0.0
    out = {
        "ok": False, "error": "", "reason": "",
        "gap_row_id": gap_row.get("id"),
        "machine_id": machine.get("id"), "machine_name": machine.get("name"),
        "project_id": project.get("id"),
        "project_name": project.get("project_name"),
        "project_code": project.get("project_code"),
        "already_bridged": bool(gap.get("bridged")),
        "gap_timestamp": gap_row.get("timestamp"),
        "prev_timestamp": prev_row["timestamp"] if prev_row else None,
        "span_hours": round(span_hours, 1),
        "steps": _steps_text(project, machine, prev_row, gap_row, span_hours),
        "r_span": 0.0, "verified": 0.0, "verified_source": "api",
        "booked": 0.0, "rounds_detail": [], "breakdown": {},
        "before": {}, "after": {}, "warnings": [],
    }

    if not prev_row:
        out["error"] = "这次断档是该机器的第一条读数，前面没有起点读数，算不出空档期间的量。"
        return out

    cur_raw = json.loads(gap_row.get("raw_data") or "{}") if isinstance(gap_row.get("raw_data"), str) \
        else (gap_row.get("raw_data") or {})
    decision = bridge_decision(project, machine, prev_row, cur_raw, gap_row["timestamp"],
                               formula, raw_fields, verified_manual=verified_manual)

    out["r_span"] = round(float(decision.get("r_span") or 0), 6)
    out["verified"] = round(float(decision.get("verified") or 0), 6)
    out["verified_source"] = decision.get("verified_source") or "api"
    out["reason"] = decision.get("reason") or ""
    out["breakdown"] = decision.get("breakdown") or {}
    out["rounds_detail"] = decision.get("matched_detail") or []

    # 替换前：断档行当前的样子
    prev_cum = float((prev_row.get("cumulative_reduction") if isinstance(prev_row, dict)
                      else prev_row["cumulative_reduction"]) or 0)
    old_bd = bd_of(gap_row)
    out["before"] = {
        "carbon_reduction": gap_row.get("carbon_reduction"),
        "cumulative_reduction": gap_row.get("cumulative_reduction"),
        "note": old_bd.get("gap_note") or old_bd.get("note") or "",
    }

    if decision.get("mode") != "bridge":
        # 不能兜：把原因原样给界面（取不到核查记录 / 缺字段 / 全段 ≤ 0 / 补记量 ≤ 0 等）
        out["error"] = decision.get("reason") or "这段空档不需要补记"
        out["after"] = out["before"]
        return out

    booked = round(float(decision.get("booked") or 0), 6)
    bd = decision.get("breakdown") or {}
    out["ok"] = True
    out["booked"] = booked
    out["after"] = {
        "carbon_reduction": booked,
        "cumulative_reduction": round(prev_cum + booked, 6),
        "baseline_emissions_tco2": bd.get("baseline_emissions_tco2"),
        "project_emissions_tco2": bd.get("project_emissions_tco2"),
        "bridge_marked": True,
    }
    if gap.get("bridged"):
        out["warnings"].append("这次断档**之前已经兜底过**，确认后会按当前已核查量重新计算并覆盖原值。")
    if out["verified_source"] == "manual":
        out["warnings"].append("已核查量是**手工指定**的，没有走促进会接口。")
    return out


def apply_bridge(conn, project: dict, machine: dict, gap: dict, formula, raw_fields,
                 verified_manual=None, do_backup=True) -> dict:
    """执行一次兜底：**重新算一遍**再写库（防止预览之后数据变了）。

    只改「断档行」这一行，然后刷新项目累计。返回 preview_bridge 的结果 + 实际写入值。
    """
    # 重算：不复用预览结果，避免"预览时看到 A、执行时写进去 B"
    out = preview_bridge(project, machine, gap, formula, raw_fields,
                         verified_manual=verified_manual)
    if not out.get("ok"):
        return out

    gap_row = gap["gap_row"]
    prev_row = gap["prev_row"]
    booked = float(out["booked"])
    prev_cum = float(prev_row["cumulative_reduction"] or 0)
    cumulative = round(prev_cum + booked, 6)

    bd = dict(out["breakdown"] or {})
    # 保留断档保护当时写下的说明 —— 覆盖掉的话就再也看不出"这里断过档"了，
    # 而 is_gap_row 认的就是它，重跑/更正都要靠它。
    old = bd_of(gap_row)
    old_note = old.get("gap_note") or old.get("note")
    if old_note:
        bd["gap_note"] = old_note
    bd["cumulative_note"] = (f"本行累计 = 断档前累计 {prev_cum:.6f} "
                             f"+ 补记 {booked:.6f} = {cumulative:.6f}")
    if out["verified_source"] == "manual":
        bd["bridge_verified_source"] = "manual"

    out["backup_path"] = backup_db() if do_backup else None

    conn.execute(
        """UPDATE monitoring_data
           SET carbon_reduction = ?, cumulative_reduction = ?, breakdown = ?
           WHERE id = ?""",
        (booked, cumulative, json.dumps(bd, ensure_ascii=False), gap_row["id"]))

    from .reduction_ledger import reconcile_machine, refresh_totals
    conn.commit()
    reconcile_machine(machine["id"], apply=True, conn=conn)
    total = refresh_totals(conn, project["id"])
    corrected = conn.execute(
        "SELECT cumulative_reduction FROM monitoring_data WHERE id=?", (gap_row["id"],)).fetchone()
    cumulative = float(corrected["cumulative_reduction"]) if corrected else cumulative
    conn.commit()

    # 留痕：这是一次人工确认的账面调整，必须能追溯
    try:
        conn.execute(
            """INSERT INTO alert_log (rule_id, project_id, alert_message)
               VALUES (NULL, ?, ?)""",
            (project["id"],
             f"断档兜底已执行：机器「{machine.get('name')}」"
             f"{prev_row['timestamp']} → {gap_row['timestamp']}，"
             f"全段 {out['r_span']:.4f} − 已核查 {out['verified']:.4f} "
             f"= 补记 {booked:.4f} tCO₂e"))
    except Exception as e:  # noqa: BLE001 — 留痕失败不该让已完成的补记回滚
        log.warning("[断档兜底] 写告警失败（不影响补记）: %s", e)

    conn.commit()
    out["written"] = {"carbon_reduction": booked, "cumulative_reduction": cumulative,
                      "project_cumulative": total}
    return out
