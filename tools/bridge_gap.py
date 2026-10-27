# -*- coding: utf-8 -*-
"""断档兜底 —— 缺了一段源数据时，用「已核查量」把空档顶掉，只补记未核查的部分。

**要解决的问题**

累计读数做差只能表达"一个正常采集间隔"。中间断了很久（项目换核查周期、
系统停机、设备离线数月）时，那条差值等于"断档期间的全部产量"，
而这段时间**很可能已经核查过了** —— 直接算进来就是重复计算，且永久留在累计里。

现在的断档保护（`config.COLLECT_GAP_RESET_HOURS`）会把断档那条当**新起点**：
不重复算了 ✓，但**未核查的那部分也一起丢了** ✗。

**本工具补的就是丢掉的那部分**：

    全段减排量 R_span  = 空档起点读数 → 断档那条读数，按公式算出来
    已核查量 V          = 与空档重叠的**已通过核查轮次**的核证量合计（从促进会拉）
    补记量              = R_span − V          ← 写回断档那条行

为什么能一减：空压站等公式对读数差是**线性可加**的
（`Δ流量(全段) = Δ流量(已核查段) + Δ流量(待算段)`），所以减排量也直接相减。
促进会只做审核、不另算一套，所以两边同口径。

写回时会把「基准线排放/项目排放」**按比例摊**，保证表 B.4 的
「基准线 − 项目 = 减排量」仍然对得上（直填模式就是因为拆不开才让 B.2/B.3 对不平）。

用法::

    # 1) 看这台机器有哪些断档（只读）
    python tools/bridge_gap.py --project GDES-VCER-036-1 --machine 3 --list

    # 2) 预演：算出补记量，但不写库
    python tools/bridge_gap.py --project GDES-VCER-036-1 --machine 3 --latest

    # 3) 确认写入
    python tools/bridge_gap.py --project GDES-VCER-036-1 --machine 3 --latest --yes

    # 指定某一次断档（时间取 --list 里显示的）
    python tools/bridge_gap.py --project GDES-VCER-036-1 --machine 3 --at "2026-09-24 10:00:00" --yes

    # 拉不到核查记录、或想手工指定时，直接给已核查量
    python tools/bridge_gap.py --project GDES-VCER-036-1 --machine 3 --latest --verified 1234.56 --yes

不加 `--yes` 只预演。写入前会**自动备份数据库**到 `data/backups/`。
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from core.project_manager import get_db, get_project, get_machine  # noqa: E402


# ══════════════════════════════════════════════════════════
# 基础工具
# ══════════════════════════════════════════════════════════

# _backup_db 已移到 core/gap_bridge.py（界面接口也要用），在下面统一 import 进来。
# 这里不再自己实现一份。


def _resolve_project(key):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM carbon_projects WHERE project_code = ? OR project_name = ? "
            "OR CAST(id AS TEXT) = ?", (key, key, str(key))).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _resolve_machine(project_id, mid):
    if mid is None:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM project_machines WHERE project_id = ? "
                "ORDER BY sort_order, id LIMIT 1", (project_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    return get_machine(int(mid))


def _get_formula(machine, hook_name):
    """按机器拿公式实例（与采集路径同一套：公式名取自项目，参数取自机器）。"""
    from core.carbon_calculator import CarbonCalculator
    mid = machine["id"]
    if mid not in CarbonCalculator._instances:
        CarbonCalculator.init_machine_formula(machine, hook_name, config.VCER_API_URL)
    return CarbonCalculator.get_instance(mid)


# ══════════════════════════════════════════════════════════
# 找断档
# ══════════════════════════════════════════════════════════

# _bd_of / _is_gap_row / _find_gaps / _pick_gap 已移到 core/gap_bridge.py
# （界面接口也要用），在下面统一 import 重新导出。这里不再自己实现一份。



# ══════════════════════════════════════════════════════════
# 拉已核查量
# ══════════════════════════════════════════════════════════

def _norm_date(s):
    s = str(s or "").strip().replace("/", "-")
    return s[:10]


# 算法**全部**在 core/gap_bridge.py 里，这里只做重新导出：
# 采集器自动兜底、命令行工具、界面接口共用同一份，别在这文件里再写第二份 ——
# 两边口径一分叉就会算出两个数。
# 保留这些别名是因为 tools/_test_bridge_gap.py 直接引用 bg._find_gaps / bg._overlapping。
from core.gap_bridge import (  # noqa: E402,F401
    bd_of as _bd_of,
    is_gap_row as _is_gap_row,
    find_gaps as _find_gaps,
    pick_gap as _pick_gap,
    backup_db as _backup_db,
    fetch_verified_rounds as _fetch_verified_rounds,
    overlapping_rounds as _overlapping,
    prorated_verified,
    scale_breakdown as _scale_breakdown,
    preview_bridge,
    apply_bridge,
)


def cmd_list(project, machine):
    conn = get_db()
    try:
        gaps = _find_gaps(conn, machine["id"])
    finally:
        conn.close()
    print(f"项目 {project.get('project_code') or project['id']} — "
          f"机器 #{machine['id']}「{machine['name']}」")
    if not gaps:
        print("  没有断档记录。")
        return 0
    pend = [g for g in gaps if not g["bridged"]]
    print(f"  发现 {len(gaps)} 次断档（待兜底 {len(pend)} 次）：")
    for g in gaps:
        pr = g["prev_row"]
        bd = _bd_of(g["gap_row"])
        print(f"\n    断档行时间: {g['gap_row']['timestamp']}"
              f"   [{'已兜底' if g['bridged'] else '待兜底'}]")
        print(f"      空档起点  : {pr['timestamp'] if pr else '（该机器第一条，无起点）'}")
        if g["span_hours"] is not None:
            print(f"      空档时长  : {g['span_hours']:.1f} 小时（{g['span_hours']/24:.1f} 天）")
        print(f"      当前减排量: {g['gap_row']['carbon_reduction']}")
        if g["bridged"]:
            print(f"      已扣已核查: {bd.get('bridge_verified')}"
                  f"（全段 {bd.get('bridge_span')}）")
        print(f"      说明      : {bd.get('gap_note') or bd.get('note')}")
    return 0


def cmd_bridge(project, machine, at, latest, verified_manual, assume_yes):
    conn = get_db()
    try:
        gaps = _find_gaps(conn, machine["id"])
        gap, err = _pick_gap(gaps, at=at, latest=latest)
        if err:
            print(f"[失败] {err}")
            return 1

        gap_row, prev_row = gap["gap_row"], gap["prev_row"]
        if gap.get("bridged"):
            print("[提示] 这次断档**之前已经兜底过**，本次会按当前已核查量重新计算并覆盖。")
        if not prev_row:
            print("[跳过] 这次断档是该机器的第一条读数，前面没有起点读数，"
                  "算不出空档期间的量（也就没有要补记的东西）。")
            return 0

        print("=" * 74)
        print("断档兜底计算")
        print("=" * 74)
        print(f"  项目      : {project.get('project_code') or project['id']} "
              f"{project.get('project_name')}")
        print(f"  机器      : #{machine['id']} {machine['name']}")
        print(f"  空档起点  : {prev_row['timestamp']}")
        print(f"  断档行    : {gap_row['timestamp']}"
              f"（{gap['span_hours']:.1f} 小时 = {gap['span_hours']/24:.1f} 天）")

        # 算法全在 core/gap_bridge.preview_bridge（界面接口用的是同一个函数），
        # 这里只负责把结果打印成人能看的格式。
        from core.data_import import get_required_raw_fields
        formula = _get_formula(machine, project.get("hook_name", ""))
        raw_fields = get_required_raw_fields(project)

        pv = preview_bridge(project, machine, gap, formula, raw_fields,
                            verified_manual=verified_manual)

        r_span = float(pv.get("r_span") or 0)
        print(f"\n  ① 全段减排量 R_span = {r_span:.6f} tCO₂e")
        print(f"     基准线 {pv.get('breakdown', {}).get('baseline_emissions_tco2')} / "
              f"项目 {pv.get('breakdown', {}).get('project_emissions_tco2')}")

        if not pv.get("ok"):
            print(f"\n[结束] {pv.get('error')}")
            # 拉不到核查记录 / 起点缺字段这两类，明确指引手工指定
            err = str(pv.get("error") or "")
            if "核查" in err or "缺少字段" in err:
                print("       可以改用 --verified <金额> 手工指定该段的已核查量。")
                return 1
            return 0

        v = float(pv.get("verified") or 0)
        vdetail = pv.get("rounds_detail") or []
        if pv.get("verified_source") == "manual":
            print(f"\n  ② 已核查量 = {v:.6f}（命令行手工指定）")
        else:
            print(f"\n  ② 已核查量 = {v:.6f} tCO₂e"
                  f"（命中 {len(pv.get('matched') or [])} 轮，按重叠天数摊分）")
            for d in vdetail:
                print(f"       第{d['round']}轮 {d['start']}~{d['end']} "
                      f"核证 {d['amount']:.4f} tCO₂e，"
                      f"落在空档内 {d['days']}/{d['total_days']} 天 → 摊 {d['prorated']:.4f}")
            if not vdetail:
                print("       注意：没有任何核查轮次的期间与这段空档重叠 → 不做扣减，"
                      "整段都会补记。如与实际不符请用 --verified 手工指定。")

        booked = float(pv.get("booked") or 0)
        print(f"\n  ③ 补记量 = R_span − 已核查量 = {r_span:.6f} − {v:.6f} = {booked:.6f}")

        bf, af = pv.get("before") or {}, pv.get("after") or {}
        print(f"\n  ④ 将把断档行改写为：")
        print(f"       减排量      : {bf.get('carbon_reduction')} → {af.get('carbon_reduction')}")
        print(f"       累计(本机器): {bf.get('cumulative_reduction')} → {af.get('cumulative_reduction')}")
        print(f"       基准线排放  : → {af.get('baseline_emissions_tco2')}")
        print(f"       项目排放    : → {af.get('project_emissions_tco2')}")
        print(f"       breakdown 标记 bridge=True（报告里能看出这是兜底补记，不是原始采集）")
        for w in pv.get("warnings") or []:
            print(f"       ⚠ {w}")

        if not assume_yes:
            print("\n[预演] 未写入。确认无误后加 --yes 执行（会自动备份数据库）。")
            return 0

        res = apply_bridge(conn, project, machine, gap, formula, raw_fields,
                           verified_manual=verified_manual)
        if not res.get("ok"):
            print(f"\n[失败] {res.get('error')}")
            return 1
        if res.get("backup_path"):
            print(f"\n[备份] {res['backup_path']}")

        w = res.get("written") or {}
        print(f"\n[完成] 已写入补记 {w.get('carbon_reduction'):.6f} tCO₂e；"
              f"项目累计核证减排量更新为 {float(w.get('project_cumulative') or 0):.4f}")
        print("       MQTT 继续正常采集，不受影响。")
        return 0
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description="断档兜底：用已核查量顶掉空档，只补记未核查部分")
    ap.add_argument("--project", required=True, help="项目编号 / 名称 / id")
    ap.add_argument("--machine", help="机器 id（不填=该项目的1号机器）")
    ap.add_argument("--list", action="store_true", help="只列出断档，不做任何修改")
    ap.add_argument("--at", help="指定某次断档（用断档行的时间戳，见 --list）")
    ap.add_argument("--latest", action="store_true", help="用最近一次断档")
    ap.add_argument("--verified", type=float, default=None,
                    help="手工指定该空档的已核查量（拉不到核查记录时用）")
    ap.add_argument("--yes", action="store_true", help="确认写入（不加=预演）")
    args = ap.parse_args()

    project = _resolve_project(args.project)
    if not project:
        print(f"[失败] 找不到项目: {args.project}")
        return 1
    machine = _resolve_machine(project["id"], args.machine)
    if not machine:
        print("[失败] 找不到机器")
        return 1

    if args.list:
        return cmd_list(project, machine)
    return cmd_bridge(project, machine, args.at, args.latest, args.verified, args.yes)


if __name__ == "__main__":
    sys.exit(main())
