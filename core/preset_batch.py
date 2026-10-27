"""
预设批量编辑 — 通过 Excel 批量导出/导入各项目的预设值。

设计（对齐需求：同一值放全项目通用字段）：
- 导出：生成一个 .xlsx，行为「字段」，列为「项目」。
  字段按三层分组：全项目通用(global) / 项目通用(shared) / 项目独有(project)。
  global 字段的值对所有项目一致（导出时取该字段全局值，各列相同）。
  shared/project 字段各项目取各自值。
- 导入：解析 Excel，回写各项目的 shared/project 字段值；
  global 字段回写全局值（整列取第一个非空值）。

Excel 结构约定：
  第 1 行：表头，第 1 列固定为「字段key」，其后每列是「项目名」。
  第 2 行：区段标题行（如「=== 全项目通用 ===」），值填充在其后的数据行。
  数据行：第 1 列 = 字段 key，其后 = 各项目该字段的值。

为鲁棒，字段行通过第 1 列的 key 精确匹配；区段标题行第 1 列以「==」开头。
"""

import json
import re
from datetime import datetime

from .project_manager import get_db
from .project_presets import (
    load_all_project_presets, load_global_presets, load_shared_presets,
    load_project_presets, save_project_presets, save_global_presets,
    _write_value_override,
)


def _field_display(p):
    return f"{p.get('label', p.get('key', ''))} ({p.get('key', '')})"


def build_export(project_ids):
    """构建批量导出的二维数组。

    返回:
        (rows, errors) — rows 为二维数组；errors 为字符串列表（跳过的项目等）。
    """
    projects = []
    errors = []
    for pid in project_ids:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        from .project_manager import get_project
        p = get_project(pid)
        if not p:
            errors.append(f"项目 #{pid} 不存在，已跳过")
            continue
        projects.append(p)

    global_presets = load_global_presets()
    shared_presets = load_shared_presets()

    # 收集各项目数据
    proj_data = {}  # pid -> {key: value}
    proj_own_keys = set()  # 所有项目独有的 key 并集
    for p in projects:
        merged = load_all_project_presets(p["id"])
        kv = {x.get("key"): x.get("value", "") for x in merged if x.get("key")}
        proj_data[p["id"]] = kv
        for x in merged:
            if x.get("scope") == "project":
                proj_own_keys.add(x.get("key"))

    # 字段行：global / shared / project 三段
    header = ["字段"] + [p["project_name"] for p in projects]

    rows = [header]
    # 说明行
    rows.append(["（说明）修改下方各单元格后重新导入即可批量更新；全项目通用字段的值对所有项目一致，请保持各列相同"])
    rows.append([""])

    def _add_section(title):
        rows.append([f"== {title} =="] + [""] * len(projects))

    def _add_field(key, label, getter):
        row = [f"{label} ({key})"]
        for p in projects:
            row.append(getter(p["id"], key))
        rows.append(row)

    # 全项目通用
    if global_presets:
        _add_section("全项目通用（值对所有项目一致）")
        for gp in global_presets:
            key = gp.get("key")
            row = [_field_display(gp)]
            for p in projects:
                row.append(gp.get("value", ""))
            rows.append(row)

    # 项目通用字段
    if shared_presets:
        _add_section("项目通用（各项目分别填值）")
        for sp in shared_presets:
            key = sp.get("key")
            if sp.get("type") == "code":
                # code 类型值对所有项目相同，存共享定义，标记只读
                row = [f"{_field_display(sp)}（代码，全项目共用）"]
                for p in projects:
                    row.append(sp.get("value", ""))
                rows.append(row)
                continue
            row = [_field_display(sp)]
            for p in projects:
                row.append(proj_data[p["id"]].get(key, ""))
            rows.append(row)

    # 项目独有
    if proj_own_keys:
        _add_section("项目独有（仅列出的项目有该字段）")
        for key in sorted(proj_own_keys):
            # 取第一个有该字段的项目拿 label
            label = key
            for p in projects:
                merged = proj_data.get(p["id"], {})
                if key in merged:
                    # 从 load_all 拿 label
                    for x in load_all_project_presets(p["id"]):
                        if x.get("key") == key:
                            label = x.get("label", key)
                            break
                    break
            row = [f"{label} ({key})"]
            for p in projects:
                row.append(proj_data[p["id"]].get(key, ""))
            rows.append(row)

    if not global_presets and not shared_presets and not proj_own_keys:
        errors.append("所选项目没有任何预设字段")

    return rows, errors


def _parse_key(cell):
    """从「label (key)」或「key」中提取 key。"""
    s = (cell or "").strip()
    if not s:
        return None
    m = re.search(r"\(([a-zA-Z_][a-zA-Z0-9_]*)\)\s*$", s)
    if m:
        return m.group(1)
    if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", s):
        return s
    return None


def apply_import(project_ids, grid):
    """把解析后的二维数组回写到各项目。

    参数:
        project_ids: 项目 id 列表（与列对应）
        grid: read_xlsx 返回的二维数组

    返回:
        dict: {"status": "...", "imported": N, "updated_fields": [...],
               "errors": [...]}
    """
    if not grid or len(grid) < 2:
        return {"status": "失败", "msg": "Excel 内容为空"}

    header = grid[0]
    # 列 → 项目映射：第 1 列是 key，其后每列对应一个项目
    cols = header[1:]
    if len(cols) != len(project_ids):
        return {"status": "失败", "msg": f"Excel 项目列数({len(cols)})与所选项目数({len(project_ids)})不一致"}

    projects = []
    from .project_manager import get_project
    for pid in project_ids:
        p = get_project(int(pid))
        if not p:
            return {"status": "失败", "msg": f"项目 #{pid} 不存在"}
        if p.get("lifecycle_status") != "active":
            return {"status": "失败", "msg": f"项目 #{pid} 已作废，预设只读"}
        projects.append(p)

    global_presets = load_global_presets()
    global_map = {p.get("key"): p for p in global_presets}
    shared_map = {p.get("key"): p for p in load_shared_presets()}

    # 遍历数据行
    updated = []          # (key, 项目数)
    errors = []
    section = ""          # 当前区段

    # 收集每列的写入计划：{pid: {key: value}}
    writes = {p["id"]: {} for p in projects}
    global_writes = {}    # {key: value}
    shared_code_writes = {}  # code 类型共享定义

    for row in grid[1:]:
        if not row or not any(str(c).strip() for c in row):
            continue
        first = str(row[0] or "").strip()
        if first.startswith("=="):
            section = first.strip("= ").strip()
            continue
        if first.startswith("（") or first == "":
            continue

        key = _parse_key(first)
        if not key:
            continue

        values = [str(c).strip() if c is not None else "" for c in row[1:len(row)]]
        # 补足列数
        while len(values) < len(projects):
            values.append("")

        is_global = key in global_map
        is_shared_code = key in shared_map and shared_map[key].get("type") == "code"

        if is_global:
            # 全局：取整列第一个非空值
            first_val = next((v for v in values if v), "")
            global_writes[key] = first_val
        elif is_shared_code:
            first_val = next((v for v in values if v), "")
            shared_code_writes[key] = first_val
        else:
            for i, p in enumerate(projects):
                writes[p["id"]][key] = values[i]

    # ── 回写全局预设 ──
    for key, val in global_writes.items():
        if key in global_map:
            global_map[key]["value"] = val
    if global_writes:
        save_global_presets(global_presets)
        updated.append(("global", len(global_writes)))

    # ── 回写 shared code 定义 ──
    if shared_code_writes:
        shared_list = load_shared_presets()
        for sp in shared_list:
            if sp.get("key") in shared_code_writes:
                sp["value"] = shared_code_writes[sp.get("key")]
        from .project_presets import save_shared_presets
        save_shared_presets(shared_list)
        updated.append(("shared_code", len(shared_code_writes)))

    # ── 回写各项目字段 ──
    # 区分两类写入，避免快照互相覆盖：
    # - shared 字段：用 _write_value_override（内部自 load+save），不参与 own 快照
    # - project 独有字段：改 own 快照后统一 save
    for p in projects:
        pid = p["id"]
        own = load_project_presets(pid)
        own_map = {x.get("key"): x for x in own if x.get("key")}
        project_changed = False
        for key, val in writes[pid].items():
            if key in shared_map and shared_map[key].get("type") != "code":
                # 项目通用字段：写覆盖记录（内部独立保存）
                if own_map.get(key, {}).get("value", "") != val:
                    _write_value_override(pid, shared_map[key], val)
            elif key in own_map:
                if own_map[key].get("value", "") != val:
                    own_map[key]["value"] = val
                    project_changed = True
            else:
                # 项目独有字段，但当前项目没有 → 跳过（不自动新建）
                errors.append(f"项目「{p['project_name']}」没有字段「{key}」，已跳过该格")
        if project_changed:
            save_project_presets(pid, list(own_map.values()))

    imported = sum(len(v) for v in writes.values()) + len(global_writes) + len(shared_code_writes)
    return {
        "status": "成功",
        "imported": imported,
        "updated": updated,
        "errors": errors[:50],
    }
