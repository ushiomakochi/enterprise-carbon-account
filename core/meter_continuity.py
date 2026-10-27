"""Continuity evidence, decision history, segments and scoped acknowledgements."""
import hashlib
import json
import math
import statistics
from datetime import datetime, timedelta

CONTINUOUS={"confirmed_shutdown","confirmed_continuous"}
VERSION="continuity-v1"


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def init_schema(conn):
    columns={r[1] for r in conn.execute("PRAGMA table_info(monitoring_quality_decisions)")}
    for name,spec in (("snapshot_json","TEXT DEFAULT '{}'"),("case_signature","TEXT DEFAULT ''"),("invalidated_at","TEXT DEFAULT ''")):
        if name not in columns:
            conn.execute("ALTER TABLE monitoring_quality_decisions ADD COLUMN "+name+" "+spec)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS monitoring_quality_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,machine_id INTEGER NOT NULL,
        kind TEXT NOT NULL,start_row_id INTEGER NOT NULL,action TEXT NOT NULL,payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));
    CREATE INDEX IF NOT EXISTS idx_quality_history ON monitoring_quality_history(machine_id,id);
    CREATE TABLE IF NOT EXISTS monitoring_continuity_acks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,action TEXT NOT NULL,
        start_date TEXT NOT NULL,end_date TEXT NOT NULL,signature TEXT NOT NULL,warnings_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));
    CREATE TABLE IF NOT EXISTS monitoring_gap_probes (
        id TEXT PRIMARY KEY,project_id INTEGER NOT NULL,machine_id INTEGER NOT NULL,
        start_row_id INTEGER NOT NULL,case_signature TEXT NOT NULL,status TEXT NOT NULL,
        result_json TEXT NOT NULL DEFAULT '{}',started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),finished_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_gap_probe_case ON monitoring_gap_probes(machine_id,start_row_id,started_at);
    """)


def point_snapshot(case,rows):
    lo,hi=str(case["start"]),str(case["end"])
    return {"kind":case["kind"],"start":lo,"end":hi,"before":case.get("before"),"after":case.get("after"),
            "points":[(r["id"],r["timestamp"],r["raw_data"]) for r in rows if lo[:19]<=str(r["timestamp"])[:19]<=hi[:19]]}


def signature_for_snapshot(conn,machine_id,snapshot):
    rows=conn.execute("SELECT id,timestamp,raw_data FROM monitoring_data WHERE machine_id=? AND timestamp>=? AND timestamp<=? ORDER BY timestamp,id",
                      (machine_id,snapshot["start"],snapshot["end"])).fetchall()
    return digest({**snapshot,"points":[tuple(r) for r in rows]})


def decision_valid(conn,machine_id,decision):
    if decision.get("invalidated_at"): return False
    if not decision.get("case_signature"): return decision.get("resolution") not in CONTINUOUS
    try: return signature_for_snapshot(conn,machine_id,json.loads(decision["snapshot_json"]))==decision["case_signature"]
    except (KeyError,TypeError,ValueError): return False


def invalidate_confirmations(conn,machine_id):
    for row in conn.execute("SELECT * FROM monitoring_quality_decisions WHERE machine_id=? AND case_signature<>'' AND invalidated_at=''",(machine_id,)).fetchall():
        decision=dict(row)
        if decision_valid(conn,machine_id,decision): continue
        conn.execute("UPDATE monitoring_quality_decisions SET invalidated_at=datetime('now','localtime') WHERE id=?",(row["id"],))
        conn.execute("INSERT INTO monitoring_quality_history (project_id,machine_id,kind,start_row_id,action,payload_json) VALUES (?,?,?,?,?,?)",
                     (row["project_id"],machine_id,row["kind"],row["start_row_id"],"invalidated",json.dumps(decision,ensure_ascii=False)))


def public_point(point):
    return {k:point.get(k) for k in ("id","timestamp","flow","power")} if point else None


def gap_metrics(before,after,earlier):
    from .monitoring_quality import _time
    seconds=(_time(after["timestamp"])-_time(before["timestamp"])).total_seconds()
    result={"seconds":seconds,"days":seconds/86400,"continuous":False,"classification":"insufficient_history","reference":{}}
    if any(p.get(k) is None or not math.isfinite(p[k]) for p in (before,after) for k in ("flow","power")): return result
    result["continuous"]=all(after[k]>=before[k] for k in ("flow","power"))
    result["delta"]={k:after[k]-before[k] for k in ("flow","power")}
    result["rate_per_day"]={k:result["delta"][k]/seconds*86400 for k in ("flow","power")}
    samples=[]
    for a,b in zip(earlier,earlier[1:]):
        ta,tb=_time(a["timestamp"]),_time(b["timestamp"])
        dt=(tb-ta).total_seconds() if ta and tb else 0
        if not 0<dt<=3*86400 or (_time(before["timestamp"])-tb).days>180: continue
        if any(p.get(k) is None for p in (a,b) for k in ("flow","power")): continue
        if all(b[k]>=a[k] for k in ("flow","power")):
            samples.append((ta,tb,{k:(b[k]-a[k])/dt*86400 for k in ("flow","power")}))
    result["sample_count"]=len(samples)
    if len(samples)<30 or (samples[-1][1]-samples[0][0]).total_seconds()<7*86400: return result
    for metric in ("flow","power"):
        values=sorted(p[2][metric] for p in samples)
        median=statistics.median(values);p95=values[int(.95*(len(values)-1))]
        result["reference"][metric]={"median_per_day":median,"p95_per_day":p95,"low":median*.25,"high":p95*2}
    if not result["continuous"]: result["classification"]="rollback"
    elif any(result["rate_per_day"][k]>result["reference"][k]["high"]>0 for k in ("flow","power")): result["classification"]="high_rate"
    elif any(result["rate_per_day"][k]<result["reference"][k]["low"] for k in ("flow","power")): result["classification"]="possible_shutdown"
    else: result["classification"]="within_reference"
    return result


def effective_resolution(case):
    decision=case.get("decision") or {}
    if decision and not case.get("decision_stale"): return decision.get("resolution","pending"),decision.get("baseline_row_id")
    if case["kind"]=="meter_drop":
        if case.get("recovered") and (case.get("recovery_seconds",float("inf"))<=3*86400 or case.get("restoration_jump")): return "exclude_event",None
        return "reset_epoch",(case.get("drop_ids") or [case["start_row_id"]])[-1]
    return "pending",None


def segments_for_machine(conn,machine_id,preview=False):
    from .monitoring_quality import _raw
    from .reduction_ledger import reconcile_machine
    changes={c["id"]:c for c in reconcile_machine(machine_id,conn=conn)} if preview else {}
    segments={}
    for row in conn.execute("SELECT * FROM monitoring_data WHERE machine_id=? ORDER BY timestamp,id",(machine_id,)):
        bd=changes[row["id"]]["breakdown"] if row["id"] in changes else _raw(row["breakdown"])
        amount=changes[row["id"]]["new_reduction"] if row["id"] in changes else float(row["carbon_reduction"] or 0)
        sid=bd.get("segment_id",next(iter(segments),row["id"]))
        raw=_raw(row["raw_data"]);point={"timestamp":row["timestamp"],"flow":raw.get("cumulative_flow"),"power":raw.get("cumulative_power")}
        segment=segments.setdefault(sid,{"id":sid,"machine_id":machine_id,"index":len(segments)+1,"start":point,"end":point,
                                        "reduction":0.0,"rows":0,"reason":bd.get("segment_reason","首条读数基准")})
        usable=not (bd.get("sampling_suppressed") or bd.get("excluded_invalid_reading") or (bd.get("reset_boundary") and not bd.get("segment_start")))
        if usable:
            if not segment.get("effective_rows"): segment["start"]=point
            segment["end"]=point
            segment["effective_rows"]=segment.get("effective_rows",0)+1
        segment["rows"]+=1;segment["reduction"]=round(segment["reduction"]+amount,6)
    return list(segments.values())


def normalize_period(start=None,end=None):
    now=datetime.now();start=start or (now-timedelta(days=30)).date().isoformat();end=end or now.date().isoformat()
    try: a=datetime.strptime(start,"%Y-%m-%d");b=datetime.strptime(end,"%Y-%m-%d")
    except (TypeError,ValueError): raise ValueError("核算期请填写 YYYY-MM-DD 日期")
    if a>b: raise ValueError("核算期起始日期不能晚于截止日期")
    return start,end


def preflight(conn,project_id,start=None,end=None):
    from .monitoring_quality import cases_for_machine
    start,end=normalize_period(start,end);warnings=[]
    machines=conn.execute("SELECT m.id,m.name,p.baseline_date FROM project_machines m JOIN carbon_projects p ON p.id=m.project_id WHERE m.project_id=? ORDER BY m.id",(project_id,)).fetchall()
    for machine in machines:
        cases,_=cases_for_machine(conn,machine["id"],machine["baseline_date"])
        for case in cases:
            if case["kind"] not in ("meter_drop","coverage_gap"): continue
            resolution,baseline=effective_resolution(case)
            if resolution in CONTINUOUS or resolution=="confirmed_inactive": continue
            if case["kind"]=="meter_drop" and resolution=="exclude_event": continue
            lo=(case.get("before") or {}).get("timestamp",case["start"]);hi=case["end"]
            if case["kind"]=="meter_drop" and baseline:
                hi=next((p["timestamp"] for p in case.get("candidate_points",[]) if p["id"]==baseline),case["start"])
            if not case.get("before") and hi[:10]<=start: continue
            if hi[:10]<start or lo[:10]>end: continue
            after=case.get("after")
            if case["kind"]=="meter_drop" and baseline:
                after=next((p for p in case.get("candidate_points",[]) if p["id"]==baseline),after)
            before_reduction=None
            if case.get("before"):
                record=conn.execute("SELECT breakdown,cumulative_reduction FROM monitoring_data WHERE id=?",(case["before"]["id"],)).fetchone()
                if record:
                    from .monitoring_quality import _raw
                    before_reduction=_raw(record["breakdown"]).get("segment_cumulative",record["cumulative_reduction"])
            warnings.append({"machine_id":machine["id"],"machine_name":machine["name"],"kind":case["kind"],"start":lo,"end":hi,
                             "before":case.get("before"),"after":after,"before_reduction":before_reduction,"metrics":case.get("metrics"),"probe":case.get("probe"),
                             "calculation":("新阶段基准，跨边界差额不计" if resolution in ("reset_epoch","start_epoch") else "首条读数作为基准，此前无可计算减排量" if not case.get("before") else "两端累计实测差计量；中途连续性尚未人工确认"),
                             "decision_stale":case.get("decision_stale",False)})
    rows=conn.execute("SELECT id,machine_id,timestamp,raw_data,carbon_reduction,breakdown FROM monitoring_data WHERE project_id=? AND timestamp>=? AND timestamp<=? ORDER BY machine_id,timestamp,id",(project_id,start,end+" 23:59:59")).fetchall()
    stable_warnings=[{k:v for k,v in warning.items() if k!="probe"} for warning in warnings]
    signature=digest([VERSION,project_id,start,end,stable_warnings,[tuple(r) for r in rows]])
    return {"start_date":start,"end_date":end,"warnings":warnings,"signature":signature,"requires_confirmation":bool(warnings)}


def acknowledge(conn,project_id,action,scope,token):
    if not scope["warnings"]: return None
    if token!=scope["signature"]: raise ValueError("数据提示尚未确认或数据已变化，请重新查看并确认")
    cur=conn.execute("INSERT INTO monitoring_continuity_acks (project_id,action,start_date,end_date,signature,warnings_json) VALUES (?,?,?,?,?,?)",
                     (project_id,action,scope["start_date"],scope["end_date"],token,json.dumps(scope["warnings"],ensure_ascii=False)))
    return cur.lastrowid
