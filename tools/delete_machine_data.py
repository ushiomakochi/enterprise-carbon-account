# -*- coding: utf-8 -*-
"""
删除指定机器的所有监测数据（含备份 + 重算项目累计）。

用法:
    python tools/delete_machine_data.py --machine-id 3
    python tools/delete_machine_data.py --machine-id 3 --yes   # 跳过确认

会做什么:
    1. 查该机器的监测数据条数，打印机器名 / 所属项目 / 将删除条数；
    2. 备份到 data/backup_machine_<id>_<时间戳>.json（可回滚）；
    3. 删除 monitoring_data 里该 machine_id 的所有记录；
    4. 重算所属项目的 pending_verification_cumulative。

注意:
    - 这是对生产数据库的删除操作，请先确认机器 ID 无误；
    - 备份文件在 data/ 目录，删除前会自动生成，保留它即可回滚。
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.project_manager import get_db, project_cumulative


def main():
    p = argparse.ArgumentParser(description="删除指定机器的所有监测数据")
    p.add_argument("--machine-id", type=int, required=True, help="机器 ID（project_machines.id）")
    p.add_argument("--yes", action="store_true", help="跳过确认，直接删除")
    args = p.parse_args()

    conn = get_db()
    try:
        m = conn.execute(
            "SELECT * FROM project_machines WHERE id = ?", (args.machine_id,)
        ).fetchone()
        if not m:
            print(f"❌ 机器 #{args.machine_id} 不存在")
            return

        proj_id = m["project_id"]
        proj = conn.execute(
            "SELECT project_name FROM carbon_projects WHERE id = ?", (proj_id,)
        ).fetchone()
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM monitoring_data WHERE machine_id = ?",
            (args.machine_id,)
        ).fetchone()["c"]

        print("=" * 56)
        print(f"  机器 #{args.machine_id}：{m['name']}")
        print(f"  所属项目：{proj['project_name'] if proj else proj_id}")
        print(f"  监测数据：{cnt} 条")
        print("=" * 56)

        if cnt == 0:
            print("该机器没有监测数据，无需删除。")
            return

        if not args.yes:
            ans = input("确认删除？输入 yes 继续：").strip().lower()
            if ans != "yes":
                print("已取消。")
                return

        # 1. 备份
        rows = conn.execute(
            "SELECT * FROM monitoring_data WHERE machine_id = ?", (args.machine_id,)
        ).fetchall()
        backup = [dict(r) for r in rows]
        backup_path = os.path.join(
            config.data_path(),
            f"backup_machine_{args.machine_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json")
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2)
        print(f"✅ 已备份 {len(backup)} 条 → {backup_path}")

        # 2. 删除
        conn.execute("DELETE FROM monitoring_data WHERE machine_id = ?", (args.machine_id,))
        conn.commit()
        print(f"✅ 已删除 {cnt} 条监测数据")

        # 3. 重算项目累计
        total = project_cumulative(conn, proj_id)
        conn.execute(
            """UPDATE carbon_projects SET pending_verification_cumulative = ?,
                   updated_at = datetime('now','localtime') WHERE id = ?""",
            (total, proj_id))
        conn.commit()
        print(f"✅ 重算项目累计 = {total} tCO₂e")
        print("完成。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
