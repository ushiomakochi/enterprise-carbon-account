"""
项目编号预分配 — 解决「PDD 需要编号，但编号需内部审核通过才发放」的死循环。

机制（先预订、后转正、空号不递补）：
1. 项目需要编号时先「预分配」：新号 = max(本地预分配最大序号, VCER 平台已注册
   最大序号) + 1，事务内连号，批量申请不会撞号；PDD 直接用该编号编制。
2. 内部审核通过（阶段1/2 已通过）→ 编号「转正」（同步项目状态时自动检测）。
3. 项目废弃 → 编号「作废」留痕，不递补（编号是审计锚点，作废后新项目用新号）。

存储：code_reservations 表（本地），因为 VCER 平台接口只反映已审核通过的项目。
"""

import os
import re
from datetime import datetime

from .project_manager import get_db


def ensure_table():
    """建编号预留表（幂等）。"""
    conn = get_db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS code_reservations (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                enterprise_id TEXT    NOT NULL,
                seq           INTEGER NOT NULL,
                full_code     TEXT    NOT NULL UNIQUE,
                project_id    INTEGER,
                status        TEXT    NOT NULL DEFAULT '预分配',
                remark        TEXT    DEFAULT '',
                created_at    TEXT    DEFAULT (datetime('now','localtime')),
                updated_at    TEXT    DEFAULT (datetime('now','localtime')),
                UNIQUE (enterprise_id, seq)
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _get_enterprise_id():
    from .project_manager import get_enterprise_config
    cfg = get_enterprise_config() or {}
    return (cfg.get("enterprise_id") or "").strip()


def _remote_max_seq(enterprise_id):
    """查询 VCER 平台该企业已注册项目的最大序号。

    返回: (max_seq, None)；接口失败返回 (None, 错误信息)。
    """
    try:
        import config as app_config
        from .vcer_client import VCERClient
        from .project_manager import get_enterprise_config
        cfg = get_enterprise_config() or {}
        client = VCERClient(cfg.get("vcer_api_url") or app_config.VCER_API_URL,
                            cfg.get("api_access_key"))
        data = client.get_carbon_projects(enterprise_id)
        remote = []
        if isinstance(data, dict):
            remote = data.get("projects") or data.get("project") or data.get("data") or []
        elif isinstance(data, list):
            remote = data
        max_seq = 0
        pattern = re.compile(rf"GDES-VCER-{re.escape(enterprise_id)}-(\d+)$")
        for r in remote:
            if not isinstance(r, dict):
                continue
            code = (r.get("project_code") or "").strip()
            m = pattern.match(code)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
        return max_seq, None
    except Exception as e:
        return None, str(e)


def reserve_code(project_id, remark=""):
    """为项目预分配编号（事务内连号，批量调用不会重号）。

    返回: {"status": "成功", "code": "GDES-VCER-036-9", "msg": ...}
    """
    ensure_table()
    enterprise_id = _get_enterprise_id()
    if not enterprise_id:
        return {"status": "失败", "msg": "请先在「设置」中填写企业序号"}

    conn = get_db()
    try:
        proj = conn.execute(
            "SELECT id, project_name, project_code FROM carbon_projects WHERE id = ?",
            (project_id,)).fetchone()
        if not proj:
            return {"status": "失败", "msg": "项目不存在"}

        # 已有预分配中的编号 → 直接返回（幂等）
        existing = conn.execute(
            "SELECT full_code FROM code_reservations "
            "WHERE project_id = ? AND status = '预分配' ORDER BY id DESC LIMIT 1",
            (project_id,)).fetchone()
        if existing:
            return {"status": "成功", "code": existing["full_code"],
                    "msg": f"该项目已有预分配编号 {existing['full_code']}", "existing": True}

        # ── 事务：取号 + 写预留 + 写项目编号（防并发重号）──
        conn.execute("BEGIN IMMEDIATE")

        # 本地最大序号（含已作废——作废不递补，序号只增不减）
        local_max = conn.execute(
            "SELECT MAX(seq) AS m FROM code_reservations WHERE enterprise_id = ?",
            (enterprise_id,)).fetchone()["m"] or 0

        # 本地项目表中已占用的同企业编号序号（避免与存量项目撞号）
        proj_max = 0
        code_pattern = re.compile(rf"GDES-VCER-{re.escape(enterprise_id)}-(\d+)$")
        for r in conn.execute(
                "SELECT project_code FROM carbon_projects WHERE project_code LIKE ?",
                (f"GDES-VCER-{enterprise_id}-%",)).fetchall():
            m = code_pattern.match((r[0] or "").strip())
            if m:
                proj_max = max(proj_max, int(m.group(1)))

        # 远程最大序号（接口失败仅告警，按本地序号分配）
        remote_max, remote_err = _remote_max_seq(enterprise_id)
        warn = None
        if remote_max is None:
            remote_max = 0
            warn = f"未能连接平台校验（{remote_err}），已按本地序号分配，请注意核对"

        # 项目当前编号的序号（如有）作为下限
        cur_seq = 0
        if proj["project_code"]:
            m = code_pattern.match(proj["project_code"].strip())
            if m:
                cur_seq = int(m.group(1))

        next_seq = max(local_max, proj_max, remote_max, cur_seq) + 1
        full_code = f"GDES-VCER-{enterprise_id}-{next_seq}"

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """INSERT INTO code_reservations
               (enterprise_id, seq, full_code, project_id, status, remark, created_at, updated_at)
               VALUES (?, ?, ?, ?, '预分配', ?, ?, ?)""",
            (enterprise_id, next_seq, full_code, project_id, remark, now, now))
        conn.execute(
            "UPDATE carbon_projects SET project_code = ?, updated_at = ? WHERE id = ?",
            (full_code, now, project_id))
        conn.commit()

        result = {"status": "成功", "code": full_code,
                  "msg": f"已预分配编号 {full_code}（审核通过后自动转正）"}
        if warn:
            result["warn"] = warn
        return result
    except Exception as e:
        conn.rollback()
        return {"status": "失败", "msg": str(e)}
    finally:
        conn.close()


def confirm_if_passed(project_id):
    """项目阶段同步后调用：阶段1/2 任一已通过，则把该项目预分配编号转正。

    返回: True=有编号被转正。
    """
    ensure_table()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT stage1_passed, stage2_passed FROM carbon_projects WHERE id = ?",
            (project_id,)).fetchone()
        if not row:
            return False
        if not (row["stage1_passed"] or row["stage2_passed"]):
            return False
        cur = conn.execute(
            """UPDATE code_reservations SET status = '已转正', updated_at = ?
               WHERE project_id = ? AND status = '预分配'""",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), project_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def void_code(project_id, reason=""):
    """作废项目的预分配编号（留痕，不递补）。已转正的不允许作废。"""
    ensure_table()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT status FROM code_reservations "
            "WHERE project_id = ? ORDER BY id DESC LIMIT 1",
            (project_id,)).fetchone()
        if not row:
            return {"status": "失败", "msg": "该项目没有预分配编号"}
        if row["status"] == "已转正":
            return {"status": "失败", "msg": "编号已转正（平台已注册），不能作废"}
        if row["status"] == "已作废":
            return {"status": "失败", "msg": "编号已是作废状态"}

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """UPDATE code_reservations
               SET status = '已作废', remark = remark || ?, updated_at = ?
               WHERE project_id = ? AND status = '预分配'""",
            (f"（作废：{reason}）" if reason else "（作废）", now, project_id))
        # 清空项目编号，允许重新预分配新号
        conn.execute(
            "UPDATE carbon_projects SET project_code = NULL, updated_at = ? WHERE id = ?",
            (now, project_id))
        conn.commit()
        return {"status": "成功", "msg": "编号已作废留痕，可重新预分配新号"}
    except Exception as e:
        conn.rollback()
        return {"status": "失败", "msg": str(e)}
    finally:
        conn.close()


def list_reservations():
    """列出全部编号预留记录（新→旧）。"""
    ensure_table()
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT r.*, p.project_name
               FROM code_reservations r
               LEFT JOIN carbon_projects p ON p.id = r.project_id
               ORDER BY r.enterprise_id, r.seq"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
