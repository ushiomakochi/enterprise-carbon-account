"""Preview or atomically repair active-machine ledgers; apply while collectors are stopped."""
import argparse
import datetime
import json
import sqlite3
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import config
from core.project_manager import get_db,init_database
from core.reduction_ledger import reconcile_machine,refresh_totals
from core.meter_continuity import init_schema,segments_for_machine
from core.continuity_probe import machine_signature


def backup_database(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    src=sqlite3.connect(source);dst=sqlite3.connect(target)
    try: src.backup(dst)
    finally: src.close();dst.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply",action="store_true");parser.add_argument("--preview",action="store_true")
    parser.add_argument("--database",type=Path,default=Path(config.DATABASE_PATH))
    args=parser.parse_args()
    if args.apply==args.preview: parser.error("选择 --preview 或 --apply 之一")
    database=args.database.resolve();config.DATABASE_PATH=str(database)
    stamp=datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.apply:
        backup=ROOT/"data"/"backups"/("carbon_account_before_reconcile_"+stamp+".db")
        backup_database(database,backup);print("backup:",backup,flush=True);init_database()
    conn=get_db();memory=sqlite3.connect(":memory:");memory.row_factory=sqlite3.Row
    try:
        conn.backup(memory);init_schema(memory)
        machines=memory.execute("SELECT m.id,m.project_id FROM project_machines m JOIN carbon_projects p ON p.id=m.project_id WHERE p.lifecycle_status='active' AND m.lifecycle_status='active' ORDER BY m.project_id,m.id").fetchall()
        signatures={m["id"]:machine_signature(memory,m["id"]) for m in machines}
        outdir=ROOT/"data"/"repair_previews";outdir.mkdir(parents=True,exist_ok=True)
        output=outdir/("ledger_"+("apply" if args.apply else "preview")+"_"+stamp+".jsonl")
        projects={};all_changes=[]
        for machine in machines:
            mid,pid=machine["id"],machine["project_id"]
            before=memory.execute("SELECT lifetime_reduction,pending_verification_cumulative FROM carbon_projects WHERE id=?",(pid,)).fetchone()
            item=projects.setdefault(pid,{"rows":0,"machines":[],"reduction_delta":0.0,"annual_delta":{},"old_lifetime":before[0],"old_pending":before[1],"segments":{}})
            changes=reconcile_machine(mid,apply=True,conn=memory)
            item["rows"]+=len(changes);item["machines"].append(mid)
            for change in changes:
                delta=change["new_reduction"]-float(change["old_reduction"] or 0)
                year=change["timestamp"][:4]
                item["reduction_delta"]+=delta
                item["annual_delta"][year]=item["annual_delta"].get(year,0)+delta
                all_changes.append({"project_id":pid,"machine_id":mid,**change})
            item["segments"][mid]=segments_for_machine(memory,mid)
        for pid,item in projects.items():
            row=memory.execute("SELECT lifetime_reduction,pending_verification_cumulative,pending_status,pending_warnings FROM carbon_projects WHERE id=?",(pid,)).fetchone()
            item.update(new_lifetime=row[0],new_pending=row[1],pending_status=row[2],pending_warnings=json.loads(row[3] or '[]'))
            item["reduction_delta"]=round(item["reduction_delta"],6)
            item["annual_delta"]={y:round(v,6) for y,v in item["annual_delta"].items()}
        with output.open('w',encoding='utf-8') as file:
            for change in all_changes: file.write(json.dumps(change,ensure_ascii=False)+'\n')
        reports=[]
        for pid,item in projects.items():
            if item["rows"]:
                reports.extend(dict(r) for r in conn.execute("SELECT id,project_id,report_type,generated_at,file_path,parameters FROM reports WHERE project_id=? ORDER BY id",(pid,)))
        manifest=outdir/("summary_"+("apply" if args.apply else "preview")+"_"+stamp+".json")
        manifest_data={"database":str(database),"created_at":stamp,"applied":False,"diff_file":str(output),"projects":projects,"existing_reports_to_review":reports}
        if args.apply: manifest_data["backup_file"]=str(backup)
        manifest.write_text(json.dumps(manifest_data,ensure_ascii=False,indent=2),encoding='utf-8')
        print("diff:",output,flush=True);print("summary:",manifest,flush=True)
        print("projects:",json.dumps({pid:{"rows":i["rows"],"reduction_delta":i["reduction_delta"],"old_pending":i["old_pending"],"new_pending":i["new_pending"],"stages":sum(len(v) for v in i["segments"].values())} for pid,i in projects.items()},ensure_ascii=False),flush=True)
        print("existing reports:",len(reports),flush=True)
        if args.apply:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for m in machines:
                    if machine_signature(conn,m["id"])!=signatures[m["id"]]: raise RuntimeError("机器数据在预览期间变化，取消应用")
                from core.meter_continuity import invalidate_confirmations
                for m in machines: invalidate_confirmations(conn,m["id"])
                for change in all_changes:
                    conn.execute("UPDATE monitoring_data SET carbon_reduction=?,cumulative_reduction=?,breakdown=? WHERE id=?",(change["new_reduction"],change["new_cumulative"],json.dumps(change["breakdown"],ensure_ascii=False),change["id"]))
                for pid in projects: refresh_totals(conn,pid)
                conn.commit()
                manifest_data["applied"]=True
                manifest.write_text(json.dumps(manifest_data,ensure_ascii=False,indent=2),encoding='utf-8')
                print("applied rows:",len(all_changes),flush=True)
            except Exception: conn.rollback();raise
    finally: memory.close();conn.close()


if __name__=='__main__': main()
