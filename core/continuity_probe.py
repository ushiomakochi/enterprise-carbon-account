"""Serialized, source-bound gap probes. Raw source responses never leave the server."""
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from uuid import uuid4

from .project_manager import get_db
from .monitoring_quality import find_case,_raw,_time
from .meter_continuity import digest,CONTINUOUS

_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix="gap-probe")
_lock=threading.Lock()


def machine_signature(conn,machine_id):
    machine=conn.execute("SELECT data_source_config,normalizer_config,lifecycle_status FROM project_machines WHERE id=?",(machine_id,)).fetchone()
    rows=conn.execute("SELECT id,timestamp,raw_data,carbon_reduction,breakdown FROM monitoring_data WHERE machine_id=? ORDER BY timestamp,id",(machine_id,)).fetchall()
    decisions=conn.execute("SELECT * FROM monitoring_quality_decisions WHERE machine_id=? ORDER BY id",(machine_id,)).fetchall()
    return digest([tuple(machine),[tuple(r) for r in rows],[tuple(r) for r in decisions]])


def writable(conn,project_id,machine_id):
    row=conn.execute("SELECT p.lifecycle_status,m.lifecycle_status FROM project_machines m JOIN carbon_projects p ON p.id=m.project_id WHERE p.id=? AND m.id=?",(project_id,machine_id)).fetchone()
    if not row or tuple(row)!=("active","active"): raise ValueError("项目或机器已作废，回查已停止")


def start_probe(project_id,machine_id,start_row_id,force=False):
    with _lock:
        conn=get_db()
        try:
            writable(conn,project_id,machine_id)
            case,_=find_case(conn,project_id,machine_id,"coverage_gap",start_row_id)
            if not case.get("before"): raise ValueError("首条读数前没有 B 边界，请使用普通历史回溯补查")
            old=conn.execute("SELECT id,status,case_signature,result_json FROM monitoring_gap_probes WHERE machine_id=? AND start_row_id=? ORDER BY started_at DESC,id DESC LIMIT 1",(machine_id,start_row_id)).fetchone()
            if old and (old["status"] in ("queued","running") or (old["case_signature"]==case["case_signature"] and old["status"]!="interrupted" and not force)):
                return old["id"]
            if old and old["status"]=="interrupted" and old["case_signature"]==case["case_signature"]:
                conn.execute("UPDATE monitoring_gap_probes SET status='queued' WHERE id=?",(old["id"],));conn.commit()
                _executor.submit(run_probe,old["id"])
                return old["id"]
            job_id=uuid4().hex
            conn.execute("INSERT INTO monitoring_gap_probes (id,project_id,machine_id,start_row_id,case_signature,status) VALUES (?,?,?,?,?,'queued')",
                         (job_id,project_id,machine_id,start_row_id,case["case_signature"]))
            conn.commit()
        finally: conn.close()
        _executor.submit(run_probe,job_id)
        return job_id


def save_result(job_id,status,result):
    conn=get_db()
    try:
        conn.execute("UPDATE monitoring_gap_probes SET status=?,result_json=?,finished_at=CASE WHEN ?='running' THEN NULL ELSE datetime('now','localtime') END WHERE id=?",
                     (status,json.dumps(result,ensure_ascii=False),status,job_id));conn.commit()
    finally: conn.close()


def insert_missing(conn,project_id,machine_id,rows):
    writable(conn,project_id,machine_id)
    inserted=0
    for row in rows:
        if conn.execute("SELECT 1 FROM monitoring_data WHERE machine_id=? AND timestamp=? LIMIT 1",(machine_id,row["timestamp"])).fetchone(): continue
        conn.execute("INSERT INTO monitoring_data (project_id,machine_id,timestamp,raw_data,carbon_reduction,cumulative_reduction,breakdown,source) VALUES (?,?,?,?,0,0,'{}','auto')",
                     (project_id,machine_id,row["timestamp"],json.dumps(row,ensure_ascii=False)));inserted+=1
    return inserted


def accept_probe_evidence(conn,project_id,machine_id,reviewer,note):
    """Applying a source-import preview also accepts the evidence it displayed."""
    from .meter_continuity import invalidate_confirmations,point_snapshot
    from .monitoring_quality import cases_for_machine
    invalidate_confirmations(conn,machine_id)
    cases,_=cases_for_machine(conn,machine_id)
    rows=conn.execute("SELECT id,timestamp,raw_data FROM monitoring_data WHERE machine_id=? ORDER BY timestamp,id",(machine_id,)).fetchall()
    for case in cases:
        decision=case.get("decision") or {}
        if not case.get("decision_stale") or decision.get("resolution") not in CONTINUOUS: continue
        snapshot=point_snapshot(case,rows)
        conn.execute("UPDATE monitoring_quality_decisions SET resolution='accept_new_data',snapshot_json=?,case_signature=?,invalidated_at='',reviewer=?,note=?,updated_at=datetime('now','localtime') WHERE id=?",
                     (json.dumps(snapshot,ensure_ascii=False),digest(snapshot),reviewer,note,decision["id"]))
        conn.execute("INSERT INTO monitoring_quality_history (project_id,machine_id,kind,start_row_id,action,payload_json) VALUES (?,?,?,?,?,?)",
                     (project_id,machine_id,case["kind"],case["start_row_id"],"probe_evidence_accepted",json.dumps({"previous":decision,"resolution":"accept_new_data","reviewer":reviewer,"note":note,"snapshot":snapshot},ensure_ascii=False)))


def preview_rows(conn,project_id,machine_id,rows):
    from .reduction_ledger import reconcile_machine
    memory=sqlite3.connect(":memory:");memory.row_factory=sqlite3.Row
    conn.backup(memory)
    try:
        before=float(memory.execute("SELECT COALESCE(SUM(carbon_reduction),0) FROM monitoring_data WHERE machine_id=?",(machine_id,)).fetchone()[0])
        inserted=insert_missing(memory,project_id,machine_id,rows)
        accept_probe_evidence(memory,project_id,machine_id,"预览","按回查新证据重算")
        changes=reconcile_machine(machine_id,conn=memory)
        years={}
        for change in changes:
            year=change["timestamp"][:4]
            years[year]=years.get(year,0)+change["new_reduction"]-float(change["old_reduction"] or 0)
        return {"inserted":inserted,"rows_changed":len(changes),"old_total":before,
                "new_total":round(before+sum(years.values()),6),
                "annual_delta":{y:round(v,6) for y,v in years.items()},
                "new_boundaries":[{"timestamp":c["timestamp"],"reason":c["breakdown"].get("segment_reason")}
                                  for c in changes if c["breakdown"].get("segment_start")]}
    finally: memory.close()


def run_probe(job_id):
    from adapters.api_adapter import ApiAdapter
    from .backfill import _backfill_api_config
    from .data_collector import DataCollector
    conn=get_db();machine_id=None;paused=False
    result={"chunks":[],"rows":[],"conflicts":0}
    try:
        job=dict(conn.execute("SELECT * FROM monitoring_gap_probes WHERE id=?",(job_id,)).fetchone())
        machine_id=job["machine_id"];pid=job["project_id"]
        writable(conn,pid,machine_id)
        if machine_id in DataCollector._backfilling: raise ValueError("该机器正在历史回溯，请结束后重查")
        DataCollector._continuity_probing.add(machine_id);paused=True
        case,_=find_case(conn,pid,machine_id,"coverage_gap",job["start_row_id"])
        machine=dict(conn.execute("SELECT * FROM project_machines WHERE id=?",(machine_id,)).fetchone())
        config=_backfill_api_config(machine)
        if not config: raise ValueError("机器没有 API 回溯配置")
        result["signature"]=machine_signature(conn,machine_id)
        begin,end=_time(case["start"]),_time(case["end"])
        previous=_raw(job.get("result_json"));resume=previous.get("current") or {}
        retry_at=_time(resume.get("retry_at"))
        if resume.get("status")=="error" and retry_at:
            result["current"]=resume
            save_result(job_id,"running",result)
            time.sleep(max(0,(retry_at-datetime.now()).total_seconds()))
        records={}
        for granularity,chunk_days in (("day",28),("hour",7),("normal",1)):
            cursor=begin
            while cursor<end:
                writable(conn,pid,machine_id)
                if machine_id in DataCollector._backfilling: raise ValueError("普通历史回溯已启动，定向回查让出接口，请结束后重查")
                limit=min(cursor+timedelta(days=chunk_days),end)
                item={"granularity":granularity,"start":str(cursor),"end":str(limit)}
                attempts_done=(int(resume.get("attempts",0)) if resume.get("status")=="error" and resume.get("granularity")==granularity and resume.get("start")==str(cursor) and resume.get("end")==str(limit) else 0)
                if attempts_done>=3:
                    item.update(status="error",error=resume.get("error","前次重试次数已用完"),attempts=attempts_done)
                for attempt in range(attempts_done,3):
                    try:
                        adapter=ApiAdapter({**config,"record_time_type":granularity,"chunk_days":chunk_days,"max_records_per_run":10**9,"fill":0})
                        rows=adapter.fetch_range(cursor,limit)
                        if adapter._last_error: raise RuntimeError(adapter._last_error)
                        item.update(status="success" if rows else "empty",count=len(rows),attempts=attempt+1,
                                    first=rows[0]["timestamp"] if rows else None,last=rows[-1]["timestamp"] if rows else None)
                        for row in rows:
                            if begin<=_time(row["timestamp"])<=end:
                                records[row["timestamp"]]={**row,"record_time_type":granularity}
                        break
                    except Exception as exc:
                        item.update(status="error",error=str(exc)[:300],attempts=attempt+1)
                        if attempt==2 or any(code in str(exc) for code in ("400","401","403","404")): break
                        item["retry_at"]=(datetime.now()+timedelta(minutes=5)).isoformat()
                        save_result(job_id,"running",{**result,"current":item})
                        time.sleep(300)
                result["chunks"].append(item);save_result(job_id,"running",{**result,"current":item})
                cursor=limit
                time.sleep(.3)
        existing={r["timestamp"]:_raw(r["raw_data"]) for r in conn.execute("SELECT timestamp,raw_data FROM monitoring_data WHERE machine_id=? AND timestamp>=? AND timestamp<=?",(machine_id,case["start"],case["end"]))}
        for ts,row in sorted(records.items()):
            if ts not in existing: result["rows"].append(row)
            elif any(existing[ts].get(k)!=row.get(k) for k in ("cumulative_flow","cumulative_power")): result["conflicts"]+=1
        result["returned_count"]=len(records)
        result["preview"]=preview_rows(conn,pid,machine_id,result["rows"])
        result["summary"]=("已找到可补入读数；请预览后应用" if result["rows"] else "未找到可新增的中间读数；空响应不能证明设备故障或表计连续")
        save_result(job_id,"ready",result)
    except Exception as exc:
        result["error"]=str(exc)[:500];save_result(job_id,"failed",result)
    finally:
        conn.close()
        if paused: DataCollector._continuity_probing.discard(machine_id)


def refresh_probe(job_id):
    """Re-preview saved source evidence without issuing more source requests."""
    conn=get_db()
    try:
        job=conn.execute("SELECT * FROM monitoring_gap_probes WHERE id=?",(job_id,)).fetchone()
        if not job or job["status"]!="ready": raise ValueError("此任务尚不可刷新预览")
        result=_raw(job["result_json"]);mid,pid=job["machine_id"],job["project_id"]
        writable(conn,pid,mid)
        signature=machine_signature(conn,mid)
        result["preview"]=preview_rows(conn,pid,mid,result["rows"])
        if machine_signature(conn,mid)!=signature: raise ValueError("刷新期间数据变化，请稍后重试")
        result["signature"]=signature
        conn.execute("UPDATE monitoring_gap_probes SET result_json=? WHERE id=?",(json.dumps(result,ensure_ascii=False),job_id));conn.commit()
        return {k:v for k,v in result.items() if k not in ("rows","signature")}
    finally: conn.close()


def apply_probe(job_id,reviewer,note):
    from pathlib import Path
    import config
    from .reduction_ledger import reconcile_machine
    if not reviewer.strip() or not note.strip(): raise ValueError("请填写操作人和应用原因")
    conn=get_db()
    try:
        job=conn.execute("SELECT * FROM monitoring_gap_probes WHERE id=?",(job_id,)).fetchone()
        if not job or job["status"]!="ready": raise ValueError("回查结果尚不可应用或已经应用")
        result=_raw(job["result_json"]);mid,pid=job["machine_id"],job["project_id"]
        writable(conn,pid,mid)
        # Full backup before the writer transaction; stale check is repeated under lock.
        backup=Path(config.data_path("backups"))/("before_gap_probe_"+uuid4().hex+".db")
        backup.parent.mkdir(parents=True,exist_ok=True)
        target=sqlite3.connect(backup)
        try: conn.backup(target)
        finally: target.close()
        conn.execute("BEGIN IMMEDIATE")
        if machine_signature(conn,mid)!=result.get("signature"): raise ValueError("预览后机器数据已变化，请刷新此回查预览")
        inserted=insert_missing(conn,pid,mid,result["rows"])
        accept_probe_evidence(conn,pid,mid,reviewer,note)
        changes=reconcile_machine(mid,apply=True,conn=conn)
        result["applied"]={"inserted":inserted,"reviewer":reviewer,"note":note,"backup":str(backup)}
        conn.execute("UPDATE monitoring_gap_probes SET status='applied',result_json=? WHERE id=?",(json.dumps(result,ensure_ascii=False),job_id))
        conn.execute("INSERT INTO monitoring_quality_history (project_id,machine_id,kind,start_row_id,action,payload_json) VALUES (?,?,?,?,?,?)",
                     (pid,mid,"coverage_gap",job["start_row_id"],"probe_applied",json.dumps(result["applied"],ensure_ascii=False)))
        conn.commit();return result["applied"]
    except Exception:
        conn.rollback();raise
    finally: conn.close()


def schedule_gaps():
    from .monitoring_quality import cases_for_machine
    conn=get_db()
    try:
        machines=conn.execute("SELECT m.id,m.project_id,p.baseline_date FROM project_machines m JOIN carbon_projects p ON p.id=m.project_id WHERE p.lifecycle_status='active' AND m.lifecycle_status='active' AND (m.data_source_type='api' OR m.backfill_method='api')").fetchall()
        for machine in machines:
            cases,_=cases_for_machine(conn,machine["id"],machine["baseline_date"])
            for case in cases:
                if case["kind"]=="coverage_gap" and case.get("before") and case.get("effective_resolution") not in CONTINUOUS:
                    start_probe(machine["project_id"],machine["id"],case["start_row_id"])
    finally: conn.close()


def start_scheduler():
    conn=get_db()
    try:
        conn.execute("UPDATE monitoring_gap_probes SET status='interrupted' WHERE status IN ('queued','running')");conn.commit()
    finally: conn.close()
    def loop():
        import logging
        while True:
            try: schedule_gaps()
            except Exception: logging.getLogger(__name__).exception("缺口定向回查调度失败")
            time.sleep(3600)
    threading.Thread(target=loop,daemon=True,name="gap-probe-scheduler").start()
