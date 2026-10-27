"""Read-only anomaly detection and persisted, evidence-backed resolutions.

The source readings remain immutable.  A decision belongs to a meter event,
not to every affected monitoring row.
"""
import json
import sqlite3
from collections import defaultdict
from datetime import datetime


def _raw(value):
    try:
        data = json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _num(value):
    try:
        import math
        number=float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _time(value):
    try:
        return datetime.fromisoformat(str(value).replace("T", " ")[:19])
    except (TypeError, ValueError):
        return None


def _point(row):
    data = dict(row)
    raw = _raw(data.get("raw_data"))
    return {
        "id": data["id"], "timestamp": data["timestamp"],
        "flow": _num(raw.get("cumulative_flow")),
        "power": _num(raw.get("cumulative_power")),
        "apart_flow": _num(raw.get("apart_flow")),
        "apart_power": _num(raw.get("apart_power")),
        "raw": raw,
    }


def analyze_rows(rows, baseline_date=None):
    """Return independent meter-drop, sampling-overlap and coverage-gap events.

    The day API labels an end-of-day cumulative value 00:00 on that date.
    When finer readings exist that same day, booking those readings again
    double-counts the day; the day row is the accounting representative.
    """
    from .meter_continuity import public_point, gap_metrics
    points = [_point(row) for row in rows]
    by_day = defaultdict(list)
    for point in points:
        by_day[point["timestamp"][:10]].append(point)
    suppressed = set()
    events = []
    for day, samples in by_day.items():
        midnight = next((p for p in samples if p["timestamp"][11:19] == "00:00:00"), None)
        finer = [p for p in samples if p is not midnight and p["timestamp"][11:19] != "00:00:00"]
        if not midnight or not finer:
            continue
        # Only recognise a daily closing value when later fine readings are
        # behind it in BOTH counters.  Do not infer the semantics from time
        # of day alone.
        is_day_close = midnight["raw"].get("record_time_type") == "day" or any(
            midnight["flow"] is not None and midnight["power"] is not None
            and p["flow"] is not None and p["power"] is not None
            and p["flow"] <= midnight["flow"] and p["power"] <= midnight["power"]
            and (p["flow"] < midnight["flow"] or p["power"] < midnight["power"])
            for p in finer
        )
        fine_drop = any(all(a.get(k) is not None and b.get(k) is not None for k in ("flow","power"))
                        and (b["flow"] < a["flow"]-max(10,abs(a["flow"])*1e-6)
                             or b["power"] < a["power"]-max(1,abs(a["power"])*1e-6))
                        for a,b in zip(finer,finer[1:]))
        fine_drop=fine_drop or any(all(p.get(k) is not None and midnight.get(k) is not None and p[k]<midnight[k]*.05 for k in ("flow","power")) for p in finer)
        if fine_drop:
            suppressed.add(midnight["id"])
            events.append({"kind":"sampling_overlap","start_row_id":midnight["id"],
                           "end_row_id":finer[-1]["id"],"start":day,"end":day,
                           "affected_ids":[midnight["id"]],"affected_count":1,"recovered":True,
                           "suggested":"fine_reset_evidence","detail":"日内细粒度包含回退，保留细粒度计量并排除交叠日结行"})
            continue
        if is_day_close:
            # The current day's day row may be only a partial close.  A later
            # fine reading above it represents new production and must remain
            # bookable rather than disappearing with earlier covered samples.
            covered = [p for p in finer if
                       p["flow"] is not None and p["power"] is not None and
                       midnight["flow"] is not None and midnight["power"] is not None and
                       p["flow"] <= midnight["flow"] and
                       p["power"] <= midnight["power"]]
            if not covered:
                continue
            ids = [p["id"] for p in covered]
            suppressed.update(ids)
            events.append({
                "kind": "sampling_overlap", "start_row_id": midnight["id"],
                "end_row_id": covered[-1]["id"], "start": day,
                "end": day, "affected_ids": ids, "affected_count": len(ids),
                "recovered": True, "suggested": "daily_close",
                "detail": "同日日结读数覆盖细粒度读数；细粒度仍保留供核查",
            })
    # Hour closing snapshots can also precede finer readings they already cover.
    by_hour=defaultdict(list)
    for point in points:
        if point["id"] not in suppressed: by_hour[point["timestamp"][:13]].append(point)
    for hour,samples in by_hour.items():
        coarse=next((p for p in samples if p["timestamp"][14:16]=="00"),None)
        fine=[p for p in samples if p is not coarse and p["timestamp"][14:16]!="00"]
        if not coarse or not fine: continue
        # A near-zero counter is not an overlapping aggregate; retain it as reset evidence.
        if any(all(p.get(k) is not None and coarse.get(k) is not None and p[k]<coarse[k]*.05 for k in ("flow","power")) for p in fine): continue
        covered=[p for p in fine if all(p.get(k) is not None and coarse.get(k) is not None and p[k]<=coarse[k] for k in ("flow","power"))]
        if not covered: continue
        if not (coarse["raw"].get("record_time_type")=="hour" or all(all(p[k]>=coarse[k]*.95 for k in ("flow","power")) for p in covered)): continue
        suppressed.update(p["id"] for p in covered)
        events.append({"kind":"sampling_overlap","start_row_id":coarse["id"],"end_row_id":covered[-1]["id"],
                       "start":coarse["timestamp"],"end":covered[-1]["timestamp"],
                       "affected_ids":[p["id"] for p in covered],"affected_count":len(covered),"recovered":True,
                       "suggested":"hour_close","detail":"小时结算读数已覆盖随后部分细粒度读数，保留原始值并去重"})
    ordered = [p for p in points if p["id"] not in suppressed]
    drops = []
    for idx in range(1, len(ordered)):
        prev, cur = ordered[idx - 1], ordered[idx]
        flow_drop = (prev["flow"] is not None and cur["flow"] is not None
                     and cur["flow"] < prev["flow"] - max(10.0, abs(prev["flow"])*1e-6))
        power_drop = (prev["power"] is not None and cur["power"] is not None
                      and cur["power"] < prev["power"] - max(1.0, abs(prev["power"])*1e-6))
        if not (flow_drop or power_drop):
            continue
        now, last = _time(cur["timestamp"]), _time(drops[-1]["last_time"]) if drops else None
        # One meter can clear a day after the other, as on Jingmei 2024-01-24/25.
        if drops and now and last and 0 <= (now - last).total_seconds() <= 2 * 86400:
            drops[-1]["drop_ids"].append(cur["id"])
            drops[-1]["last_time"] = cur["timestamp"]
        else:
            drops.append({
                "start_idx": idx, "start_row_id": cur["id"],
                "drop_ids": [cur["id"]], "last_time": cur["timestamp"],
                "before": prev,
            })
    for pos, drop in enumerate(drops):
        first = drop["start_idx"]
        limit = drops[pos + 1]["start_idx"] if pos + 1 < len(drops) else len(ordered)
        before = drop["before"]
        # A simultaneous collapse of both cumulative meters starts a new
        # candidate epoch even if its small pre-reset scale is exceeded next
        # day.  Jingmei's second reset would otherwise look "recovered".
        drop_points = ordered[first:min(limit, first + len(drop["drop_ids"]))]
        simultaneous_reset = (
            before["flow"] is not None and before["flow"] > 0
            and before["power"] is not None and before["power"] > 0
            and min((p["flow"] for p in drop_points if p["flow"] is not None), default=float("inf"))
                <= before["flow"] * 0.05
            and min((p["power"] for p in drop_points if p["power"] is not None), default=float("inf"))
                <= before["power"] * 0.05
        )
        recovered_at = None
        for idx in range(first + 1, limit):
            if simultaneous_reset and (_time(ordered[idx]["timestamp"])-_time(ordered[first]["timestamp"])).total_seconds()>3*86400:
                break
            p = ordered[idx]
            if (before["flow"] is not None and before["power"] is not None
                    and p["flow"] is not None and p["power"] is not None
                    and p["flow"] >= before["flow"] - 0.5
                    and p["power"] >= before["power"] - 0.05):
                # A complete reset that only regains a tiny former count is a
                # real new epoch; only a near-original large scale bounce is transient.
                if simultaneous_reset and (before["flow"]<1000 or before["power"]<100):
                    continue
                recovered_at = idx
                break
        restoration_jump=False
        if recovered_at is not None:
            previous,recovery=ordered[recovered_at-1],ordered[recovered_at]
            dt=(_time(recovery["timestamp"])-_time(previous["timestamp"])).total_seconds()
            for metric in ("flow","power"):
                if any(p.get(metric) is None for p in (before,previous,recovery)): continue
                rates=[]
                for a,b in zip(ordered[max(0,first-60):first],ordered[max(0,first-60)+1:first]):
                    seconds=(_time(b["timestamp"])-_time(a["timestamp"])).total_seconds()
                    if seconds>0 and a.get(metric) is not None and b.get(metric) is not None and b[metric]>=a[metric]: rates.append((b[metric]-a[metric])/seconds)
                cap=sorted(rates)[int(.95*(len(rates)-1))]*2 if len(rates)>=5 else None
                jump=recovery[metric]-previous[metric]
                net=max(1,abs(recovery[metric]-before[metric]))
                if jump>max(1000,net*10) and (cap is None or (dt>0 and jump/dt>cap)):
                    restoration_jump=True
        last_idx = recovered_at if recovered_at is not None else limit - 1
        involved = ordered[first:last_idx + 1]
        if not involved:
            continue
        events.append({
            "kind": "meter_drop", "start_row_id": drop["start_row_id"],
            "end_row_id": involved[-1]["id"],
            "start": involved[0]["timestamp"], "end": involved[-1]["timestamp"],
            "affected_ids": [p["id"] for p in involved],
            "affected_count": len(involved), "drop_ids": drop["drop_ids"],
            "candidate_points": [
                {k: p[k] for k in ("id", "timestamp", "flow", "power")}
                for p in ordered[first:limit] if p["id"] in drop["drop_ids"]
            ],
            "before": {k: before[k] for k in ("id", "timestamp", "flow", "power")},
            "after": public_point(ordered[first]),
            "recovered": recovered_at is not None,
            "restoration_jump":restoration_jump,
            "recovery_seconds": ((_time(ordered[recovered_at]["timestamp"])-_time(ordered[first]["timestamp"])).total_seconds() if recovered_at is not None else None),
            "suggested": ("reset_candidate" if simultaneous_reset else
                          "transient" if recovered_at is not None else "reset_or_missing"),
            "detail": "累计读数下降；需核实是错误采样、换表还是设备清零",
        })
    if ordered and baseline_date:
        first = _time(ordered[0]["timestamp"])
        baseline = _time(str(baseline_date)[:10] + " 00:00:00")
        if first and baseline and (first - baseline).days > 2:
            events.append({
                "kind": "coverage_gap", "start_row_id": ordered[0]["id"],
                "end_row_id": ordered[0]["id"], "start": baseline.date().isoformat(),
                "end": first.date().isoformat(), "affected_ids": [ordered[0]["id"]],
                "affected_count": 1, "recovered": False, "suggested": "source_check",
                "before": None, "after": public_point(ordered[0]),
                "metrics": {"continuous":False,"classification":"initial_boundary"},
                "detail": "基准日至首条读数无起点，首条作为计量基准；此前不估算减排",
            })
    recent_seconds=[]
    for index,(before,after) in enumerate(zip(ordered,ordered[1:])):
        a,b=_time(before["timestamp"]),_time(after["timestamp"])
        if not a or not b or b<=a: continue
        seconds=(b-a).total_seconds()
        import statistics
        cadence=statistics.median(recent_seconds[-30:]) if recent_seconds else 86400
        threshold=max(86400,3*cadence)
        if seconds>threshold:
            metrics=gap_metrics(before,after,ordered[:index+1])
            events.append({"kind":"coverage_gap","start_row_id":after["id"],"end_row_id":after["id"],
                           "start":before["timestamp"],"end":after["timestamp"],
                           "affected_ids":[after["id"]],"affected_count":1,"recovered":False,
                           "suggested":"source_check","before":public_point(before),"after":public_point(after),
                           "metrics":metrics,"detail":"缺少中间采样；非负累计差仍正常计量，低增量可能是停机"})
        if seconds<=3*86400: recent_seconds.append(seconds)
    # Supplier apartValue is not independent of value.  Long-window normal
    # queries can be downsampled more coarsely than the interval used for
    # apartValue; flag that mismatch instead of silently booking it.
    mismatches = []
    for before, after in zip(ordered, ordered[1:]):
        a, b = _time(before["timestamp"]), _time(after["timestamp"])
        if not a or not b or not (0 < (b-a).total_seconds() <= 3600):
            continue
        if before["timestamp"][11:19] == "00:00:00" or after["timestamp"][11:19] == "00:00:00":
            continue
        checks = []
        for metric, apart, tolerance in (("flow", "apart_flow", 1.0),
                                          ("power", "apart_power", 0.1)):
            if (before[metric] is not None and after[metric] is not None
                    and after[apart] is not None):
                difference = after[metric] - before[metric]
                checks.append(abs(difference - after[apart]) >
                              max(tolerance, abs(difference) * 0.05))
        if checks and any(checks):
            mismatches.append(after)
    if mismatches:
        # Mismatches are one source-format diagnostic per machine, not dozens
        # of independent meter faults. Preserve every row id and the full span.
        events.append({
            "kind": "apart_mismatch", "start_row_id": mismatches[0]["id"],
            "end_row_id": mismatches[-1]["id"],
            "start": mismatches[0]["timestamp"],
            "end": mismatches[-1]["timestamp"],
            "affected_ids": [p["id"] for p in mismatches],
            "affected_count": len(mismatches), "recovered": False,
            "suggested": "use_cumulative_difference",
            "detail": "部分采样的 apartValue 与本地相邻累计读数差不符；这是来源诊断，不是每条都发生表计故障",
        })
    events.sort(key=lambda e: (str(e["start"]), e["kind"]))
    return events, suppressed


def decisions_for_machine(conn, machine_id):
    try:
        rows = conn.execute(
            "SELECT * FROM monitoring_quality_decisions WHERE machine_id=?",
            (machine_id,)).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return {}
        raise
    return {(r["kind"], r["start_row_id"]): dict(r) for r in rows}


def cases_for_machine(conn, machine_id, baseline_date=None, rows=None):
    if rows is None:
        rows = conn.execute(
            "SELECT id,timestamp,raw_data FROM monitoring_data "
            "WHERE machine_id=? ORDER BY timestamp,id", (machine_id,)).fetchall()
    from .meter_continuity import decision_valid, point_snapshot, digest, effective_resolution, gap_metrics
    events,suppressed=analyze_rows(rows,baseline_date)
    decisions=decisions_for_machine(conn,machine_id)
    # Preserve an invalidated confirmation as a reviewable event even when new
    # dense points made the original time gap disappear.
    from .meter_continuity import CONTINUOUS
    for old in decisions.values():
        if old.get("resolution") not in CONTINUOUS or decision_valid(conn,machine_id,old): continue
        snapshot=_raw(old.get("snapshot_json"))
        if not snapshot.get("before") or not snapshot.get("after"): continue
        lo,hi=snapshot["start"],snapshot["end"]
        interior=[_point(r) for r in rows if lo<=r["timestamp"]<=hi and r["id"] not in suppressed]
        metrics=gap_metrics(snapshot["before"],snapshot["after"],[])
        if any(e["kind"]=="meter_drop" and lo<=e["start"]<=hi for e in events): metrics["continuous"]=False
        events=[e for e in events if not(e["kind"]=="coverage_gap" and e["start_row_id"]==old["start_row_id"])]
        events.append({"kind":"coverage_gap","start_row_id":old["start_row_id"],"end_row_id":old["start_row_id"],
                       "start":lo,"end":hi,"before":snapshot["before"],"after":snapshot["after"],"metrics":metrics,
                       "affected_ids":[p["id"] for p in interior],"affected_count":len(interior),"recovered":False,
                       "suggested":"review_new_evidence","detail":"确认后区间内增加了新读数；旧计量暂保留，请预览后接纳新数据或重新确认连续"})
    events.sort(key=lambda e:(str(e["start"]),e["kind"]))
    try:
        probes=conn.execute("SELECT id,start_row_id,status,result_json,finished_at FROM monitoring_gap_probes WHERE machine_id=? ORDER BY started_at,id",(machine_id,)).fetchall()
    except sqlite3.OperationalError:
        probes=[]
    latest={r["start_row_id"]:r for r in probes}
    for event in events:
        decision=decisions.get((event["kind"],event["start_row_id"]))
        event["decision"]=decision
        event["decision_stale"]=bool(decision and not decision_valid(conn,machine_id,decision))
        event["case_signature"]=digest(point_snapshot(event,rows))
        probe=latest.get(event["start_row_id"]) if event["kind"]=="coverage_gap" else None
        if probe:
            result=_raw(probe["result_json"])
            event["probe"]={"id":probe["id"],"status":probe["status"],"finished_at":probe["finished_at"],
                            **{k:v for k,v in result.items() if k not in ("rows","signature")}}
        try:
            history=conn.execute("SELECT action,payload_json,created_at FROM monitoring_quality_history WHERE machine_id=? AND kind=? AND start_row_id=? ORDER BY id DESC",(machine_id,event["kind"],event["start_row_id"])).fetchall()
            event["history"]=[]
            for item in history:
                payload=_raw(item["payload_json"])
                event["history"].append({"action":item["action"],"created_at":item["created_at"],
                                         **{k:payload.get(k) for k in ("resolution","reviewer","note","evidence")}})
        except sqlite3.OperationalError:
            event["history"]=[]
        resolution,baseline=effective_resolution(event)
        event["effective_resolution"]=resolution
        event["effective_baseline"]=baseline
        event["status"]=("stale" if event["decision_stale"] else
                         "auto_resolved" if event["kind"]=="sampling_overlap" else
                         "diagnostic" if event["kind"]=="apart_mismatch" else
                         "resolved" if decision and decision["resolution"]!="pending" else
                         "auto_segmented" if event["kind"]=="meter_drop" else "pending")
    return events, suppressed

def find_case(conn, project_id, machine_id, kind, start_row_id):
    machine = conn.execute(
        "SELECT m.project_id,p.baseline_date,p.lifecycle_status "
        "FROM project_machines m JOIN carbon_projects p ON p.id=m.project_id "
        "WHERE m.id=?", (machine_id,)).fetchone()
    if not machine or machine["project_id"] != project_id:
        raise ValueError("机器不属于这个项目")
    cases, _ = cases_for_machine(conn, machine_id, machine["baseline_date"])
    case = next((e for e in cases
                 if e["kind"] == kind and e["start_row_id"] == start_row_id), None)
    if not case:
        raise ValueError("事件已变化，请刷新列表后重新核对")
    return case, machine


def validate_resolution(case, resolution, baseline_row_id=None):
    if case["kind"] == "meter_drop":
        if resolution == "reset_epoch":
            if baseline_row_id not in case["affected_ids"]:
                raise ValueError("新计量起点必须属于本次事件")
        elif resolution == "exclude_event":
            if not case["recovered"] or len(case["affected_ids"]) < 2:
                raise ValueError("尚无可核实的恢复读数，不可排除整个事件")
        elif resolution != "pending":
            raise ValueError("不支持的回退处理结论")
    elif case["kind"] == "coverage_gap":
        from .meter_continuity import CONTINUOUS
        if resolution not in ("pending","confirmed_inactive","confirmed_missing","start_epoch","accept_new_data",*CONTINUOUS):
            raise ValueError("请选择缺口的核对结论")
        if resolution in CONTINUOUS and not (case.get("before") and (case.get("metrics") or {}).get("continuous")):
            raise ValueError("两项累计读数须均未回退且有完整 B/C 边界，才能确认连续")
    else:
        raise ValueError("日结与细粒度交叠由账本自动去重，无需人工改写")


def preview_decision(conn, project_id, machine_id, kind, start_row_id,
                     resolution, baseline_row_id=None):
    """Preview every downstream amount without writing a decision or reading."""
    import hashlib
    from .reduction_ledger import reconcile_machine
    case, machine = find_case(conn, project_id, machine_id, kind, start_row_id)
    validate_resolution(case, resolution, baseline_row_id)
    rows = conn.execute(
        "SELECT id,timestamp,raw_data,carbon_reduction,cumulative_reduction,breakdown "
        "FROM monitoring_data WHERE machine_id=? ORDER BY timestamp,id",
        (machine_id,)).fetchall()
    payload = [(r["id"], r["timestamp"], r["raw_data"], r["carbon_reduction"],
                r["cumulative_reduction"], r["breakdown"]) for r in rows]
    decision = {"resolution": resolution, "baseline_row_id": baseline_row_id}
    decisions = sorted(decisions_for_machine(conn, machine_id).values(),
                       key=lambda r: (r["kind"], r["start_row_id"]))
    signature = hashlib.sha256(json.dumps(
        [payload, decisions, kind, start_row_id, decision], ensure_ascii=False,
        default=str).encode("utf-8")).hexdigest()
    changes = reconcile_machine(machine_id, apply=False, conn=conn,
                                quality_overrides={(kind, start_row_id): decision})
    annual = defaultdict(float)
    for change in changes:
        annual[change["timestamp"][:4]] += (
            float(change["new_reduction"] or 0) - float(change["old_reduction"] or 0))
    total_delta = sum(annual.values())
    reports = conn.execute("SELECT id,report_type,generated_at FROM reports "
                           "WHERE project_id=? ORDER BY id", (project_id,)).fetchall()
    return {
        "case": case, "signature": signature,
        "rows_changed": len(changes), "reduction_delta": round(total_delta, 6),
        "annual_delta": {year: round(v, 6) for year, v in sorted(annual.items())},
        "review_rows_after": sum(bool(c["breakdown"].get("meter_review"))
                                 for c in changes),
        "sample_changes": changes[:8] + (changes[-8:] if len(changes) > 8 else []),
        "existing_reports": [dict(r) for r in reports],
    }

def apply_decision(conn, project_id, machine_id, kind, start_row_id,
                   resolution, baseline_row_id, signature, reviewer, note,
                   evidence):
    """Apply a reviewed preview atomically; archive pre-change rows first."""
    from pathlib import Path
    from uuid import uuid4
    import config
    from .reduction_ledger import reconcile_machine
    from .meter_continuity import CONTINUOUS, point_snapshot, digest
    if not reviewer.strip() or not note.strip() or (resolution not in (*CONTINUOUS,"pending","accept_new_data") and not evidence.strip()):
        raise ValueError("请填写核对人和处理依据；换表或排除错误点还需证据来源")
    if len(reviewer) > 80 or len(note) > 2000 or len(evidence) > 2000:
        raise ValueError("核对内容过长")
    conn.execute("BEGIN IMMEDIATE")
    try:
        preview = preview_decision(conn, project_id, machine_id, kind,
                                   start_row_id, resolution, baseline_row_id)
        if preview["signature"] != signature:
            raise ValueError("预览后监测数据已变化，请重新预览")
        case, machine = find_case(conn, project_id, machine_id, kind, start_row_id)
        machine_status = conn.execute(
            "SELECT lifecycle_status FROM project_machines WHERE id=?",
            (machine_id,)).fetchone()
        if (machine["lifecycle_status"] != "active" or not machine_status
                or machine_status["lifecycle_status"] != "active"):
            raise ValueError("项目或机器已作废，不可修改历史计量")
        target = (Path(config.data_path("backups")) /
                  ("quality_project_%s_machine_%s_%s.json" %
                   (project_id, machine_id, uuid4().hex[:12])))
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = {
            "project_id": project_id, "machine_id": machine_id,
            "case": case, "preview": {
                k: v for k, v in preview.items() if k != "case"
            },
            "project": dict(conn.execute(
                "SELECT * FROM carbon_projects WHERE id=?", (project_id,)).fetchone()),
            "decisions": [dict(r) for r in conn.execute(
                "SELECT * FROM monitoring_quality_decisions WHERE machine_id=?",
                (machine_id,))],
            "monitoring": [dict(r) for r in conn.execute(
                "SELECT * FROM monitoring_data WHERE machine_id=? ORDER BY timestamp,id",
                (machine_id,))],
        }
        target.write_text(json.dumps(backup, ensure_ascii=False, default=str),
                          encoding="utf-8")
        conn.execute(
            """INSERT INTO monitoring_quality_decisions
               (project_id,machine_id,kind,start_row_id,resolution,
                baseline_row_id,reviewer,note,evidence)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(machine_id,kind,start_row_id) DO UPDATE SET
                 resolution=excluded.resolution,
                 baseline_row_id=excluded.baseline_row_id,
                 reviewer=excluded.reviewer,note=excluded.note,
                 evidence=excluded.evidence,
                 updated_at=datetime('now','localtime')""",
            (project_id,machine_id,kind,start_row_id,resolution,
             baseline_row_id,reviewer.strip(),note.strip(),evidence.strip()))
        source_rows=conn.execute("SELECT id,timestamp,raw_data FROM monitoring_data WHERE machine_id=? ORDER BY timestamp,id",(machine_id,)).fetchall()
        snapshot=point_snapshot(case,source_rows)
        conn.execute("UPDATE monitoring_quality_decisions SET snapshot_json=?,case_signature=?,invalidated_at='' WHERE machine_id=? AND kind=? AND start_row_id=?",
                     (json.dumps(snapshot,ensure_ascii=False),digest(snapshot),machine_id,kind,start_row_id))
        conn.execute("INSERT INTO monitoring_quality_history (project_id,machine_id,kind,start_row_id,action,payload_json) VALUES (?,?,?,?,?,?)",
                     (project_id,machine_id,kind,start_row_id,"decision",json.dumps({"previous":case.get("decision"),"resolution":resolution,"baseline_row_id":baseline_row_id,
                      "reviewer":reviewer,"note":note,"evidence":evidence,"snapshot":snapshot},ensure_ascii=False)))
        changes = reconcile_machine(machine_id, apply=True, conn=conn)
        if resolution in CONTINUOUS:
            booked=conn.execute("SELECT id,carbon_reduction FROM monitoring_data WHERE machine_id=? AND timestamp>=? AND timestamp<=? ORDER BY timestamp,id",(machine_id,snapshot["start"],snapshot["end"])).fetchall()
            snapshot["booked"]=[tuple(r) for r in booked]
            conn.execute("UPDATE monitoring_quality_decisions SET snapshot_json=?,case_signature=? WHERE machine_id=? AND kind=? AND start_row_id=?",(json.dumps(snapshot,ensure_ascii=False),digest(snapshot),machine_id,kind,start_row_id))
        conn.commit()
        return {"applied_rows": len(changes), "backup": str(target),
                "reduction_delta": preview["reduction_delta"]}
    except Exception:
        conn.rollback()
        raise


def unresolved_for_project(conn, project_id, start_date=None, end_date=None):
    """Find unreviewed meter events and coverage gaps affecting a report span."""
    machines = conn.execute(
        "SELECT m.id,p.baseline_date FROM project_machines m "
        "JOIN carbon_projects p ON p.id=m.project_id "
        "WHERE m.project_id=? AND m.lifecycle_status='active' ORDER BY m.id",
        (project_id,)).fetchall()
    pending = []
    for machine in machines:
        cases, _ = cases_for_machine(conn, machine["id"], machine["baseline_date"])
        for case in cases:
            if case["kind"] not in ("meter_drop", "coverage_gap"):
                continue
            if case["status"] != "pending" and not (
                    case["kind"] == "coverage_gap" and
                    (case.get("decision") or {}).get("resolution") == "confirmed_missing"):
                continue
            if start_date and case["end"][:10] < start_date[:10]:
                continue
            if end_date and case["start"][:10] > end_date[:10]:
                continue
            pending.append({"machine_id":machine["id"], "kind":case["kind"],
                            "start":case["start"], "end":case["end"],
                            "affected_count":case["affected_count"]})
    return pending


def check_source_case(conn, project_id, machine_id, kind, start_row_id):
    """Read source evidence through the machine's selected enterprise adapter."""
    from datetime import timedelta
    from adapters.api_adapter import ApiAdapter
    case, _ = find_case(conn, project_id, machine_id, kind, start_row_id)
    config_row = conn.execute(
        "SELECT history_api_base,history_api_username,history_api_password "
        "FROM enterprise_config WHERE id=1").fetchone()
    machine = conn.execute(
        "SELECT data_source_config,normalizer_config FROM project_machines WHERE id=?",
        (machine_id,)).fetchone()
    source = _raw(machine["data_source_config"] if machine else None)
    normalizer = _raw(machine["normalizer_config"] if machine else None) or source.get("normalizer") or {}
    adapter = ApiAdapter({**source, "normalizer": normalizer})
    hook = adapter._normalizer.hook
    site_id = str(source.get("site_id") or "").strip()
    if not config_row or not config_row[0] or not site_id or hook is None:
        raise ValueError("数据平台配置、机器站点或前置转换文件不完整")
    start = _time(case["start"])
    if start is None:
        raise ValueError("事件日期无效")
    begin = (start - timedelta(days=2)).strftime("%Y-%m-%d 00:00:00")
    end = (start + timedelta(days=4)).strftime("%Y-%m-%d 00:00:00")
    base, user, password = config_row
    url, headers, params, body, method = adapter._history_request(
        hook, base, user, password, site_id, begin, end, "day")
    result = adapter._authorized_post(hook, base, user, password, url,
                                      headers, body, 30, method, params)
    returned = []
    convert = getattr(hook, "convert_api_with_apart", hook.convert_api)
    for alias in hook.aliases.values():
        for row in hook.parse_history_response(alias, result):
            mapped = convert(alias, row)
            if mapped is None:
                continue
            returned.append({"field": mapped[0], "timestamp": mapped[2],
                             "value": mapped[1],
                             "apartValue": mapped[3] if len(mapped) > 3 else None,
                             "recordTimeType": row.get("recordTimeType") if isinstance(row, dict) else None,
                             "source_record": row})
    return {"site_id": site_id, "begin": begin, "end": end,
            "source_count": len(returned), "source_rows": returned}
