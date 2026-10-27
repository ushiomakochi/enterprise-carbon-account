"""Local retirement archive for VCER projects.

Voiding is committed before any remote I/O, so a failed attachment download
never re-enables collection. Official VCER records are copied, never changed.
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote
from datetime import datetime

from .project_manager import get_db, get_project


def _archive_root(project_id):
    import config
    root = Path(config.data_path("project_archives", str(project_id)))
    root.mkdir(parents=True, exist_ok=True)
    return root


def is_active(project_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT lifecycle_status FROM carbon_projects WHERE id=?",
                           (project_id,)).fetchone()
        return bool(row and row["lifecycle_status"] == "active")
    finally:
        conn.close()


def void_project(project_id, reason, actor="本机经办人"):
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("请填写作废原因")
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT lifecycle_status FROM carbon_projects WHERE id=?",
                           (project_id,)).fetchone()
        if not row:
            raise ValueError("项目不存在")
        if row["lifecycle_status"] != "active":
            raise ValueError("项目已经停用")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE carbon_projects SET lifecycle_status='archiving',"
                     "void_reason=?,voided_at=?,updated_at=? WHERE id=?",
                     (reason, now, now, project_id))
        conn.execute("INSERT INTO project_lifecycle_events "
                     "(project_id,action,reason,actor) VALUES (?,?,?,?)",
                     (project_id, "void", reason, actor))
        conn.commit()
    finally:
        conn.close()
    # Drop already-subscribed MQTT/API adapters as soon as the stop is committed.
    try:
        from .data_collector import DataCollector
        from .project_manager import list_machines
        for machine in list_machines(project_id):
            DataCollector.drop_adapter(machine["id"])
    except Exception:
        pass
    try:
        return retry_archive(project_id)
    except Exception as exc:
        return mark_archive_failure(project_id, str(exc))


def mark_archive_failure(project_id, error):
    """Keep the project disabled and expose unexpected archive errors for retry."""
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT manifest_json FROM project_archive WHERE project_id=?",
                           (project_id,)).fetchone()
        manifest = row["manifest_json"] if row else "{}"
        errors = json.dumps([error], ensure_ascii=False)
        conn.execute("INSERT INTO project_archive (project_id,manifest_json,status,errors_json) "
                     "VALUES (?,?,'archive_incomplete',?) ON CONFLICT(project_id) "
                     "DO UPDATE SET status='archive_incomplete',errors_json=excluded.errors_json,"
                     "updated_at=datetime('now','localtime')", (project_id, manifest, errors))
        conn.execute("UPDATE carbon_projects SET lifecycle_status='archive_incomplete' "
                     "WHERE id=? AND lifecycle_status!='active'", (project_id,))
        conn.commit()
    finally:
        conn.close()
    return {"status": "归档未完成", "lifecycle_status": "archive_incomplete",
            "errors": [error], "files": 0}


def _paths(node, prefix=""):
    """Find file paths inside stage and round payloads without guessing filenames."""
    if isinstance(node, dict):
        for key, value in node.items():
            label = (prefix + "." + key).strip(".")
            if isinstance(value, (dict, list)):
                yield from _paths(value, label)
            elif isinstance(value, str):
                stripped = value.strip()
                if stripped.startswith(("{", "[")):
                    try:
                        decoded = json.loads(stripped)
                        if isinstance(decoded, (dict, list)):
                            yield from _paths(decoded, label)
                            continue
                    except ValueError:
                        pass
                low = label.lower()
                if any(part in low for part in ("file", "attachment", "proof",
                                                "document", "certificate", "pdd",
                                                "report_path")) and ("/" in value or "\\" in value):
                    yield label, value
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _paths(value, f"{prefix}[{i}]")


def retry_archive(project_id):
    project = get_project(project_id)
    if not project or project["lifecycle_status"] == "active":
        raise ValueError("只有停用项目可归档")
    root = _archive_root(project_id)
    errors, files = [], []
    conn = get_db()
    try:
        old = conn.execute("SELECT manifest_json FROM project_archive WHERE project_id=?",
                           (project_id,)).fetchone()
        previous = json.loads(old["manifest_json"]) if old else {}
    finally:
        conn.close()
    payload = {"project": {k: v for k, v in project.items()
                           if k not in ("vcer_query_pwd", "vcer_query_key")},
               "stages": previous.get("stages") or {},
               "rounds": previous.get("rounds") or {},
               "reports": previous.get("reports") or []}
    key = project.get("vcer_query_key")
    if key:
        import config
        from .vcer_client import VCERClient
        client = VCERClient(config.VCER_API_URL)
        try:
            status = client.get_project_status(key, project.get("vcer_query_pwd"))
            if not isinstance(status, dict) or not (status.get("success") or status.get("status") == "成功"):
                raise ValueError(status.get("message") or status.get("msg") or "状态接口失败")
            payload["stages"] = status
        except Exception as exc:
            errors.append(f"促进会阶段状态: {exc}")
        for stage in range(1, 6):
            try:
                result = client.get_stage_rounds(key, stage)
                if not isinstance(result, dict) or not (result.get("success") or result.get("status") == "成功"):
                    raise ValueError(result.get("message") or result.get("msg") or "轮次接口失败")
                payload["rounds"][str(stage)] = result
            except Exception as exc:
                errors.append(f"第 {stage} 阶段轮次: {exc}")
    elif project.get("project_code"):
        errors.append("已登记项目没有查询密钥，无法归档促进会阶段与附件")
    conn = get_db()
    try:
        reports = conn.execute("SELECT * FROM reports WHERE project_id=?",
                               (project_id,)).fetchall()
        payload["reports"] = [dict(r) for r in reports]
    except Exception as exc:
        errors.append(f"本地报告清单: {exc}")
    finally:
        conn.close()
    # Preserve generated report files alongside their database metadata.
    for report in payload["reports"]:
        report_path = report.get("file_path")
        if not report_path:
            continue
        try:
            from config import resolve_data_path
            source = Path(resolve_data_path(report_path))
            if not source.is_file():
                raise FileNotFoundError(report_path)
            name = "report-" + str(report.get("id")) + source.suffix.lower()
            target = root / "reports" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            contents = target.read_bytes()
            files.append({"field": "local_report", "source_path": report_path,
                          "archive_path": str(target.relative_to(root)).replace(chr(92), "/"),
                          "size": len(contents),
                          "sha256": hashlib.sha256(contents).hexdigest()})
        except Exception as exc:
            errors.append(f"本地报告 {report.get('id')}: {exc}")
    raw_paths = dict(_paths({"stages": payload["stages"], "rounds": payload["rounds"]}))
    if raw_paths:
        import requests
        import config
        base = config.VCER_API_URL.rstrip("/")
        if base.endswith("/cn"):
            base = base[:-3]
        for label, path in raw_paths.items():
            if path.startswith(("http://", "https://")):
                errors.append(f"{label}: 附件给出外部网址，需人工核对")
                continue
            name = hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]
            target = root / "attachments" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                if not target.exists():
                    url = base + "/db/api/file_view?path=" + quote(path, safe="") + "&download=1"
                    resp = requests.get(url, timeout=30)
                    if resp.status_code >= 400:
                        raise ValueError(f"HTTP {resp.status_code}")
                    if not resp.content:
                        raise ValueError("附件为空")
                    if "json" in resp.headers.get("Content-Type", "").lower() or (
                            resp.content[:30].lstrip().lower().startswith(b"<html")):
                        raise ValueError("附件接口返回错误页面")
                    target.write_bytes(resp.content)
                contents = target.read_bytes()
                files.append({"field": label, "source_path": path,
                              "archive_path": str(target.relative_to(root)),
                              "size": len(contents),
                              "sha256": hashlib.sha256(contents).hexdigest()})
            except Exception as exc:
                errors.append(f"{label}: {exc}")
    old_files = {f.get("archive_path"): f for f in previous.get("files", [])}
    for f in files:
        old_files[f.get("archive_path")] = f
    files = list(old_files.values())
    payload["files"] = files
    payload["archived_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        state = "archive_incomplete" if errors else "voided"
        conn.execute("INSERT INTO project_archive (project_id,manifest_json,status,errors_json) "
                     "VALUES (?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET "
                     "manifest_json=excluded.manifest_json,status=excluded.status,"
                     "errors_json=excluded.errors_json,updated_at=datetime('now','localtime')",
                     (project_id, json.dumps(payload, ensure_ascii=False), state,
                      json.dumps(errors, ensure_ascii=False)))
        conn.execute("UPDATE carbon_projects SET lifecycle_status=?,"
                     "updated_at=datetime('now','localtime') WHERE id=? AND lifecycle_status!='active'",
                     (state, project_id))
        conn.commit()
    finally:
        conn.close()
    return {"status": "成功" if not errors else "归档未完成",
            "lifecycle_status": state, "errors": errors, "files": len(files)}


def restore_project(project_id, reason, actor="本机经办人"):
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("请填写恢复原因，并确认已核对站点、设备与核查范围")
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT lifecycle_status FROM carbon_projects WHERE id=?",
                           (project_id,)).fetchone()
        if not row or row["lifecycle_status"] == "active":
            raise ValueError("项目不存在或正在使用")
        conn.execute("UPDATE carbon_projects SET lifecycle_status='active',"
                     "updated_at=datetime('now','localtime') WHERE id=?", (project_id,))
        conn.execute("INSERT INTO project_lifecycle_events "
                     "(project_id,action,reason,actor) VALUES (?,?,?,?)",
                     (project_id, "restore", reason, actor))
        conn.commit()
    finally:
        conn.close()
    return {"status": "成功", "msg": "已恢复使用；停用期间数据不会自动补采"}


def archive_detail(project_id):
    conn = get_db()
    try:
        archive = conn.execute("SELECT * FROM project_archive WHERE project_id=?",
                               (project_id,)).fetchone()
        events = conn.execute("SELECT * FROM project_lifecycle_events WHERE project_id=? "
                              "ORDER BY id DESC", (project_id,)).fetchall()
        return {"archive": dict(archive) if archive else None,
                "events": [dict(x) for x in events]}
    finally:
        conn.close()



def change_machine_status(project_id, machine_id, action, reason, actor="本机经办人"):
    """Retire or restore a physical machine without deleting its historical ledger."""
    reason = (reason or "").strip()
    if action not in ("void", "restore") or not reason:
        raise ValueError("请填写机器撤走或恢复的原因")
    target = "voided" if action == "void" else "active"
    expected = "active" if action == "void" else "voided"
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT m.lifecycle_status, p.lifecycle_status AS project_status "
            "FROM project_machines m JOIN carbon_projects p ON p.id=m.project_id "
            "WHERE m.id=? AND m.project_id=?", (machine_id, project_id)).fetchone()
        if not row:
            raise ValueError("机器不存在")
        if row["project_status"] != "active":
            raise ValueError("项目已作废，请先恢复项目")
        if row["lifecycle_status"] != expected:
            raise ValueError("机器当前状态不允许此操作")
        conn.execute(
            "UPDATE project_machines SET lifecycle_status=?,void_reason=?,"
            "voided_at=CASE WHEN ?='voided' THEN datetime('now','localtime') ELSE voided_at END,"
            "updated_at=datetime('now','localtime') WHERE id=?",
            (target, reason if action == "void" else None, target, machine_id))
        conn.execute(
            "INSERT INTO machine_lifecycle_events (machine_id,project_id,action,reason,actor) "
            "VALUES (?,?,?,?,?)", (machine_id, project_id, action, reason, actor))
        conn.commit()
    finally:
        conn.close()
    return {"status": "成功", "msg": "机器已作废，历史数据保留" if action == "void"
            else "机器已恢复；停用期间数据不会自动补采"}


def machine_events(machine_id):
    conn = get_db()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM machine_lifecycle_events WHERE machine_id=? ORDER BY id DESC",
            (machine_id,)).fetchall()]
    finally:
        conn.close()
