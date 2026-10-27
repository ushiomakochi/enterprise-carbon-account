"""Monitoring ledger and verification-period accounting.

A monitoring row covers (previous reading, current reading].  Reconciliation is
previewable; callers must explicitly apply it.  Original readings are immutable.
"""
import json
from datetime import date, datetime, timedelta

from .project_manager import get_db, get_project, project_cumulative


def _json(value):
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _covered(start, end, rounds):
    """True/False for a whole interval; None when it crosses coverage."""
    if not start or not end:
        return False
    lo, hi = _date(start), _date(end)
    if str(end)[11:19] == "00:00:00" and hi:
        hi -= timedelta(days=1)
    if not lo or not hi or hi < lo:
        return False
    state = None
    day = lo
    while day <= hi:
        covered = any(r["start"] <= day.isoformat() <= r["end"] for r in rounds)
        if state is not None and covered != state:
            return None
        state = covered
        day += timedelta(days=1)
    return bool(state)


def pending_from_rows(conn, project_id, rounds):
    baseline = conn.execute("SELECT baseline_date FROM carbon_projects WHERE id=?",
                            (project_id,)).fetchone()
    baseline_day = _date(baseline["baseline_date"]) if baseline else None
    rows = conn.execute(
        "SELECT machine_id,timestamp,carbon_reduction,breakdown FROM monitoring_data "
        "WHERE project_id=? ORDER BY machine_id,timestamp,id", (project_id,)).fetchall()
    total, warnings, prev = 0.0, [], {}
    for row in rows:
        machine = row["machine_id"]
        prior = prev.get(machine)
        prev[machine] = row["timestamp"]
        if baseline_day and _date(row["timestamp"]) < baseline_day:
            continue
        if baseline_day and prior and _date(prior) and _date(prior) < baseline_day:
            warnings.append(f"{prior}—{row['timestamp']} 跨项目基准日，需补边界读数")
            continue
        bd = _json(row["breakdown"])
        if prior is None and float(row["carbon_reduction"] or 0) > 0:
            warnings.append(f"{row['timestamp']} 首条有减排量但缺少区间起点，待核对")
            continue
        if bd.get("meter_review") or bd.get("boundary_review"):
            warnings.append(f"{row['timestamp']} 设备 {machine} 读数待核对")
            continue
        if bd.get("coverage_gap"):
            warnings.append(f"{row['timestamp']} 设备 {machine} 存在监测数据缺口，需核实设备状态")
        long_interval=False
        if prior:
            try: long_interval=(datetime.fromisoformat(str(row["timestamp"]))-datetime.fromisoformat(str(prior))).total_seconds()>36*3600
            except ValueError: pass
        if bd.get("daily_close") and long_interval:
            lo,hi=_date(prior),_date(row["timestamp"])
            states=set()
            day=lo+timedelta(days=1)
            while day<=hi:
                states.add(any(r["start"]<=day.isoformat()<=r["end"] for r in rounds))
                day+=timedelta(days=1)
            state=next(iter(states)) if len(states)==1 else None
        elif bd.get("daily_close"):
            day = str(row["timestamp"])[:10]
            state = any(r["start"] <= day <= r["end"] for r in rounds)
        else:
            state = _covered(prior, row["timestamp"], rounds)
        if state is None:
            warnings.append(f"{prior}—{row['timestamp']} 跨核查边界，需补边界读数")
        elif not state:
            total += float(row["carbon_reduction"] or 0)
    # Explicit warning for gaps between approved verification periods.
    periods = sorted(rounds, key=lambda r: r["start"])
    for left, right in zip(periods, periods[1:]):
        a, b = _date(left["end"]), _date(right["start"])
        if a and b and (b-a).days > 1:
            warnings.append(f"{(a+timedelta(days=1)).isoformat()}—"
                            f"{(b-timedelta(days=1)).isoformat()} 尚未核查")
    return round(total, 6), list(dict.fromkeys(warnings))


def refresh_totals(conn, project_id):
    """Store lifetime separately; pending requires a confirmed rounds snapshot."""
    lifetime = project_cumulative(conn, project_id)
    snap = conn.execute("SELECT rounds_json,status FROM verification_snapshots "
                        "WHERE project_id=?", (project_id,)).fetchone()
    if snap and snap["status"] == "confirmed":
        rounds = json.loads(snap["rounds_json"])
        pending, warnings = pending_from_rows(conn, project_id, rounds)
        conn.execute("UPDATE carbon_projects SET lifetime_reduction=?, "
                     "pending_verification_cumulative=?, pending_status='confirmed', "
                     "pending_warnings=?, updated_at=datetime('now','localtime') WHERE id=?",
                     (lifetime, pending, json.dumps(warnings, ensure_ascii=False), project_id))
    else:
        # Keep last confirmed pending value while the remote source is unavailable.
        conn.execute("UPDATE carbon_projects SET lifetime_reduction=?, "
                     "updated_at=datetime('now','localtime') WHERE id=?",
                     (lifetime, project_id))
    return lifetime


def sync_verification(project_id):
    """Fetch approved stage-3 periods and atomically replace the local snapshot."""
    from .gap_bridge import fetch_verified_rounds
    project = get_project(project_id)
    if not project:
        raise ValueError("项目不存在")
    rounds, error = fetch_verified_rounds(project)
    if not error and project.get("stage3_passed") and not rounds:
        error = "促进会显示核查阶段已通过，但未返回任何已通过轮次"
    conn = get_db()
    try:
        if error:
            existing = conn.execute("SELECT 1 FROM verification_snapshots WHERE project_id=?",
                                    (project_id,)).fetchone()
            conn.execute("UPDATE carbon_projects SET pending_status=?, "
                         "pending_warnings=? WHERE id=?",
                         ("stale" if existing else "unknown",
                          json.dumps([error], ensure_ascii=False), project_id))
            if existing:
                conn.execute("UPDATE verification_snapshots SET status='stale', error=? "
                             "WHERE project_id=?", (error, project_id))
            conn.commit()
            return {"status": "待核对", "error": error}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO verification_snapshots "
                     "(project_id,rounds_json,synced_at,status,error) VALUES (?,?,?,'confirmed','') "
                     "ON CONFLICT(project_id) DO UPDATE SET rounds_json=excluded.rounds_json,"
                     "synced_at=excluded.synced_at,status='confirmed',error=''",
                     (project_id, json.dumps(rounds, ensure_ascii=False), now))
        refresh_totals(conn, project_id)
        conn.commit()
        return {"status": "成功", "rounds": rounds}
    finally:
        conn.close()


def _reconcile_rows(conn, machine_id, quality_overrides=None):
    """Calculate changes in memory, preserving direct-entry and booked bridge amounts."""
    from .carbon_calculator import CarbonCalculator
    from .project_manager import get_machine
    import config
    machine = get_machine(machine_id)
    project = get_project(machine["project_id"])
    rows = conn.execute(
        "SELECT id,timestamp,raw_data,carbon_reduction,cumulative_reduction,breakdown,source "
        "FROM monitoring_data WHERE machine_id=? ORDER BY timestamp,id", (machine_id,)).fetchall()
    if not rows:
        return []
    air = project.get("hook_name") == "air_compressor"
    if not air and machine_id not in CarbonCalculator._instances:
        CarbonCalculator.init_machine_formula(machine, project.get("hook_name", ""),
                                              config.VCER_API_URL)
    changes, prior, cumulative = [], None, 0.0
    high_flow = high_power = None
    in_review_period = False
    from .monitoring_quality import cases_for_machine
    cases, suppressed_ids = cases_for_machine(conn, machine_id, project.get("baseline_date"), rows)
    if quality_overrides:
        for case in cases:
            key = (case["kind"], case["start_row_id"])
            if key in quality_overrides:
                case["decision"] = quality_overrides[key]
                case["decision_stale"] = False
    overlap_daily = {case["start_row_id"] for case in cases
                     if case["kind"] == "sampling_overlap" and case.get("suggested")!="hour_close"}
    from .meter_continuity import effective_resolution, CONTINUOUS
    pending_rows,reset_hold,reset_baselines,excluded_rows={},{},set(),set()
    continuous_rows={}
    retained_rows={}
    for case in cases:
        decision=case.get("decision") or {}
        if case.get("decision_stale") and decision.get("resolution") in CONTINUOUS:
            snapshot=_json(decision.get("snapshot_json"))
            booked=dict(snapshot.get("booked") or [])
            for reading in rows:
                if snapshot.get("start","")<reading["timestamp"]<=snapshot.get("end",""):
                    retained_rows[reading["id"]]=float(booked.get(reading["id"],0) or 0)
    for case in cases:
        resolution,baseline_id=effective_resolution(case)
        if case["kind"]=="coverage_gap" and resolution in CONTINUOUS:
            continuous_rows[case["start_row_id"]]=case
        if case["kind"]=="coverage_gap" and resolution=="start_epoch":
            reset_baselines.add(case["start_row_id"])
            reset_hold[case["start_row_id"]]=case

    gap_rows = {case["start_row_id"] for case in cases
                if case["kind"] == "coverage_gap" and
                effective_resolution(case)[0] not in ("confirmed_inactive",*CONTINUOUS)}
    for case in cases:
        if case["kind"] != "meter_drop":
            continue
        resolution,baseline_id=effective_resolution(case)
        ids=case["affected_ids"]
        if resolution=="reset_epoch" and baseline_id in ids:
            for row_id in ids[:ids.index(baseline_id) + 1]:
                reset_hold[row_id] = case
            reset_baselines.add(baseline_id)
        elif resolution == "exclude_event" and case["recovered"]:
            excluded_rows.update(ids[:-1])
        else:
            pending_rows.update({row_id: case for row_id in ids})
    # Midnight daily aggregates may contain readings from later that same day.
    # Preserve raw timestamps and flag the ordering ambiguity separately.
    midnight = {}
    for reading in rows:
        if str(reading["timestamp"])[11:19] == "00:00:00":
            midnight[str(reading["timestamp"])[:10]] = _json(reading["raw_data"])
    # Use the factor already stored with historical calculations. Re-fetching
    # today's factor could silently rewrite years of approved calculations.
    stored_factor = next((_number(_json(row["breakdown"]).get("z_b_factor"))
                          for row in rows if _number(_json(row["breakdown"]).get("z_b_factor")) is not None),
                         None)
    segment_id,segment_index,segment_cumulative,segment_reason=rows[0]["id"],1,0.0,"首条读数基准"
    for row in rows:
        if row["id"] in reset_baselines and row["id"]!=segment_id:
            segment_id=row["id"];segment_index+=1;segment_cumulative=0.0
            segment_reason="累计表重新计数；跨边界差额不计"
        raw, bd = _json(row["raw_data"]), _json(row["breakdown"])
        new_bd = dict(bd)
        for key in ("meter_review", "boundary_review", "sampling_conflict",
                    "review_event_start", "sampling_suppressed", "daily_close",
                    "coverage_gap", "quality_case_start", "excluded_invalid_reading",
                    "reset_boundary", "minor_rollback", "segment_id", "segment_index", "segment_cumulative",
                    "segment_start", "segment_reason", "continuity_confirmed", "continuity_change"):
            new_bd.pop(key, None)
        if bd.get("note") in (
                "累计表回滚或换表，已暂停该段计量，需核对仪表边界",
                "疑似日汇总与同日分钟读数顺序冲突，已暂停该段计量，需核对接口时间含义",
                "日汇总与同日分钟读数顺序冲突，已暂停该段计量，需核对接口时间含义",
                "同日日结读数已覆盖此细粒度读数，保留原始值但不重复计量",
                "经核实为无效采样，保留原始值供追溯",
                "已确认新计量起点；跨边界差额不计",
                "新计量阶段基准；跨边界差额不计",
                "确认后增加了中间读数，暂保留原计量；请预览新数据后应用",
                "累计表下降事件尚未确认，事件区间暂停计量",
                "小幅读数修正；本条不计，后续从原最高有效读数继续"):
            new_bd.pop("note", None)
        flow, power = _number(raw.get("cumulative_flow")), _number(raw.get("cumulative_power"))
        if row["id"] in continuous_rows:
            boundary=continuous_rows[row["id"]]["before"]
            high_flow,high_power=boundary["flow"],boundary["power"]
            new_bd["continuity_confirmed"]=True
        if bd.get("bridge"):
            amount = float(row["carbon_reduction"] or 0)
        elif bd.get("direct_amount") or (
                row["source"] == "import" and raw == bd and not bd.get("baseline")):
            # Legacy direct-entry rows stored the same JSON as raw and breakdown.
            amount = float(row["carbon_reduction"] or 0)
        elif row["source"] in ("manual", "import", "api") and (
                not raw or (air and (flow is None or power is None))):
            # Explicit amount imports have no pair of meter readings to recalculate.
            amount = float(row["carbon_reduction"] or 0)
        elif row["id"] in suppressed_ids:
            amount = 0.0
            new_bd["sampling_suppressed"] = True
            new_bd["note"] = "同日日结读数已覆盖此细粒度读数，保留原始值但不重复计量"
        elif row["id"] in excluded_rows:
            amount = 0.0
            new_bd["excluded_invalid_reading"] = True
            new_bd["note"] = "经核实为无效采样，保留原始值供追溯"
        elif row["id"] in reset_hold:
            amount = 0.0
            new_bd["reset_boundary"] = True
            new_bd["quality_case_start"] = reset_hold[row["id"]]["start_row_id"]
            new_bd["note"] = "新计量阶段基准；跨边界差额不计"
        elif row["id"] in pending_rows:
            amount = 0.0
            new_bd["meter_review"] = True
            new_bd["quality_case_start"] = pending_rows[row["id"]]["start_row_id"]
            new_bd["note"] = "累计表下降事件尚未确认，事件区间暂停计量"
        elif prior is None:
            amount = 0.0
            new_bd = {"baseline": True, "note": "该机器首条读数作为基线，不计减排量"}
        elif air and (flow is None or power is None or high_flow is None or high_power is None):
            amount = 0.0
            new_bd["boundary_review"] = True
        elif air and (flow < high_flow or power < high_power):
            amount = 0.0
            small_flow = flow >= high_flow - max(10.0, abs(high_flow)*1e-6)
            small_power = power >= high_power - max(1.0, abs(high_power)*1e-6)
            if small_flow and small_power:
                new_bd["minor_rollback"] = True
                new_bd["note"] = "小幅读数修正；本条不计，后续从原最高有效读数继续"
            else:
                new_bd["meter_review"] = True
            daily = midnight.get(str(row["timestamp"])[:10], {})
            day_flow = _number(daily.get("cumulative_flow"))
            day_power = _number(daily.get("cumulative_power"))
            conflict = str(row["timestamp"])[11:19] != "00:00:00" and (
                (day_flow is not None and flow is not None and flow < day_flow) or
                (day_power is not None and power is not None and power < day_power))
            if new_bd.get("meter_review") and conflict:
                new_bd["sampling_conflict"] = True
                new_bd["note"] = "疑似日汇总与同日分钟读数顺序冲突，已暂停该段计量，需核对接口时间含义"
            elif new_bd.get("meter_review"):
                new_bd["note"] = "累计表回滚或换表，已暂停该段计量，需核对仪表边界"
        else:
            if air:
                # A rebound below an earlier peak cannot be counted twice.
                prior_values = {"prev_flow": high_flow, "prev_power": high_power}
            else:
                previous = _json(prior["raw_data"]) if prior else {}
                prior_values = {
                    "prev_" + key.removeprefix("cumulative_"): value
                    for key, value in previous.items() if key.startswith("cumulative_")
                }
            if air:
                factor = _number(bd.get("z_b_factor")) or stored_factor
                if factor is None:
                    raise ValueError(f"机器 {machine_id} 缺少历史 Z_b 因子，不可静默重算")
                ef = _number(bd.get("ef_grid")) or 0.5777
                delta_flow = flow - high_flow
                delta_power = power - high_power
                baseline_emission = delta_flow * factor * 0.00001
                project_emission = delta_power / 1000.0 * ef
                amount = round(max(0.0, baseline_emission - project_emission), 6)
                new_bd = {
                    **new_bd, "delta_flow_m3": round(delta_flow, 2),
                    "delta_power_kwh": round(delta_power, 2),
                    "z_b_factor": factor, "ef_grid": ef,
                    "baseline_emissions_tco2": round(baseline_emission, 8),
                    "project_emissions_tco2": round(project_emission, 8),
                    "reconciled": True}
                new_bd.pop("baseline", None)
                new_bd.pop("gap_reset", None)
            else:
                result = CarbonCalculator.compute(machine_id, raw, {
                    **prior_values, "cumulative_reduction": cumulative})
                amount = float(result["carbon_reduction"])
                new_bd = result.get("breakdown", {})
        # The ESG day endpoint labels the same day's closing value at 00:00.
        # Old rows lack record_time_type: identify them only when their apart
        # values match a preceding day-scale interval or a proven overlap.
        day_close = raw.get("record_time_type") == "day" or row["id"] in overlap_daily
        if (not day_close and prior is not None
                and str(row["timestamp"])[11:19] == "00:00:00"):
            old_raw = _json(prior["raw_data"])
            old_flow = _number(old_raw.get("cumulative_flow"))
            old_power = _number(old_raw.get("cumulative_power"))
            apart_flow = _number(raw.get("apart_flow"))
            apart_power = _number(raw.get("apart_power"))
            try:
                hours = (datetime.fromisoformat(str(row["timestamp"])) -
                         datetime.fromisoformat(str(prior["timestamp"]))).total_seconds()/3600
            except ValueError:
                hours = 0
            day_close = (hours >= 20 and None not in
                         (flow,power,old_flow,old_power,apart_flow,apart_power)
                         and abs((flow-old_flow)-apart_flow) <= max(1, abs(apart_flow)*.02)
                         and abs((power-old_power)-apart_power) <= max(.1, abs(apart_power)*.02))
        if day_close:
            new_bd["daily_close"] = True
        if row["id"] in gap_rows:
            new_bd["coverage_gap"] = True
        if row["id"] in retained_rows:
            amount=retained_rows[row["id"]]
            new_bd["continuity_change"]=True
            new_bd["boundary_review"]=True
            new_bd["note"]="确认后增加了中间读数，暂保留原计量；请预览新数据后应用"
        needs_review = bool(new_bd.get("meter_review") or new_bd.get("boundary_review"))
        if needs_review and not in_review_period:
            new_bd["review_event_start"] = True
        in_review_period = needs_review
        cumulative = round(cumulative + amount, 6)
        segment_cumulative=round(segment_cumulative+amount,6)
        new_bd.update(segment_id=segment_id,segment_index=segment_index,
                      segment_cumulative=segment_cumulative,segment_reason=segment_reason,
                      segment_start=row["id"]==segment_id)
        if row["id"] in reset_baselines:
            high_flow, high_power = flow, power
        elif row["id"] not in suppressed_ids and row["id"] not in excluded_rows \
                and row["id"] not in reset_hold:
            if flow is not None:
                high_flow = max(flow, high_flow) if high_flow is not None else flow
            if power is not None:
                high_power = max(power, high_power) if high_power is not None else power
        if (abs(amount - float(row["carbon_reduction"] or 0)) > 0.0000005 or
                abs(cumulative - float(row["cumulative_reduction"] or 0)) > 0.0000005 or
                new_bd != bd):
            changes.append({"id": row["id"], "timestamp": row["timestamp"],
                            "old_reduction": row["carbon_reduction"], "new_reduction": round(amount, 6),
                            "old_cumulative": row["cumulative_reduction"],
                            "new_cumulative": cumulative, "breakdown": new_bd})
        prior = row
    return changes


def reconcile_machine(machine_id, apply=False, conn=None, quality_overrides=None):
    own = conn is None
    conn = conn or get_db()
    try:
        changes = _reconcile_rows(conn, machine_id, quality_overrides)
        if apply:
            from .meter_continuity import invalidate_confirmations
            invalidate_confirmations(conn,machine_id)
            for c in changes:
                conn.execute("UPDATE monitoring_data SET carbon_reduction=?, "
                             "cumulative_reduction=?, breakdown=? WHERE id=?",
                             (c["new_reduction"], c["new_cumulative"],
                              json.dumps(c["breakdown"], ensure_ascii=False), c["id"]))
            pid = conn.execute("SELECT project_id FROM project_machines WHERE id=?",
                               (machine_id,)).fetchone()["project_id"]
            refresh_totals(conn, pid)
            if own:
                conn.commit()
        return changes
    finally:
        if own:
            conn.close()

