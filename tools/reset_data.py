# -*- coding: utf-8 -*-
"""数据清理 / 企业重置工具。

系统原本只有「删除整个项目」这一条路 —— 想把模拟数据清掉、但**保留项目**
（项目编号、促进会密钥、预设、机器配置都还在），界面上做不到。
这个脚本补上这些操作，并且**任何破坏性操作都会先自动备份数据库**。

用法::

    # 1) 先看看现在有什么（不做任何修改）
    python tools/reset_data.py --list

    # 2) 只清掉某个项目的监测数据（项目本身保留）—— 想重新接 MQTT 录真数据时用
    python tools/reset_data.py --clear-monitoring GDES-VCER-036-1 --yes

    # 3) 只清掉某台机器的监测数据
    python tools/reset_data.py --clear-monitoring GDES-VCER-036-1 --machine 3 --yes

    # 4) 删掉整个项目（连同它的机器、监测数据、报告记录）
    python tools/reset_data.py --clear-project GDES-VCER-036-1 --yes

    # 5) 报告：先看有哪些，再按需删（默认连磁盘 Word 文件一起删）
    python tools/reset_data.py --list-reports GDES-VCER-036-1
    python tools/reset_data.py --clear-reports GDES-VCER-036-1 --yes
    python tools/reset_data.py --clear-reports GDES-VCER-036-1 --report-type pdd --yes
    python tools/reset_data.py --clear-reports GDES-VCER-036-1 --report-id 12 --report-id 15 --yes
    python tools/reset_data.py --clear-reports GDES-VCER-036-1 --keep-files --yes

    # 6) 全清：所有项目的所有数据 + 企业配置复原成默认
    #    （等价于删掉 data/ 目录，但会先备份、并且不会误删代码）
    python tools/reset_data.py --clear-all --yes

    # 6b) 全清时连预设配置一起清（默认**保留**预设定义与全项目通用预设值）
    python tools/reset_data.py --clear-all --include-config --yes

不加 `--yes` 只做「预演」，把要删的东西列出来，不动数据。
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core.project_manager import get_db, project_cumulative  # noqa: E402


def _assert_not_real_db(action):
    """护栏：进程声明了沙箱时，破坏性操作必须打在沙箱上。

    **为什么要有这个**：2026-09-29 出过一次事故 —— 跑测试套件时把**真库**的
    9.7 万条监测数据删了、项目累计也被清零（签名正是 --clear-monitoring 干的）。
    各个测试都调了 `make_copy_hermetic` 把 config.DATABASE_PATH 指向副本，
    但事后没能定位到具体是哪一步把路径又指回了真库。

    所以这里不依赖"测试一定改对了路径"这个假设：只要 `tools/_testutil.py` 声明过沙箱
    （环境变量 CARBON_ACCOUNT_SANDBOX），而当前 config.DATABASE_PATH 不是那个沙箱，
    就说明路径被指回了真库 —— 直接拒绝，而不是把生产数据删掉。

    人工跑命令行时这个环境变量不存在，行为完全不变。
    """
    sandbox = os.environ.get("CARBON_ACCOUNT_SANDBOX")
    if not sandbox:
        return
    cur = os.path.abspath(config.DATABASE_PATH)
    if cur != os.path.abspath(sandbox):
        raise SystemExit(
            f"\n[已阻止] 拒绝执行「{action}」。\n"
            f"  进程声明了测试沙箱 : {sandbox}\n"
            f"  但当前库却指向    : {cur}\n"
            f"  这会把真库删掉。请检查是谁把 config.DATABASE_PATH 改回去了；\n"
            f"  确认要动真库的话，请另开一个干净终端直接跑本脚本（不带该环境变量）。\n")


def _backup_db():
    """破坏性操作前先备份数据库，返回备份路径。"""
    src = config.DATABASE_PATH
    if not os.path.exists(src):
        return None
    bdir = os.path.join(os.path.dirname(src), "backups")
    os.makedirs(bdir, exist_ok=True)
    dst = os.path.join(
        bdir,
        f"carbon_account_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    )
    shutil.copyfile(src, dst)
    return dst


def _confirm(prompt, assume_yes):
    if assume_yes:
        return True
    print(f"\n[预演] {prompt}")
    print("       确认要执行请加 --yes（例如：python tools/reset_data.py ... --yes）")
    return False


def _fmt_rowcount(conn, table, where="", params=()):
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return conn.execute(sql, params).fetchone()[0]
    except Exception:
        return 0


def _project_by_code(conn, code):
    return conn.execute(
        "SELECT * FROM carbon_projects WHERE project_code = ? OR project_name = ? "
        "OR CAST(id AS TEXT) = ?",
        (code, code, str(code))
    ).fetchone()


# ══════════════════════════════════════════════════════════
# --list
# ══════════════════════════════════════════════════════════

def cmd_list():
    conn = get_db()
    try:
        ent = conn.execute("SELECT * FROM enterprise_config WHERE id = 1").fetchone()
        print("=" * 74)
        print("企业配置")
        print("=" * 74)
        if ent:
            print(f"  企业名称 : {ent['enterprise_name']}")
            print(f"  企业序号 : {ent['enterprise_id'] or '（未填）'}")
            print(f"  促进会API: {ent['vcer_api_url']}")
            has_key = bool(ent["api_access_key"])
            print(f"  API密钥  : {'已配置' if has_key else '未配置'}")
        else:
            print("  （没有企业配置行）")

        projects = conn.execute(
            "SELECT * FROM carbon_projects ORDER BY id").fetchall()
        print()
        print("=" * 74)
        print(f"项目（{len(projects)} 个）")
        print("=" * 74)
        for p in projects:
            n_mon = _fmt_rowcount(conn, "monitoring_data", "project_id = ?", (p["id"],))
            n_rep = _fmt_rowcount(conn, "reports", "project_id = ?", (p["id"],))
            print(f"\n  #{p['id']}  {p['project_code'] or '（无编号）'}  {p['project_name']}")
            print(f"       计入期起 {p['baseline_date'] or '（未填）'}"
                  f"   监测数据 {n_mon} 条   报告 {n_rep} 份")
            ms = conn.execute(
                "SELECT id, name, data_source_type FROM project_machines "
                "WHERE project_id = ? ORDER BY sort_order, id", (p["id"],)).fetchall()
            for m in ms:
                mc = _fmt_rowcount(conn, "monitoring_data", "machine_id = ?", (m["id"],))
                print(f"       机器 #{m['id']}  {m['name']}  [{m['data_source_type']}]"
                      f"  {mc} 条数据")
        print()
        print("=" * 74)
        print(f"监测数据合计 {_fmt_rowcount(conn, 'monitoring_data')} 条"
              f"   报告文件目录 {config.REPORT_DIR}")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════
# --clear-monitoring
# ══════════════════════════════════════════════════════════

def cmd_clear_monitoring(project_key, machine_id, assume_yes):
    _assert_not_real_db("清除监测数据")
    conn = get_db()
    try:
        proj = _project_by_code(conn, project_key)
        if not proj:
            print(f"[失败] 找不到项目: {project_key}")
            return 1
        pid = proj["id"]

        where, params = "project_id = ?", [pid]
        label = f"项目 #{pid} {proj['project_name']}"
        if machine_id:
            m = conn.execute("SELECT * FROM project_machines WHERE id = ?",
                             (machine_id,)).fetchone()
            if not m or m["project_id"] != pid:
                print(f"[失败] 机器 #{machine_id} 不属于该项目")
                return 1
            where += " AND machine_id = ?"
            params.append(machine_id)
            label += f" 的机器 #{machine_id} {m['name']}"

        n = _fmt_rowcount(conn, "monitoring_data", where, tuple(params))
        print(f"将清除 {label} 的监测数据 {n} 条")
        print("  （项目本身、机器配置、公式参数、促进会密钥、预设都保留）")
        if not _confirm("清除后不可恢复（会先自动备份数据库）", assume_yes):
            return 0

        bak = _backup_db()
        if bak:
            print(f"[备份] 数据库已备份到 {bak}")

        conn.execute(f"DELETE FROM monitoring_data WHERE {where}", tuple(params))
        # 累计核证减排量要**按剩下的数据重算**，不能一律置 0：
        # 带 --machine 时只清了一台机器，其余机器的数据还在 ——
        # 直接置 0 会让总览和报告显示 0，而库里明明还有数据。
        # 口径与 delete_machine 一致（各机器最新一行的累计之和）。
        total = project_cumulative(conn, pid)
        conn.execute(
            "UPDATE carbon_projects SET pending_verification_cumulative = ?, "
            "updated_at = ? WHERE id = ?",
            (total, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pid))
        conn.commit()
        print(f"[完成] 已删除 {n} 条监测数据，"
              f"项目累计核证减排量已按剩余数据重算为 {total:.4f}")
        print("       机器还在、配置没动，可以直接接 MQTT 或重新导入数据。")
        return 0
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════
# --clear-project
# ══════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════
# --list-reports / --clear-reports
# ══════════════════════════════════════════════════════════

_REPORT_TYPES = ("verification", "pdd", "approval")


def _report_rows(conn, pid, ids=None, rtype=None, year=None):
    """挑出要处理的报告行。返回 (rows, sql_desc)。"""
    where, params = ["project_id = ?"], [pid]
    desc = []
    if ids:
        where.append("id IN (%s)" % ",".join("?" * len(ids)))
        params.extend(ids)
        desc.append("id=" + ",".join(str(i) for i in ids))
    if rtype:
        where.append("report_type = ?")
        params.append(rtype)
        desc.append(f"类型={rtype}")
    if year:
        where.append("strftime('%Y', generated_at) = ?")
        params.append(str(year))
        desc.append(f"年份={year}")
    sql = ("SELECT id, report_type, report_title, file_path, generated_at, file_size_bytes "
           f"FROM reports WHERE {' AND '.join(where)} ORDER BY generated_at, id")
    return conn.execute(sql, tuple(params)).fetchall(), "，".join(desc) or "全部"


def cmd_list_reports(project_key):
    conn = get_db()
    try:
        proj = _project_by_code(conn, project_key)
        if not proj:
            print(f"[失败] 找不到项目: {project_key}")
            return 1
        rows, _ = _report_rows(conn, proj["id"])
        print(f"项目 #{proj['id']} {proj['project_name']}"
              f"（编号 {proj['project_code'] or '无'}）共 {len(rows)} 份报告：")
        if not rows:
            return 0
        for r in rows:
            fp = r["file_path"] or ""
            exists = "文件在" if (fp and os.path.exists(fp)) else "文件缺失"
            size = r["file_size_bytes"] or 0
            print(f"  #{r['id']:<4} {r['report_type']:<13} {r['generated_at']}  "
                  f"{size/1024:.0f}KB  [{exists}]  {r['report_title']}")
        # 报告编号靠"报告数"推算，先讲清楚
        by_type = {}
        for r in rows:
            by_type[r["report_type"]] = by_type.get(r["report_type"], 0) + 1
        print("\n  按类型：" + "、".join(f"{k} {v} 份" for k, v in sorted(by_type.items())))
        print("  ⚠ 核查报告的编号是按「已有报告份数」推算的（见 reports/context_builder.get_annual_code），")
        print("     删掉记录会让之后新生成的报告编号往前退，可能与已发出去的编号重复。")
        return 0
    finally:
        conn.close()


def cmd_clear_reports(project_key, ids, rtype, year, keep_files, assume_yes):
    _assert_not_real_db("删除报告")
    conn = get_db()
    try:
        proj = _project_by_code(conn, project_key)
        if not proj:
            print(f"[失败] 找不到项目: {project_key}")
            return 1
        pid = proj["id"]
        rows, desc = _report_rows(conn, pid, ids, rtype, year)
        if not rows:
            print(f"[跳过] 该项目下没有符合条件的报告（筛选：{desc}）")
            return 0

        n_ver = sum(1 for r in rows if r["report_type"] == "verification")
        print(f"将删除项目 #{pid} {proj['project_name']} 的报告 {len(rows)} 份"
              f"（筛选：{desc}）")
        for r in rows:
            print(f"  #{r['id']:<4} {r['report_type']:<13} {r['generated_at']}  "
                  f"{r['report_title']}")
        print(f"\n  · 数据库记录：{len(rows)} 条")
        print(f"  · 磁盘文件  ：{'保留（--keep-files）' if keep_files else '一并删除'}")
        if n_ver:
            print(f"\n  ⚠ 其中有 {n_ver} 份核查报告。核查报告编号是按「已有份数」推算的"
                  f"（get_annual_code），")
            print(f"     删掉后新生成的报告编号会往前退，**可能与已经发出去的编号重复**。")
            print(f"     如果只是要清理磁盘/记录，建议加 --keep-files 或只删某一类。")
        if not _confirm("删除后库记录不可恢复（会先自动备份数据库）", assume_yes):
            return 0

        bak = _backup_db()
        if bak:
            print(f"[备份] 数据库已备份到 {bak}")

        removed_files, missing_files, failed = 0, 0, 0
        for r in rows:
            fp = r["file_path"] or ""
            if not keep_files and fp:
                if os.path.exists(fp):
                    try:
                        os.remove(fp)
                        removed_files += 1
                    except OSError as e:
                        failed += 1
                        print(f"  [警告] 删文件失败 {fp}: {e}")
                else:
                    missing_files += 1
            conn.execute("DELETE FROM reports WHERE id = ?", (r["id"],))
        conn.commit()

        print(f"\n[完成] 已删除报告记录 {len(rows)} 条")
        if not keep_files:
            print(f"       磁盘文件删除 {removed_files} 个"
                  + (f"，原本就不存在 {missing_files} 个" if missing_files else "")
                  + (f"，失败 {failed} 个（见上面的警告）" if failed else ""))
        else:
            print("       磁盘文件按要求保留")
        return 0
    finally:
        conn.close()


def _delete_report_files(conn, pid):
    """删掉该项目已生成报告留在磁盘上的文件（DB 记录靠外键级联删）。"""
    removed = 0
    try:
        rows = conn.execute(
            "SELECT file_path FROM reports WHERE project_id = ?", (pid,)).fetchall()
    except Exception:
        return 0
    for r in rows:
        fp = r["file_path"]
        if fp and os.path.exists(fp):
            try:
                os.remove(fp)
                removed += 1
            except OSError:
                pass
    return removed


def _delete_project_images(conn, pid):
    """删掉该项目预设里引用到的图片文件（可选清理，失败不影响主流程）。"""
    removed = 0
    try:
        row = conn.execute("SELECT presets FROM carbon_projects WHERE id = ?",
                           (pid,)).fetchone()
        blob = row["presets"] if row else ""
        if not blob:
            return 0
        img_dir = os.path.join(os.path.dirname(config.DATABASE_PATH), "preset_images")
        if not os.path.isdir(img_dir):
            return 0
        for fn in os.listdir(img_dir):
            if fn in blob:
                try:
                    os.remove(os.path.join(img_dir, fn))
                    removed += 1
                except OSError:
                    pass
    except Exception:
        pass
    return removed


def cmd_clear_project(project_key, assume_yes):
    _assert_not_real_db("删除项目")
    conn = get_db()
    try:
        proj = _project_by_code(conn, project_key)
        if not proj:
            print(f"[失败] 找不到项目: {project_key}")
            return 1
        pid = proj["id"]
        n_mon = _fmt_rowcount(conn, "monitoring_data", "project_id = ?", (pid,))
        n_mach = _fmt_rowcount(conn, "project_machines", "project_id = ?", (pid,))
        n_rep = _fmt_rowcount(conn, "reports", "project_id = ?", (pid,))
        n_rule = _fmt_rowcount(conn, "alert_rules", "project_id = ?", (pid,))

        print(f"将删除项目 #{pid} {proj['project_name']}"
              f"（编号 {proj['project_code'] or '无'}）")
        print(f"  连同：机器 {n_mach} 台、监测数据 {n_mon} 条、报告记录 {n_rep} 份、"
              f"告警规则 {n_rule} 条")
        print("  （报告文件与项目图片也会一并删除；促进会那边不受影响）")
        if not _confirm("删除后不可恢复（会先自动备份数据库）", assume_yes):
            return 0

        bak = _backup_db()
        if bak:
            print(f"[备份] 数据库已备份到 {bak}")

        files = _delete_report_files(conn, pid)
        imgs = _delete_project_images(conn, pid)
        conn.execute("DELETE FROM carbon_projects WHERE id = ?", (pid,))
        # 系统日志没有外键，项目没了会把 project_id 悬空 —— 置空比留着好
        try:
            conn.execute("UPDATE system_logs SET project_id = NULL WHERE project_id = ?", (pid,))
        except Exception:
            pass
        conn.commit()
        print(f"[完成] 项目已删除；顺带清掉报告文件 {files} 个、图片 {imgs} 个")
        return 0
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════
# --clear-all
# ══════════════════════════════════════════════════════════

def cmd_clear_all(assume_yes, include_config):
    _assert_not_real_db("清空全部数据")
    conn = get_db()
    try:
        n_proj = _fmt_rowcount(conn, "carbon_projects")
        n_mon = _fmt_rowcount(conn, "monitoring_data")
        n_rep = _fmt_rowcount(conn, "reports")

        print("将清空**本套系统**的全部企业数据：")
        print(f"  项目 {n_proj} 个、监测数据 {n_mon} 条、报告记录 {n_rep} 份")
        print("  企业配置（企业名称/序号/促进会API密钥/Logo）复原为默认值")
        print("  已生成的报告文件与上传的图片一并删除")
        if include_config:
            print("  ⚠️ 连预设配置也清（project_presets_*.json）—— 更彻底，"
                  "字段定义会在下次启动时按内置默认重建")
        else:
            print("  预设配置（预设字段定义、全项目通用预设值）**保留**")
        print("\n  代码、报告模板（templates/ 下的）都不动。")
        if not _confirm("清空后不可恢复（会先自动备份数据库）", assume_yes):
            return 0

        bak = _backup_db()
        if bak:
            print(f"[备份] 数据库已备份到 {bak}")

        # 1) 报告记录里登记过的文件
        files = 0
        try:
            rows = conn.execute("SELECT file_path FROM reports").fetchall()
            for r in rows:
                fp = r["file_path"]
                if fp and os.path.exists(fp):
                    try:
                        os.remove(fp)
                        files += 1
                    except OSError:
                        pass
        except Exception:
            pass

        # 2) 图片目录全清
        imgs = 0
        img_dir = os.path.join(os.path.dirname(config.DATABASE_PATH), "preset_images")
        if os.path.isdir(img_dir):
            for fn in os.listdir(img_dir):
                try:
                    os.remove(os.path.join(img_dir, fn))
                    imgs += 1
                except OSError:
                    pass

        # 3) 数据库：删业务数据（外键级联带走机器/监测/报告/告警）
        for tbl in ("carbon_projects", "alert_rules", "alert_log", "system_logs"):
            try:
                conn.execute(f"DELETE FROM {tbl}")
            except Exception as e:
                print(f"  [warn] 清空 {tbl} 失败: {e}")

        # 4) 企业配置复原成默认（保留 id=1 这一行）
        try:
            conn.execute(
                """UPDATE enterprise_config SET
                   enterprise_name = '我的企业', enterprise_id = '',
                   api_access_key = NULL, ef_grid_override = NULL,
                   report_logo_path = NULL, presets = '[]',
                   vcer_api_url = ?, updated_at = ?
                   WHERE id = 1""",
                (config.VCER_API_URL,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        except Exception as e:
            print(f"  [warn] 复原企业配置失败: {e}")
        conn.commit()

        # 5) 预设配置
        if include_config:
            d = os.path.dirname(config.DATABASE_PATH)
            for fn in ("project_presets_shared.json", "project_presets_global.json",
                       "template_keys_cache.json"):
                p = os.path.join(d, fn)
                if os.path.exists(p):
                    try:
                        os.remove(p)
                        print(f"  已删除配置 {fn}")
                    except OSError:
                        pass

        print(f"[完成] 报告文件 {files} 个、图片 {imgs} 个已删除；数据库已清空并复原")
        print("       重启服务后会是一套全新的通用系统，"
              "需要重新填企业名称/序号/促进会 API 密钥。")
        return 0
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(
        description="数据清理 / 企业重置（破坏性操作前会自动备份数据库）")
    ap.add_argument("--list", action="store_true", help="查看当前数据（不做修改）")
    ap.add_argument("--clear-monitoring", metavar="项目编号",
                    help="只清监测数据，保留项目与机器配置")
    ap.add_argument("--machine", type=int, default=None,
                    help="配合 --clear-monitoring：只清某台机器")
    ap.add_argument("--clear-project", metavar="项目编号", help="删除整个项目")
    ap.add_argument("--clear-all", action="store_true", help="清空全部企业数据")
    ap.add_argument("--include-config", action="store_true",
                    help="配合 --clear-all：连预设配置一起清")

    # ── 报告 ──
    ap.add_argument("--list-reports", metavar="项目编号",
                    help="列出该项目的报告（只读）")
    ap.add_argument("--clear-reports", metavar="项目编号",
                    help="删该项目的报告（默认连磁盘文件一起删；用下面的筛选项缩小范围）")
    ap.add_argument("--report-id", type=int, action="append", default=None,
                    metavar="N", help="配合 --clear-reports：只删指定报告 id，可重复写")
    ap.add_argument("--report-type", choices=_REPORT_TYPES, default=None,
                    help="配合 --clear-reports：只删某一类报告")
    ap.add_argument("--report-year", type=int, default=None, metavar="YYYY",
                    help="配合 --clear-reports：只删该年生成的")
    ap.add_argument("--keep-files", action="store_true",
                    help="配合 --clear-reports：只删数据库记录，磁盘上的 Word 文件保留")

    ap.add_argument("--yes", action="store_true", help="确认执行（不加则为预演）")
    args = ap.parse_args()

    if args.list:
        cmd_list()
        return 0
    if args.clear_monitoring:
        return cmd_clear_monitoring(args.clear_monitoring, args.machine, args.yes)
    if args.list_reports:
        return cmd_list_reports(args.list_reports)
    if args.clear_reports:
        return cmd_clear_reports(args.clear_reports, args.report_id, args.report_type,
                                 args.report_year, args.keep_files, args.yes)
    if args.clear_project:
        return cmd_clear_project(args.clear_project, args.yes)
    if args.clear_all:
        return cmd_clear_all(args.yes, args.include_config)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
