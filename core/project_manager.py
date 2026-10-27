"""
项目管理模块 — 碳项目的 CRUD、阶段状态跟踪、本地碳资产同步。
"""

import sqlite3
import json
from datetime import datetime


def get_db():
    """获取 SQLite 数据库连接"""
    from config import DATABASE_PATH
    import os
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 网络映射盘（SMB）上锁不可靠，不开 WAL；仅放宽同步级别与锁等待。
    # 采集器后台线程会持续写库（逐条入账），前端保存/回溯也会写库，
    # 并发写时靠 busy_timeout 等待对方释放锁（默认 30 秒）。
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _run_with_db_lock_retry(op, retries=5):
    """执行写操作 op()（返回 dict）；遇 sqlite3 'database is locked' 自动重试。

    采集器后台线程会持续写库（逐条入账），前端保存/回溯也会写库，并发写时
    可能撞锁。op 内部遇锁应抛 sqlite3.OperationalError（含 "locked"），
    其他异常/结果原样返回。
    """
    import time as _time
    last = None
    for i in range(retries):
        try:
            return op()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                return {"status": "失败", "msg": str(e)}
            last = e
            if i < retries - 1:
                _time.sleep(0.4 * (i + 1))
    return {"status": "失败", "msg": f"数据库忙，重试 {retries} 次后仍失败: {last}"}


def init_database():
    """初始化数据库：执行建表脚本"""
    import os
    from config import BASE_DIR

    schema_path = os.path.join(BASE_DIR, "database", "schema_sqlite.sql")
    if not os.path.exists(schema_path):
        print(f"[WARN] 未找到建表脚本: {schema_path}")
        return

    conn = get_db()
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            sql = f.read()
        conn.executescript(sql)
        conn.commit()

        # ── 向后兼容迁移：旧数据库缺少列时自动补 ──
        _migrate_schema(conn)

        print("[OK] 数据库初始化完成")
    except Exception as e:
        print(f"[ERROR] 数据库初始化失败: {e}")
        raise
    finally:
        conn.close()


def _migrate_schema(conn):
    """向后兼容：为旧数据库自动添加缺失的列"""
    _ensure_column(conn, "carbon_projects", "presets", "TEXT DEFAULT '[]'")
    _ensure_column(conn, "enterprise_config", "annual_code_enabled", "INTEGER DEFAULT 0")
    _ensure_column(conn, "enterprise_config", "dev_mode", "INTEGER DEFAULT 0")
    _ensure_column(conn, "enterprise_config", "history_api_base", "TEXT DEFAULT ''")
    _ensure_column(conn, "enterprise_config", "history_api_username", "TEXT DEFAULT ''")
    _ensure_column(conn, "enterprise_config", "history_api_password", "TEXT DEFAULT ''")
    _ensure_column(conn, "project_machines", "backfill_method", "TEXT DEFAULT 'none'")
    _ensure_column(conn, "project_machines", "backfill_config", "TEXT DEFAULT '{}'")
    _ensure_column(conn, "project_machines", "normalizer_config", "TEXT DEFAULT '{}'")
    _ensure_column(conn, "carbon_projects", "projection_10yr", "TEXT DEFAULT NULL")
    # 阶段通过布尔列：旧库新增时，用旧的 stageN_status 回填一次（之后不再读旧列）
    for s in range(1, 6):
        added = _ensure_column(conn, "carbon_projects", f"stage{s}_passed", "INTEGER DEFAULT 0")
        if added:
            try:
                conn.execute(
                    f"UPDATE carbon_projects SET stage{s}_passed = 1 WHERE stage{s}_status = '已通过'"
                )
                conn.commit()
            except Exception as e:
                print(f"[migrate] 回填 stage{s}_passed 失败（可忽略）: {e}")

    # 系统日志表（「日志」页签数据源）。建表脚本只在 init_database 时执行，
    # 老库不会重新跑，所以这里按幂等方式补一次。
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS system_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT    NOT NULL,
                level       INTEGER NOT NULL,
                level_name  TEXT    NOT NULL,
                logger      TEXT    DEFAULT NULL,
                message     TEXT    NOT NULL,
                detail      TEXT    DEFAULT NULL,
                project_id  INTEGER DEFAULT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_system_logs_level ON system_logs(level);
            CREATE INDEX IF NOT EXISTS idx_system_logs_ts ON system_logs(ts);
        """)
        conn.commit()
        # 老库补 project_id 列 + 索引
        _ensure_column(conn, "system_logs", "project_id", "INTEGER DEFAULT NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_system_logs_project "
                     "ON system_logs(project_id, level, id)")
        conn.commit()
    except Exception as e:
        print(f"[migrate] 建 system_logs 表失败（可忽略）: {e}")

    # ── 多机器改造：monitoring_data.machine_id + project_machines 表 ──
    # 表本身由建表脚本（CREATE TABLE IF NOT EXISTS）建，这里只负责补列、建索引、
    # 以及把「一项目一台机器」的历史数据搬到新模型上。
    _ensure_column(conn, "monitoring_data", "machine_id", "INTEGER DEFAULT NULL")
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_machine_project "
                     "ON project_machines(project_id, sort_order, id)")
        # 按机器查上一期数据（collect_and_store）与按机器汇总（投影）都走这条
        conn.execute("CREATE INDEX IF NOT EXISTS idx_monitor_machine_ts "
                     "ON monitoring_data(machine_id, timestamp, id)")
        # project_cumulative 里「未分配机器(machine_id IS NULL)」的历史行查询，
        # 以及按项目过滤，都走这条 —— 没有它会全表扫描，回溯越到后面越慢（O(n²)）
        conn.execute("CREATE INDEX IF NOT EXISTS idx_monitor_project_machine_ts "
                     "ON monitoring_data(project_id, machine_id, timestamp, id)")
        conn.commit()
    except Exception as e:
        print(f"[migrate] 建机器索引失败（可忽略）: {e}")

    # Lifecycle and verified-period accounting are independent of official VCER status.
    _ensure_column(conn, "carbon_projects", "lifetime_reduction", "REAL DEFAULT 0")
    newly_tracking_pending = _ensure_column(conn, "carbon_projects", "pending_status", "TEXT DEFAULT 'unknown'")
    if newly_tracking_pending:
        conn.execute("UPDATE carbon_projects SET pending_verification_cumulative=NULL")
        conn.commit()
    _ensure_column(conn, "carbon_projects", "pending_warnings", "TEXT DEFAULT '[]'")
    _ensure_column(conn, "carbon_projects", "lifecycle_status", "TEXT DEFAULT 'active'")
    _ensure_column(conn, "carbon_projects", "void_reason", "TEXT DEFAULT NULL")
    _ensure_column(conn, "carbon_projects", "voided_at", "TEXT DEFAULT NULL")
    _ensure_column(conn, "project_machines", "lifecycle_status", "TEXT DEFAULT 'active'")
    _ensure_column(conn, "project_machines", "void_reason", "TEXT DEFAULT NULL")
    _ensure_column(conn, "project_machines", "voided_at", "TEXT DEFAULT NULL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS verification_snapshots (
            project_id INTEGER PRIMARY KEY REFERENCES carbon_projects(id) ON DELETE CASCADE,
            rounds_json TEXT NOT NULL, synced_at TEXT NOT NULL,
            status TEXT NOT NULL, error TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS project_lifecycle_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL, action TEXT NOT NULL,
            reason TEXT NOT NULL, actor TEXT DEFAULT '本机经办人',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS machine_lifecycle_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id INTEGER NOT NULL, project_id INTEGER NOT NULL,
            action TEXT NOT NULL, reason TEXT NOT NULL,
            actor TEXT DEFAULT '本机经办人',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS project_archive (
            project_id INTEGER PRIMARY KEY REFERENCES carbon_projects(id) ON DELETE CASCADE,
            manifest_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'incomplete',
            errors_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS monitoring_review_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monitoring_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            note TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_monitor_review_latest
            ON monitoring_review_events(monitoring_id,id DESC);
        CREATE INDEX IF NOT EXISTS idx_monitor_gap_summary
            ON monitoring_data(project_id)
            WHERE json_valid(breakdown) AND json_extract(breakdown,'$.coverage_gap')=1;
        CREATE TABLE IF NOT EXISTS monitoring_quality_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            machine_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            start_row_id INTEGER NOT NULL,
            resolution TEXT NOT NULL DEFAULT 'pending',
            baseline_row_id INTEGER,
            reviewer TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            evidence TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(machine_id,kind,start_row_id)
        );
        CREATE INDEX IF NOT EXISTS idx_quality_project
            ON monitoring_quality_decisions(project_id,machine_id);
    """)
    conn.commit()
    from .meter_continuity import init_schema as init_continuity_schema
    init_continuity_schema(conn)
    conn.commit()
    _migrate_project_machines(conn)
    # SQLite evaluates these under the writer lock. An already-running worker
    # cannot commit a new row after the void transaction has committed.
    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS monitoring_void_insert
        BEFORE INSERT ON monitoring_data
        WHEN (SELECT lifecycle_status FROM carbon_projects WHERE id=NEW.project_id)!='active'
          OR (NEW.machine_id IS NOT NULL AND
              (SELECT lifecycle_status FROM project_machines WHERE id=NEW.machine_id)!='active')
        BEGIN SELECT RAISE(ABORT,'project or machine is voided'); END;
        CREATE TRIGGER IF NOT EXISTS monitoring_void_update
        BEFORE UPDATE ON monitoring_data
        WHEN (SELECT lifecycle_status FROM carbon_projects WHERE id=NEW.project_id)!='active'
          OR (NEW.machine_id IS NOT NULL AND
              (SELECT lifecycle_status FROM project_machines WHERE id=NEW.machine_id)!='active')
        BEGIN SELECT RAISE(ABORT,'project or machine is voided'); END;
        CREATE TRIGGER IF NOT EXISTS monitoring_void_delete
        BEFORE DELETE ON monitoring_data
        WHEN (SELECT lifecycle_status FROM carbon_projects WHERE id=OLD.project_id)!='active'
          OR (OLD.machine_id IS NOT NULL AND
              (SELECT lifecycle_status FROM project_machines WHERE id=OLD.machine_id)!='active')
        BEGIN SELECT RAISE(ABORT,'project or machine is voided'); END;
        CREATE TRIGGER IF NOT EXISTS machine_void_insert
        BEFORE INSERT ON project_machines
        WHEN (SELECT lifecycle_status FROM carbon_projects WHERE id=NEW.project_id)!='active'
        BEGIN SELECT RAISE(ABORT,'project is voided'); END;
        CREATE TRIGGER IF NOT EXISTS machine_void_update
        BEFORE UPDATE ON project_machines
        WHEN (SELECT lifecycle_status FROM carbon_projects WHERE id=NEW.project_id)!='active'
        BEGIN SELECT RAISE(ABORT,'project is voided'); END;
        CREATE TRIGGER IF NOT EXISTS machine_void_delete
        BEFORE DELETE ON project_machines
        WHEN (SELECT lifecycle_status FROM carbon_projects WHERE id=OLD.project_id)!='active'
        BEGIN SELECT RAISE(ABORT,'project is voided'); END;
        CREATE TRIGGER IF NOT EXISTS report_void_insert
        BEFORE INSERT ON reports
        WHEN (SELECT lifecycle_status FROM carbon_projects WHERE id=NEW.project_id)!='active'
        BEGIN SELECT RAISE(ABORT,'project is voided'); END;
    """)
    conn.commit()


def _migrate_project_machines(conn):
    """把「一个项目 = 一台设备」的老模型迁移成多机器模型。幂等，可重复跑。

    对每个还没有机器的项目：
      1. 建一台「1号机器」，hook_params / data_source_type / data_source_config
         从项目级那三列**搬**过来（这三列保留，往后只当新建机器的默认值来源）；
      2. 把该项目 machine_id IS NULL 的历史监测数据**回填**到这台机器上 ——
         不回填的话，历史的 9.7 万条数据会在所有按机器统计的地方凭空消失。

    公式（carbon_projects.hook_name）保持不动：公式仍是项目级的。
    """
    try:
        rows = conn.execute(
            """SELECT p.id, p.hook_params, p.data_source_type, p.data_source_config
               FROM carbon_projects p
               WHERE NOT EXISTS (
                   SELECT 1 FROM project_machines m WHERE m.project_id = p.id)"""
        ).fetchall()
    except Exception as e:
        print(f"[migrate] 查询无机器项目失败（可忽略）: {e}")
        return

    for r in rows:
        try:
            conn.execute(
                """INSERT INTO project_machines
                   (project_id, name, sort_order, hook_params,
                    data_source_type, data_source_config)
                   VALUES (?, '1号机器', 1, ?, ?, ?)""",
                (r["id"], r["hook_params"] or "{}",
                 r["data_source_type"] or "manual",
                 r["data_source_config"] or "{}")
            )
            conn.commit()
            print(f"[migrate] 项目 #{r['id']} 已建「1号机器」")
        except Exception as e:
            print(f"[migrate] 项目 #{r['id']} 建机器失败（可忽略）: {e}")

    # 回填 machine_id：只回填「恰好一台机器」的项目。
    # 多机器的项目里 NULL 行归属不明，宁可留着也不猜（正常流程下不会有这种项目）。
    try:
        todo = conn.execute(
            """SELECT m.project_id AS pid, MIN(m.id) AS mid
               FROM project_machines m
               GROUP BY m.project_id
               HAVING COUNT(*) = 1"""
        ).fetchall()
    except Exception as e:
        print(f"[migrate] 查询待回填项目失败（可忽略）: {e}")
        return

    for t in todo:
        try:
            cur = conn.execute(
                "UPDATE monitoring_data SET machine_id = ? "
                "WHERE project_id = ? AND machine_id IS NULL",
                (t["mid"], t["pid"])
            )
            conn.commit()
            if cur.rowcount:
                print(f"[migrate] 项目 #{t['pid']} 回填 {cur.rowcount} 条监测数据到机器 #{t['mid']}")
        except Exception as e:
            print(f"[migrate] 项目 #{t['pid']} 回填 machine_id 失败（可忽略）: {e}")


def _ensure_column(conn, table, col_name, col_def):
    """如果指定表缺少列，则 ALTER TABLE 添加；返回 True 表示本次新增了列。"""
    cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    col_names = [c[1] for c in cols]
    if col_name in col_names:
        return False
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")
        conn.commit()
        print(f"[migrate] {table} 表已添加 {col_name} 列")
        return True
    except Exception as e:
        print(f"[migrate] 添加 {table}.{col_name} 失败（可忽略）: {e}")
        return False


# ── 企业配置 ──

def get_enterprise_config():
    """获取企业配置"""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM enterprise_config WHERE id = 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_enterprise_config(data: dict):
    """更新企业配置"""
    conn = get_db()
    try:
        allowed = ["enterprise_name", "enterprise_id", "vcer_api_url",
                    "api_access_key", "ef_grid_override", "report_logo_path",
                    "presets", "annual_code_enabled", "dev_mode",
                    "history_api_base", "history_api_username", "history_api_password"]
        sets = []
        values = []
        for k in allowed:
            if k in data:
                sets.append(f"{k} = ?")
                values.append(data[k])
        if sets:
            sets.append("updated_at = ?")
            values.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            conn.execute(
                f"UPDATE enterprise_config SET {', '.join(sets)} WHERE id = 1",
                values
            )
            conn.commit()
        return {"status": "成功"}
    except Exception as e:
        return {"status": "失败", "msg": str(e)}
    finally:
        conn.close()


# ── 项目 CRUD ──

def _with_effective_net(project: dict) -> dict:
    """把行里的 available_assets_net 换成 effective_available_net 的结果。

    读接口统一走这里，保证「库里存的」和「前端/报告拿到的」永远是同一个数，
    下游也不必再各自实现一遍兜底（那正是这个 bug 的来源：三份拷贝各写各的）。
    """
    if project:
        project["available_assets_net"] = effective_available_net(project)
    return project


def list_projects():
    """列出所有项目（附机器列表，可用碳资产按统一口径算好）"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM carbon_projects ORDER BY created_at DESC"
        ).fetchall()
        projects = [_with_effective_net(p)
                    for p in attach_machine_info([dict(r) for r in rows])]
        counts = conn.execute(
            "SELECT project_id, COUNT(*) AS n, "
            "SUM(CASE WHEN json_extract(breakdown,'$.sampling_conflict')=1 "
            "THEN 1 ELSE 0 END) AS sampling_n "
            "FROM monitoring_data WHERE json_valid(breakdown) AND "
            "(json_extract(breakdown,'$.meter_review')=1 OR "
            "json_extract(breakdown,'$.boundary_review')=1) "
            "GROUP BY project_id"
        ).fetchall()
        by_project = {r["project_id"]: r for r in counts}
        gap_counts = {r["project_id"]: r["n"] for r in conn.execute(
            "SELECT project_id,COUNT(*) AS n FROM monitoring_data "
            "WHERE json_valid(breakdown) AND "
            "json_extract(breakdown,'$.coverage_gap')=1 GROUP BY project_id")}
        for project in projects:
            summary = by_project.get(project["id"])
            project["monitoring_anomaly_count"] = summary["n"] if summary else 0
            project["sampling_conflict_count"] = summary["sampling_n"] if summary else 0
            project["monitoring_gap_count"] = gap_counts.get(project["id"], 0)
        return projects
    finally:
        conn.close()


def get_project(project_id: int):
    """获取单个项目"""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM carbon_projects WHERE id = ?", (project_id,)
        ).fetchone()
        return _with_effective_net(dict(row)) if row else None
    finally:
        conn.close()


def get_project_by_code(project_code: str):
    """按项目编号获取项目"""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM carbon_projects WHERE project_code = ?", (project_code,)
        ).fetchone()
        return _with_effective_net(dict(row)) if row else None
    finally:
        conn.close()


def create_project(data: dict) -> dict:
    """创建新项目"""
    required = ["project_name", "hook_name"]
    for key in required:
        if not data.get(key):
            return {"status": "失败", "msg": f"缺少必要参数: {key}"}

    conn = get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO carbon_projects
               (project_name, project_type, project_summary, hook_name,
                baseline_date, data_source_type, data_source_config,
                hook_params, vcer_query_key, vcer_query_pwd,
                pending_validation_10yr, estimated_annual_reduction)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["project_name"],
                data.get("project_type", data["hook_name"]),
                data.get("project_summary", ""),
                data["hook_name"],
                data.get("baseline_date"),
                data.get("data_source_type", "manual"),
                json.dumps(data.get("data_source_config") or {}, ensure_ascii=False) if isinstance(data.get("data_source_config"), dict) else (data.get("data_source_config") or "{}"),
                json.dumps(data.get("hook_params") or {}, ensure_ascii=False) if isinstance(data.get("hook_params"), dict) else (data.get("hook_params") or "{}"),
                data.get("vcer_query_key"),
                data.get("vcer_query_pwd"),
                data.get("pending_validation_10yr"),
                data.get("estimated_annual_reduction"),
            )
        )
        conn.commit()
        new_id = cursor.lastrowid
        # 项目建好就配一台「1号机器」：没有机器的项目在采集/报告/公式实例
        # 这三条链路上都是残缺的，宁可建完让用户去改，也别留个空壳。
        create_machine(new_id, {
            "data_source_type": data.get("data_source_type", "manual"),
            "data_source_config": data.get("data_source_config") or {},
            "hook_params": data.get("hook_params") or {},
        })
        return {"status": "成功", "id": new_id, "msg": "项目创建成功"}
    except Exception as e:
        return {"status": "失败", "msg": str(e)}
    finally:
        conn.close()


def update_project(project_id: int, data: dict) -> dict:
    """更新项目信息。撞锁自动重试。"""
    allowed = [
        "project_name", "project_type", "project_summary",
        "hook_name", "baseline_date", "data_source_type",
        "data_source_config", "hook_params",
        "vcer_query_key", "vcer_query_pwd", "project_code",
        "pending_validation_10yr", "estimated_annual_reduction",
    ]

    def _do():
        conn = get_db()
        try:
            sets = []
            values = []
            for k in allowed:
                if k in data:
                    if k in ("data_source_config", "hook_params"):
                        val = json.dumps(data[k], ensure_ascii=False) if isinstance(data[k], dict) else data[k]
                    else:
                        val = data[k]
                    sets.append(f"{k} = ?")
                    values.append(val)
            if sets:
                sets.append("updated_at = ?")
                values.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                values.append(project_id)
                conn.execute(
                    f"UPDATE carbon_projects SET {', '.join(sets)} WHERE id = ?",
                    values
                )
                conn.commit()
            return {"status": "成功", "msg": "项目更新成功"}
        except sqlite3.OperationalError:
            raise  # 撞锁让外层重试
        except Exception as e:
            return {"status": "失败", "msg": str(e)}
        finally:
            conn.close()

    return _run_with_db_lock_retry(_do)


def delete_project(project_id: int) -> dict:
    """删除项目"""
    conn = get_db()
    try:
        conn.execute("DELETE FROM carbon_projects WHERE id = ?", (project_id,))
        conn.commit()
        return {"status": "成功", "msg": "项目已删除"}
    except Exception as e:
        return {"status": "失败", "msg": str(e)}
    finally:
        conn.close()


# ── 机器 CRUD（一个项目多台设备）──
#
# 每台机器自带 hook_params（公式参数）与 data_source_type/config（数据源）。
# **公式不在这里** —— hook_name 仍是项目级的（PDD 的方法学一般只有一个）。

# 机器上可写字段 → 是否需要 JSON 序列化
_MACHINE_JSON_FIELDS = ("data_source_config", "hook_params", "backfill_config", "normalizer_config")


def _machine_to_dict(row):
    """sqlite3.Row → dict；顺带把 data_count 之类的派生列保留下来。"""
    return dict(row) if row is not None else None


def _json_field(val):
    """dict/list → JSON 字符串；字符串原样返回（前端可能已经序列化过）。"""
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return val if val is not None else "{}"


def list_machines(project_id: int) -> list:
    """列出项目的所有机器（含每台的监测数据条数，供删除前提示用）。

    查不到就返回空列表 —— 报告、投影这些路径都会调它，个别库还没跑迁移/
    表缺失时不该让整条链路 500。真空了会由 ensure_project_machine() 兜底补一台。
    """
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT m.*,
                      (SELECT COUNT(*) FROM monitoring_data d
                        WHERE d.machine_id = m.id) AS data_count
               FROM project_machines m
               WHERE m.project_id = ?
               ORDER BY m.sort_order, m.id""",
            (project_id,)
        ).fetchall()
        return [_machine_to_dict(r) for r in rows]
    except Exception as e:
        print(f"[project_manager] 读取项目 #{project_id} 机器列表失败（按空处理）: {e}")
        return []
    finally:
        conn.close()


def count_machines(project_id: int) -> int:
    """项目机器数。查不到按 0 算（不抛）。"""
    try:
        return len(list_machines(project_id))
    except Exception:
        return 0


# 语义化别名：读代码时「项目的机器列表」比「列机器」更顺
get_project_machines = list_machines


def get_machine(machine_id: int):
    """获取单台机器"""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM project_machines WHERE id = ?", (machine_id,)
        ).fetchone()
        return _machine_to_dict(row)
    finally:
        conn.close()


def get_primary_machine(project_id: int):
    """项目的「主机器」= 排序第一台。

    报告级的项目模板变量（{{ project.dew_point }} 之类）、以及所有没显式指定
    机器的老接口，都取这台。正常项目至少有一台（迁移保证 + 建项目时自动建）。
    """
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT * FROM project_machines WHERE project_id = ?
               ORDER BY CASE WHEN lifecycle_status='active' THEN 0 ELSE 1 END,
                        sort_order, id LIMIT 1""",
            (project_id,)
        ).fetchone()
        return _machine_to_dict(row)
    finally:
        conn.close()


def _next_machine_name(conn, project_id: int) -> str:
    """给新机器起个默认名：最小的未占用「N号机器」。"""
    used = {r["name"] for r in conn.execute(
        "SELECT name FROM project_machines WHERE project_id = ?", (project_id,)
    ).fetchall()}
    n = 1
    while f"{n}号机器" in used:
        n += 1
    return f"{n}号机器"


def create_machine(project_id: int, data: dict = None) -> dict:
    """给项目加一台机器。

    未给的字段从项目级 legacy 列取默认值 —— 老项目加第二台机器时，
    「和第一台一样的数据源/参数」是最常见的起点。
    """
    data = data or {}

    def _do():
        conn = get_db()
        try:
            proj = conn.execute(
                "SELECT * FROM carbon_projects WHERE id = ?", (project_id,)
            ).fetchone()
            if not proj:
                return {"status": "失败", "msg": f"项目 {project_id} 不存在"}

            name = (data.get("name") or "").strip() or _next_machine_name(conn, project_id)
            max_order = conn.execute(
                "SELECT COALESCE(MAX(sort_order), 0) AS o FROM project_machines WHERE project_id = ?",
                (project_id,)
            ).fetchone()["o"]

            cursor = conn.execute(
                """INSERT INTO project_machines
                   (project_id, name, sort_order, hook_params,
                    data_source_type, data_source_config, backfill_method, backfill_config, normalizer_config)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    name,
                    (max_order + 1) if data.get("sort_order") is None else data["sort_order"],
                    _json_field(data.get("hook_params", proj["hook_params"] or "{}")),
                    data.get("data_source_type") or (proj["data_source_type"] or "manual"),
                    _json_field(data.get("data_source_config", proj["data_source_config"] or "{}")),
                    data.get("backfill_method") or "none",
                    _json_field(data.get("backfill_config", "{}")),
                    _json_field(data.get("normalizer_config", "{}")),
                )
            )
            conn.commit()
            new_id = cursor.lastrowid
            return {"status": "成功", "id": new_id, "msg": "机器已添加",
                    "machine": get_machine(new_id)}
        except sqlite3.OperationalError:
            raise  # 撞锁让外层重试
        except Exception as e:
            return {"status": "失败", "msg": str(e)}
        finally:
            conn.close()

    return _run_with_db_lock_retry(_do)


def update_machine(machine_id: int, data: dict) -> dict:
    """更新一台机器（名称 / 排序 / 数据源 / 公式参数）。撞锁自动重试。"""
    # 新版单独存前置转换。前端同时带旧字段供仍在运行的旧进程兼容；
    # 新版在入库前移除重复块，保持 data_source_config 只含请求参数。
    if "normalizer_config" in data and isinstance(data.get("data_source_config"), dict):
        data = dict(data)
        data["data_source_config"] = dict(data["data_source_config"])
        data["data_source_config"].pop("normalizer", None)
    allowed = ["name", "sort_order", "data_source_type",
               "data_source_config", "hook_params",
               "backfill_method", "backfill_config", "normalizer_config"]

    def _do():
        conn = get_db()
        try:
            sets, values = [], []
            for k in allowed:
                if k in data:
                    val = _json_field(data[k]) if k in _MACHINE_JSON_FIELDS else data[k]
                    sets.append(f"{k} = ?")
                    values.append(val)
            if sets:
                sets.append("updated_at = ?")
                values.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                values.append(machine_id)
                conn.execute(
                    f"UPDATE project_machines SET {', '.join(sets)} WHERE id = ?", values
                )
                conn.commit()
            return {"status": "成功", "msg": "机器已更新", "machine": get_machine(machine_id)}
        except sqlite3.OperationalError:
            raise  # 撞锁让外层重试
        except Exception as e:
            return {"status": "失败", "msg": str(e)}
        finally:
            conn.close()

    return _run_with_db_lock_retry(_do)


def delete_machine(machine_id: int) -> dict:
    """删除一台机器，连同它名下的监测数据。

    项目必须至少留一台机器：一台不剩的话，采集、公式实例、报告全都没了依托，
    再想恢复只能靠重新迁移。所以最后一台直接拒删。
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM project_machines WHERE id = ?", (machine_id,)
        ).fetchone()
        if not row:
            return {"status": "失败", "msg": f"机器 {machine_id} 不存在"}

        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM project_machines WHERE project_id = ?",
            (row["project_id"],)
        ).fetchone()["c"]
        if cnt <= 1:
            return {"status": "失败", "msg": "项目至少要保留一台机器"}

        deleted = conn.execute(
            "DELETE FROM monitoring_data WHERE machine_id = ?", (machine_id,)
        ).rowcount
        conn.execute("DELETE FROM project_machines WHERE id = ?", (machine_id,))
        conn.commit()

        from .reduction_ledger import refresh_totals
        refresh_totals(conn, row["project_id"])
        conn.commit()
        return {"status": "成功", "deleted_data": deleted, "deleted_machine": row["name"],
                "msg": f"已删除「{row['name']}」及名下 {deleted} 条监测数据"}
    except Exception as e:
        return {"status": "失败", "msg": str(e)}
    finally:
        conn.close()


def ensure_project_machine(project_id: int) -> dict:
    """保证项目至少有一台机器，返回主机器。

    给「迁移没覆盖到」的项目兜底 —— 报告、采集这些路径都假设机器存在，
    真碰上没机器的项目就地补一台，别让整条链路断掉。
    """
    m = get_primary_machine(project_id)
    if m:
        return m
    create_machine(project_id, {})
    return get_primary_machine(project_id)


def project_cumulative(conn, project_id: int) -> float:
    """项目的累计核证减排量 = **各机器各自累计值之和**。

    每台机器的 cumulative_reduction 是它自己那台的累计；
    项目口径就是把各台最新累计值加起来。另加一组「未分配机器」的历史行
    （machine_id IS NULL），防止迁移万一漏掉的项目被算漏。
    """
    total = 0.0
    try:
        rows = conn.execute(
            """SELECT (SELECT d.cumulative_reduction FROM monitoring_data d
                        WHERE d.machine_id = m.id
                        ORDER BY d.timestamp DESC, d.id DESC LIMIT 1) AS cum
               FROM project_machines m WHERE m.project_id = ?""",
            (project_id,)
        ).fetchall()
        total += sum(float(r["cum"] or 0) for r in rows)

        orphan = conn.execute(
            """SELECT cumulative_reduction FROM monitoring_data
               WHERE project_id = ? AND machine_id IS NULL
               ORDER BY timestamp DESC, id DESC LIMIT 1""",
            (project_id,)
        ).fetchone()
        if orphan:
            total += float(orphan["cumulative_reduction"] or 0)
    except Exception as e:
        print(f"[project_manager] 计算项目 #{project_id} 累计减排量失败: {e}")
    return round(total, 6)


def list_all_machines() -> list:
    """列出所有项目的所有机器（连带项目的 hook_name / 名称）。

    采集器要按**机器**遍历（每台一份数据源），但公式名挂在项目上 ——
    一次 join 全取出来，省得每台再查一次项目。
    """
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT m.*, p.hook_name AS hook_name, p.project_name AS project_name,
                      p.baseline_date AS baseline_date
               FROM project_machines m
               JOIN carbon_projects p ON p.id = m.project_id
               WHERE p.lifecycle_status = 'active'
                 AND m.lifecycle_status = 'active'
               ORDER BY m.project_id, m.sort_order, m.id"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def attach_machine_info(projects: list) -> list:
    """给项目列表补上 machine_count / machines（项目卡片用）。

    一次查全部机器再分组，避免每个项目一条查询。
    """
    if not projects:
        return projects
    conn = get_db()
    try:
        ids = [p["id"] for p in projects if p.get("id")]
        if not ids:
            return projects
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""SELECT m.id, m.project_id, m.name, m.data_source_type,
                       m.backfill_method, m.lifecycle_status,
                       (SELECT COUNT(*) FROM monitoring_data d
                         WHERE d.machine_id = m.id) AS data_count
                FROM project_machines m
                WHERE m.project_id IN ({placeholders})
                ORDER BY m.sort_order, m.id""",
            ids
        ).fetchall()
    except Exception:
        return projects
    finally:
        conn.close()

    grouped = {}
    for r in rows:
        grouped.setdefault(r["project_id"], []).append({
            "id": r["id"], "name": r["name"],
            "data_source_type": r["data_source_type"],
            "backfill_method": r["backfill_method"],
            "lifecycle_status": r["lifecycle_status"],
            "data_count": r["data_count"],
        })
    for p in projects:
        ms = grouped.get(p.get("id"), [])
        p["machines"] = ms
        p["machine_count"] = len(ms)
    return projects


# ── 阶段通过状态更新 ──

def update_stages_passed(project_id: int, passed_map: dict) -> dict:
    """批量更新各阶段是否通过（一次连接、一条 UPDATE、一次提交）。

    passed_map: {1: 1, 2: 0, 3: 1, ...}，1=已通过 0=未通过。
    """
    valid = {s: 1 if passed_map.get(s) else 0 for s in range(1, 6)}
    conn = get_db()
    try:
        conn.execute(
            """UPDATE carbon_projects SET
               stage1_passed = ?, stage2_passed = ?, stage3_passed = ?,
               stage4_passed = ?, stage5_passed = ?, updated_at = ?
               WHERE id = ?""",
            (
                valid[1], valid[2], valid[3], valid[4], valid[5],
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                project_id,
            )
        )
        conn.commit()
        return {"status": "成功", "msg": "阶段通过状态已更新"}
    except Exception as e:
        return {"status": "失败", "msg": str(e)}
    finally:
        conn.close()


def effective_available_net(project: dict) -> float:
    """项目的「可用碳资产(B)」—— 全系统唯一的算法，别再写第二份。

    以 VCER 返回的 `available_assets_net` 为准。只有**核查表确实没有记录**
    （`available_assets_gross == 0`）且平台又返回 0 时，才用「总碳资产 − 已交易」
    兜底（历史行为：核查记录还没录进来时，先按审定量显示）。

    ⚠️ 千万别写成 `net or (total - traded)` 或 `if net == 0 and total > 0`：
    平台返回 0 更常见的原因是「**已核证的都卖完了**」，那会儿可用本来就该是 0。
    拿 total − traded 去顶，就会凭空多出一大截 ——
    GDES-VCER-036-3 就是这么多出了 11792.86。
    而且这个坑对**每一个卖完的项目**都会犯，不是个例。

    幂等：对已经算好的值再算一次结果不变，所以存库和展示都可以直接调它。
    """
    net = project.get("available_assets_net", 0) or 0
    total = project.get("total_carbon_assets", 0) or 0
    # ⚠️ gross **不** `or 0`：要区分「明确是 0（核查表确实没记录）」和
    # 「压根没这个字段（响应不全/老接口）」。后者绝不能触发兜底 ——
    # 否则平台少返回一个字段，就会把这个 bug 重新放出来。
    gross = project.get("available_assets_gross")
    traded = project.get("traded_amount", 0) or 0
    if net == 0 and total > 0 and gross == 0:
        return max(total - traded, 0)
    return net


def sync_carbon_assets(project_id: int, assets: dict) -> dict:
    """同步碳资产数据（从 VCER API 获取后调用）"""
    conn = get_db()
    try:
        total = assets.get("total_carbon_assets", 0) or 0
        gross = assets.get("available_assets_gross", 0) or 0
        traded = assets.get("traded_amount", 0) or 0
        # 可用量统一走 effective_available_net（含「核查表无记录」的兜底口径）
        net = effective_available_net({
            "total_carbon_assets": total,
            "available_assets_gross": gross,
            "traded_amount": traded,
            "available_assets_net": assets.get("available_assets_net", 0) or 0,
        })

        conn.execute(
            """UPDATE carbon_projects SET
               total_carbon_assets = ?, available_assets_gross = ?,
               available_assets_net = ?, traded_amount = ?,
               updated_at = ?
               WHERE id = ?""",
            (
                total,
                gross,
                net,
                traded,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                project_id,
            )
        )
        conn.commit()
        return {"status": "成功"}
    except Exception as e:
        return {"status": "失败", "msg": str(e)}
    finally:
        conn.close()


def update_sync_time(project_id: int, sync_time: str = None) -> dict:
    """更新最后同步时间"""
    if sync_time is None:
        sync_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        conn.execute(
            "UPDATE carbon_projects SET last_sync_time = ? WHERE id = ?",
            (sync_time, project_id)
        )
        conn.commit()
        return {"status": "成功"}
    except Exception as e:
        return {"status": "失败", "msg": str(e)}
    finally:
        conn.close()
