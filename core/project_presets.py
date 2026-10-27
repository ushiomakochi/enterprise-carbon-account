"""
项目预设信息管理 — 每个项目独立的模板变量。

与 core/presets.py（企业级）对应，项目独有数据存储在 carbon_projects.presets 列（JSON 数组）。

支持类型:
- text:     纯文本，模板用 {{ project_preset.<key> }}
- richtext: 富文本（HTML），渲染时 strip 标签
- image:    图片，渲染时转为 InlineImage（顶层注入 like 企业预设）
- computed: 计算值，value 存表达式（如 "estimated_annual_reduction * 10"），
            渲染时从项目字段取值计算

通用字段（所有项目共享）:
- 字段定义（key/label/type/category/sort_order）存于 data/project_presets_shared.json
- 字段值仍按项目分别存储：项目填值后在 carbon_projects.presets 中写入一条同 key
  覆盖记录（仅承载该项目的值），加载时合并：定义以通用为准，值以项目覆盖为准
- 项目独有预设（scope=project）只存在于当前项目

用法:
    from core.project_presets import (load_all_project_presets,
                                       add_project_preset, update_project_preset,
                                       delete_project_preset, get_project_presets_context)

    presets = load_all_project_presets(project_id)  # 通用+独有合并视图（带 scope 标记）
    ctx = get_project_presets_context(project_id, project_dict)
    # → {"install_address": "广州市花都区...", "equipment_model": "ZHQZ..."}
"""

import os
import re
import ast
import json
import time
import threading
from datetime import datetime

from .project_manager import get_db
from . import json_store as _json_store

# ── 分类 ──（项目预设独立的一套，与企业预设的 CATEGORIES 不通用）
CATEGORIES = ["项目信息", "设备信息", "监测参数", "审定信息", "计算值", "自定义"]

# ── 通用字段定义存储文件 ──
import config as _config
_SHARED_FILE = _config.data_path("project_presets_shared.json")

# ── 全项目通用预设存储文件（值对所有项目相同，独立于企业预设/项目预设）──
_GLOBAL_FILE = _config.data_path("project_presets_global.json")

# ── 通用字段的权威定义 ──
# 这份列表必须与 data/project_presets_shared.json 保持一致：
# 该 JSON 丢失时会按这里重建，对不上就会静默少字段（老版本曾因此丢过 install_image 等）。
# 新增通用字段时，这里和 JSON 都要加。
_DEFAULT_PRESETS = [
    # 项目信息
    {"key": "install_address",        "label": "安装地址",             "type": "text",  "value": "", "category": "项目信息", "sort_order": 1},
    {"key": "install_location_lnglat", "label": "安装位置（经纬度）",   "type": "text",  "value": "", "category": "项目信息", "sort_order": 2},
    {"key": "crediting_period",       "label": "计入期",               "type": "text",  "value": "", "category": "项目信息", "sort_order": 5},
    {"key": "project_install_company", "label": "项目安装公司",         "type": "text",  "value": "", "category": "项目信息", "sort_order": 6},
    {"key": "project_install_date",   "label": "项目安装时间",         "type": "text",  "value": "", "category": "项目信息", "sort_order": 7},
    {"key": "produce_date",           "label": "生产日期",             "type": "text",  "value": "", "category": "项目信息", "sort_order": 8},
    {"key": "installation_condition", "label": "安装情况",             "type": "text",  "value": "", "category": "项目信息", "sort_order": 9},
    {"key": "project_location_image", "label": "项目位置图",           "type": "image", "value": "", "category": "项目信息", "sort_order": 10},
    {"key": "unique_statement",       "label": "项目及减排量唯一性声明", "type": "image", "value": "", "category": "项目信息", "sort_order": 11},
    {"key": "install_image",          "label": "项目安装图片",         "type": "image", "value": "", "category": "项目信息", "sort_order": 12},
    {"key": "investment_barriers",    "label": "投资障碍",             "type": "text",  "value": "", "category": "项目信息", "sort_order": 13},
    {"key": "technical_barrier",      "label": "技术障碍",             "type": "text",  "value": "", "category": "项目信息", "sort_order": 14},
    # 设备信息
    {"key": "equipment_name",         "label": "设备名称",             "type": "text",  "value": "", "category": "设备信息", "sort_order": 20},
    {"key": "equipment_model",        "label": "设备型号",             "type": "text",  "value": "", "category": "设备信息", "sort_order": 21},
    {"key": "equipment_qty",          "label": "设备数量",             "type": "text",  "value": "", "category": "设备信息", "sort_order": 22},
    {"key": "rated_power",            "label": "铭牌功率（kW）",       "type": "text",  "value": "", "category": "设备信息", "sort_order": 23},
    {"key": "rated_output",           "label": "铭牌产气量（m³/min）", "type": "text",  "value": "", "category": "设备信息", "sort_order": 24},
    # 审定信息 —— 审定机构随项目变（今年 A 机构、明年可能换 B），所以放项目预设而非企业预设
    {"key": "approval_org",              "label": "审定机构名称",           "type": "text", "value": "", "category": "审定信息", "sort_order": 30},
    {"key": "approval_org_address",      "label": "审定机构地址",           "type": "text", "value": "", "category": "审定信息", "sort_order": 31},
    {"key": "approval_org_legal_person", "label": "审定机构法人代表",       "type": "text", "value": "", "category": "审定信息", "sort_order": 32},
    {"key": "approval_org_credit_code",  "label": "审定机构统一社会信用代码", "type": "text", "value": "", "category": "审定信息", "sort_order": 33},
    {"key": "approval_contact_person",   "label": "审定机构联系人",         "type": "text", "value": "", "category": "审定信息", "sort_order": 34},
    {"key": "approval_contact_phone",    "label": "审定机构联系电话",       "type": "text", "value": "", "category": "审定信息", "sort_order": 35},
    {"key": "approval_contact_email",    "label": "审定机构联系人邮箱",     "type": "text", "value": "", "category": "审定信息", "sort_order": 36},
    {"key": "approval_contact_fax",      "label": "审定机构传真",           "type": "text", "value": "", "category": "审定信息", "sort_order": 37},
]

# 报告模板依赖的通用字段：老库升级时按 key 幂等补齐，避免模板里出现填不了的空白位。
# 删字段时务必同步删这里，否则下次启动会补回来。
_REQUIRED_PRESETS = [
    "approval_org", "approval_org_address", "approval_org_legal_person",
    "approval_org_credit_code", "approval_contact_person", "approval_contact_phone",
    "approval_contact_email", "approval_contact_fax",
]


# ══════════════════════════════════════════════════════════
# CRUD
# ══════════════════════════════════════════════════════════

# ── JSON 定义文件的安全读写 ──
# 具体的重试/原子写/并发锁都在 core/json_store.py，那边解释了为什么必须这么做
# （网络盘 + 并发请求 → 读失败静默返回空列表 → 误报「不存在」甚至清空文件）。
_WRITE_LOCK = _json_store.WRITE_LOCK
_read_json_list = _json_store.read_json_list
_write_json_atomic = _json_store.write_json_atomic


def load_project_presets(project_id):
    """从 carbon_projects.presets 加载该项目所有预设。

    返回:
        list[dict]: 按 sort_order 排序的预设列表
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT presets FROM carbon_projects WHERE id = ?", (project_id,)
        ).fetchone()
        if row:
            try:
                presets = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or [])
                if isinstance(presets, list):
                    presets.sort(key=lambda x: x.get("sort_order", 99))
                    return presets
            except (json.JSONDecodeError, TypeError):
                pass
        return []
    finally:
        conn.close()


def save_project_presets(project_id, presets):
    """保存预设列表到 carbon_projects.presets"""
    conn = get_db()
    try:
        project_state = conn.execute(
            "SELECT lifecycle_status FROM carbon_projects WHERE id=?", (project_id,)).fetchone()
        if not project_state or project_state["lifecycle_status"] != "active":
            raise ValueError("项目已作废，预设只读")
        conn.execute(
            "UPDATE carbon_projects SET presets = ?, updated_at = ? WHERE id = ?",
            (json.dumps(presets, ensure_ascii=False),
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             project_id)
        )
        conn.commit()
    except Exception as e:
        raise RuntimeError(f"保存项目预设失败: {e}")
    finally:
        conn.close()


def _ensure_defaults(project_id):
    """历史兼容：早期版本会为每个项目写入一份默认预设副本。

    现在默认预设改为「通用字段」（定义共享、值按项目覆盖），
    此函数不再向项目写入副本，仅返回该项目已有的预设列表。
    """
    return load_project_presets(project_id)


# ══════════════════════════════════════════════════════════
# 通用字段（所有项目共享定义，值按项目覆盖）
# ══════════════════════════════════════════════════════════

def load_shared_presets():
    """加载通用项目预设字段定义。首次访问时用 _DEFAULT_PRESETS 初始化。"""
    if not os.path.exists(_SHARED_FILE):
        defaults = json.loads(json.dumps(_DEFAULT_PRESETS, ensure_ascii=False))
        save_shared_presets(defaults)
        return defaults
    return _read_json_list(_SHARED_FILE, "项目通用预设定义")


def save_shared_presets(presets):
    """保存通用项目预设字段定义（原子写）"""
    _write_json_atomic(_SHARED_FILE, presets)


def ensure_required_project_presets():
    """把 _REQUIRED_PRESETS 里缺失的通用字段补进共享定义文件（按 key 幂等）。

    老库升级时用：审定备案申请书等模板引用的字段，如果共享定义里没有，
    各项目的预设面板就不会出现这一格，模板渲染出来是空白且没有任何提示。
    只补「定义」，不动各项目已填的值；用户删掉的字段下次启动会被补回来
    ——这是有意的，它们对应模板的必填位。
    """
    try:
        existing = {p.get("key") for p in load_shared_presets()}
        defaults = {p["key"]: p for p in _DEFAULT_PRESETS}
        added = []
        shared = load_shared_presets()
        for key in _REQUIRED_PRESETS:
            if key in existing:
                continue
            tpl = defaults.get(key)
            if not tpl:
                continue
            shared.append(dict(tpl))
            added.append(key)
        if added:
            shared.sort(key=lambda p: p.get("sort_order", 99))
            save_shared_presets(shared)
            print(f"[project_presets] 已补齐通用字段: {'、'.join(added)}")
    except Exception as e:
        print(f"[project_presets] 补齐通用字段失败（可忽略）: {e}")


def load_global_presets():
    """加载全项目通用预设（scope=global，值对所有项目相同）。"""
    if not os.path.exists(_GLOBAL_FILE):
        return []
    return _read_json_list(_GLOBAL_FILE, "全项目通用预设定义")


def save_global_presets(presets):
    """保存全项目通用预设。"""
    os.makedirs(os.path.dirname(_GLOBAL_FILE), exist_ok=True)
    with open(_GLOBAL_FILE, "w", encoding="utf-8") as f:
        json.dump(presets, f, ensure_ascii=False, indent=2)


def load_all_project_presets(project_id):
    """合并视图：全项目通用（global）+ 项目通用字段（shared）+ 项目独有（project）。

    - global：值对所有项目相同，直接用定义里的 value
    - shared：定义共享，值取该项目覆盖记录（无覆盖则空）
    - project：仅当前项目
    """
    shared = load_shared_presets()
    global_presets = load_global_presets()
    own = load_project_presets(project_id)
    own_map = {p.get("key"): p for p in own if p.get("key")}

    merged = []
    # 全项目通用（值相同）
    for g in global_presets:
        key = g.get("key")
        if not key:
            continue
        item = dict(g)
        item["scope"] = "global"
        merged.append(item)

    for s in shared:
        key = s.get("key")
        if not key:
            continue
        item = dict(s)
        override = own_map.pop(key, None)
        if override is not None:
            item["value"] = override.get("value", "")
        item["scope"] = "shared"
        merged.append(item)

    for key, o in own_map.items():
        item = dict(o)
        item["scope"] = "project"
        merged.append(item)

    merged.sort(key=lambda x: x.get("sort_order", 99))
    return merged


def _write_value_override(project_id, shared_def, value):
    """把通用字段的值写入项目覆盖记录（不存在则创建）。"""
    own = load_project_presets(project_id)
    for p in own:
        if p.get("key") == shared_def["key"]:
            p["value"] = value if value else ""
            save_project_presets(project_id, own)
            return
    own.append({
        "key": shared_def["key"],
        "label": shared_def.get("label", shared_def["key"]),
        "type": shared_def.get("type", "text"),
        "value": value if value else "",
        "category": shared_def.get("category", "自定义"),
        "sort_order": shared_def.get("sort_order", 99),
    })
    save_project_presets(project_id, own)


def _rename_override_key_in_all_projects(old_key, new_key):
    """通用字段改名时，同步改名所有项目中的值覆盖记录。"""
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, presets FROM carbon_projects").fetchall()
        for row in rows:
            try:
                presets = json.loads(row["presets"]) if isinstance(row["presets"], str) else (row["presets"] or [])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(presets, list):
                continue
            changed = False
            for p in presets:
                if p.get("key") == old_key:
                    p["key"] = new_key
                    changed = True
            if changed:
                conn.execute(
                    "UPDATE carbon_projects SET presets = ? WHERE id = ?",
                    (json.dumps(presets, ensure_ascii=False), row["id"])
                )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def add_project_preset_locked(project_id, key, label, preset_type="text", value="",
                       category="自定义", sort_order=None, scope="project"):
    """添加一个新的项目预设。

    参数:
        scope: "project"=仅当前项目；
               "shared"=项目通用字段（所有项目都有，各项目分别填值）；
               "global"=全项目通用（值对所有项目相同）
    """
    if not key or not label:
        return {"status": "失败", "msg": "预设 key 和标签不能为空"}

    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
        return {"status": "失败", "msg": "Key 只能包含英文字母、数字和下划线，且以字母或下划线开头"}

    if preset_type not in ("text", "richtext", "image", "computed", "code", "table"):
        return {"status": "失败", "msg": f"不支持的预设类型: {preset_type}"}

    # 查重：全项目通用 + 项目通用字段 + 当前项目存储
    global_presets = load_global_presets()
    shared = load_shared_presets()
    own = load_project_presets(project_id)
    if any(p.get("key") == key for p in global_presets):
        return {"status": "失败", "msg": f"全项目通用预设中已存在 key '{key}'"}
    if any(p.get("key") == key for p in shared):
        return {"status": "失败", "msg": f"通用字段中已存在 key '{key}'"}
    if any(p.get("key") == key for p in own):
        return {"status": "失败", "msg": f"预设 key '{key}' 已存在"}

    category = category if category in CATEGORIES else "自定义"

    if scope == "global":
        # 校验：全项目通用 code 不能引用「值各项目不同」的字段
        err = validate_global_code_refs(value, shared, own)
        if err:
            return {"status": "失败", "msg": err}
        if sort_order is None:
            sort_order = max((p.get("sort_order", 0) for p in global_presets), default=0) + 1
        new_def = {
            "key": key, "label": label.strip(), "type": preset_type,
            "value": value, "category": category, "sort_order": sort_order,
        }
        global_presets.append(new_def)
        save_global_presets(global_presets)
        result = dict(new_def)
        result["scope"] = "global"
        return {"status": "成功", "preset": result}

    if scope == "shared":
        if sort_order is None:
            sort_order = max((p.get("sort_order", 0) for p in shared), default=0) + 1
        is_code = (preset_type == "code")
        new_def = {
            "key": key, "label": label.strip(), "type": preset_type,
            # code 类型的值（代码逻辑）对所有项目共用，存共享定义；
            # 其它类型各项目分别填值，定义里留空
            "value": value if is_code else "",
            "category": category, "sort_order": sort_order,
        }
        shared.append(new_def)
        save_shared_presets(shared)
        # 非 code 类型：创建时若带了值，写入当前项目的覆盖记录
        if value and not is_code:
            _write_value_override(project_id, new_def, value)
        result = dict(new_def)
        result["scope"] = "shared"
        return {"status": "成功", "preset": result}

    if sort_order is None:
        sort_order = max((p.get("sort_order", 0) for p in own), default=0) + 1
    new_preset = {
        "key": key, "label": label.strip(), "type": preset_type,
        "value": value,
        "category": category,
        "sort_order": sort_order,
    }
    own.append(new_preset)
    save_project_presets(project_id, own)
    result = dict(new_preset)
    result["scope"] = "project"
    return {"status": "成功", "preset": result}


def _apply_meta(preset, data, valid_types):
    """应用元数据字段（label/type/category/sort_order）到 preset dict。"""
    if "label" in data:
        preset["label"] = data["label"].strip()
    if "type" in data and data["type"] in valid_types:
        preset["type"] = data["type"]
    if "category" in data:
        preset["category"] = data["category"] if data["category"] in CATEGORIES else "自定义"
    if "sort_order" in data and isinstance(data["sort_order"], (int, float)):
        preset["sort_order"] = int(data["sort_order"])


def update_project_preset_locked(project_id, key, data):
    """更新指定 key 的预设（自动识别 global/shared/project，支持 scope 转换）。

    - 仅传 value：global → 更新全局值；shared → 写项目覆盖（code 存定义）；project → 更新值
    - 传元数据（label/type/category/sort_order/new_key）：更新所在存储的定义
    - 传 scope 且与当前不同：在 global / shared / project 之间转换
    """
    global_presets = load_global_presets()
    shared = load_shared_presets()
    own = load_project_presets(project_id)
    g_idx = next((i for i, p in enumerate(global_presets) if p.get("key") == key), None)
    s_idx = next((i for i, p in enumerate(shared) if p.get("key") == key), None)
    o_idx = next((i for i, p in enumerate(own) if p.get("key") == key), None)

    if g_idx is None and s_idx is None and o_idx is None:
        return {"status": "失败", "msg": f"预设 '{key}' 不存在"}

    if g_idx is not None:
        current_scope = "global"
    elif s_idx is not None:
        current_scope = "shared"
    else:
        current_scope = "project"

    target_scope = data.get("scope") if data.get("scope") in ("global", "project", "shared") else current_scope

    # ── 仅更新值 ──
    only_value = set(data.keys()) <= {"value"}
    if only_value:
        value = data.get("value", "")
        if current_scope == "global":
            global_presets[g_idx]["value"] = value
            save_global_presets(global_presets)
            return {"status": "成功", "preset": {**global_presets[g_idx], "scope": "global"}}
        if current_scope == "shared":
            if shared[s_idx].get("type") == "code":
                shared[s_idx]["value"] = value
                save_shared_presets(shared)
                return {"status": "成功", "preset": {**shared[s_idx], "value": value, "scope": "shared"}}
            _write_value_override(project_id, shared[s_idx], value)
            return {"status": "成功", "preset": {**shared[s_idx], "value": value, "scope": "shared"}}
        own[o_idx]["value"] = value if value else ""
        save_project_presets(project_id, own)
        return {"status": "成功", "preset": {**own[o_idx], "scope": "project"}}

    # ── scope 转换 ──
    if target_scope != current_scope:
        if current_scope == "global":
            item = dict(global_presets[g_idx])
            cur_value = item.get("value", "")
        elif current_scope == "shared":
            item = dict(shared[s_idx])
            # code 类型的值（代码逻辑）存在 shared 定义本身；其它类型值在项目覆盖记录
            if item.get("type") == "code":
                cur_value = item.get("value", "")
            else:
                cur_value = own[o_idx].get("value", "") if o_idx is not None else ""
        else:
            item = dict(own[o_idx])
            cur_value = item.get("value", "")

        # 校验：转为 global 时，code 不能引用「值各项目不同」的字段
        if target_scope == "global" and item.get("type") == "code":
            err = validate_global_code_refs(cur_value, shared, own)
            if err:
                return {"status": "失败", "msg": err}

        # 从当前存储移除定义（shared 的覆盖记录一并移除）
        if current_scope == "global":
            global_presets.pop(g_idx)
        elif current_scope == "shared":
            shared.pop(s_idx)
            if o_idx is not None:
                own.pop(o_idx)
        else:
            own.pop(o_idx)

        new_item = {
            "key": item["key"], "label": item.get("label", item["key"]),
            "type": item.get("type", "text"),
            "category": item.get("category", "自定义"),
            "sort_order": item.get("sort_order", 99),
            "value": "",
        }
        _apply_meta(new_item, data, ("text", "richtext", "image", "computed", "code", "table"))

        if target_scope == "global":
            new_item["value"] = cur_value
            global_presets.append(new_item)
            save_global_presets(global_presets)
            save_shared_presets(shared)
            save_project_presets(project_id, own)
            return {"status": "成功", "preset": {**new_item, "scope": "global"}}
        elif target_scope == "shared":
            is_code = new_item.get("type") == "code"
            new_item["value"] = cur_value if is_code else ""
            shared.append(new_item)
            save_shared_presets(shared)
            if cur_value and not is_code:
                _write_value_override(project_id, new_item, cur_value)
            return {"status": "成功", "preset": {**new_item, "scope": "shared"}}
        else:
            new_item["value"] = cur_value
            own.append(new_item)
            save_project_presets(project_id, own)
            return {"status": "成功", "preset": {**new_item, "scope": "project"}}

    # ── 元数据更新（scope 不变）──
    new_key = data.get("new_key", "").strip() if data.get("new_key") else None

    if current_scope == "global":
        target = global_presets[g_idx]
        if new_key and new_key != key:
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', new_key):
                return {"status": "失败", "msg": "新 Key 格式不合法"}
            if any(p.get("key") == new_key for p in global_presets):
                return {"status": "失败", "msg": f"新 Key '{new_key}' 已存在"}
            target["key"] = new_key
        _apply_meta(target, data, ("text", "richtext", "image", "computed", "code", "table"))
        if "value" in data:
            target["value"] = data["value"]
        save_global_presets(global_presets)
        result = dict(target)
        result["scope"] = "global"
        return {"status": "成功", "preset": result}

    if current_scope == "shared":
        target = shared[s_idx]
        if new_key and new_key != key:
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', new_key):
                return {"status": "失败", "msg": "新 Key 格式不合法"}
            if any(p.get("key") == new_key for p in shared):
                return {"status": "失败", "msg": f"新 Key '{new_key}' 已存在"}
            target["key"] = new_key
            _rename_override_key_in_all_projects(key, new_key)
        _apply_meta(target, data, ("text", "richtext", "image", "computed", "code", "table"))
        if "value" in data:
            if target.get("type") == "code":
                target["value"] = data["value"]
            else:
                _write_value_override(project_id, target, data["value"])
        save_shared_presets(shared)
        result = dict(target)
        if o_idx is not None:
            result["value"] = data.get("value", own[o_idx].get("value", "")) if "value" in data else own[o_idx].get("value", "")
        result["scope"] = "shared"
        return {"status": "成功", "preset": result}

    # 项目独有
    target = own[o_idx]
    if new_key and new_key != key:
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', new_key):
            return {"status": "失败", "msg": "新 Key 格式不合法"}
        for p in own:
            if p.get("key") == new_key:
                return {"status": "失败", "msg": f"新 Key '{new_key}' 已存在"}
        if any(p.get("key") == new_key for p in shared) or any(p.get("key") == new_key for p in global_presets):
            return {"status": "失败", "msg": f"通用字段中已存在 key '{new_key}'"}
        target["key"] = new_key
    _apply_meta(target, data, ("text", "richtext", "image", "computed", "code", "table"))
    if "value" in data:
        target["value"] = data["value"] if data["value"] else ""
    save_project_presets(project_id, own)
    result = dict(target)
    result["scope"] = "project"
    return {"status": "成功", "preset": result}


def delete_project_preset_locked(project_id, key, scope=None):
    """删除指定 key 的预设。

    参数:
        scope: "global"=删除全项目通用预设；"shared"=删除项目通用字段定义
               （所有项目都不再显示，各项目已填的值覆盖记录一并清除）；
               "project"=删除项目独有字段；
               None=按 key 所在存储自动判断。
    """
    global_presets = load_global_presets()
    shared = load_shared_presets()
    own = load_project_presets(project_id)
    g_idx = next((i for i, p in enumerate(global_presets) if p.get("key") == key), None)
    s_idx = next((i for i, p in enumerate(shared) if p.get("key") == key), None)
    o_idx = next((i for i, p in enumerate(own) if p.get("key") == key), None)

    if scope is None:
        if g_idx is not None:
            scope = "global"
        elif s_idx is not None:
            scope = "shared"
        else:
            scope = "project"

    if scope == "global":
        if g_idx is None:
            return {"status": "失败", "msg": f"全项目通用预设 '{key}' 不存在"}
        global_presets.pop(g_idx)
        save_global_presets(global_presets)
        return {"status": "成功"}

    if scope == "shared":
        if s_idx is None:
            return {"status": "失败", "msg": f"通用字段 '{key}' 不存在"}
        shared.pop(s_idx)
        save_shared_presets(shared)
        # 清除所有项目中的值覆盖记录
        conn = get_db()
        try:
            rows = conn.execute("SELECT id, presets FROM carbon_projects").fetchall()
            for row in rows:
                try:
                    presets = json.loads(row["presets"]) if isinstance(row["presets"], str) else (row["presets"] or [])
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(presets, list):
                    continue
                new_list = [p for p in presets if p.get("key") != key]
                if len(new_list) != len(presets):
                    conn.execute(
                        "UPDATE carbon_projects SET presets = ? WHERE id = ?",
                        (json.dumps(new_list, ensure_ascii=False), row["id"])
                    )
            conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()
        return {"status": "成功"}

    # scope == "project"
    if o_idx is None:
        return {"status": "失败", "msg": f"预设 '{key}' 在当前项目不存在"}
    own.pop(o_idx)
    save_project_presets(project_id, own)
    return {"status": "成功"}


# ══════════════════════════════════════════════════════════
# 渲染上下文
# ══════════════════════════════════════════════════════════


# ── 并发保护：把「读定义 → 改 → 写回」整段串行化 ──
# Flask 默认 threaded=True，两个请求同时改同一个预设会互相覆盖（丢更新）。
# 用可重入锁包住整段；RLock 是为了容忍内部可能的再入。
def add_project_preset(*args, **kwargs):
    with _WRITE_LOCK:
        return add_project_preset_locked(*args, **kwargs)


def update_project_preset(*args, **kwargs):
    with _WRITE_LOCK:
        return update_project_preset_locked(*args, **kwargs)


def delete_project_preset(*args, **kwargs):
    with _WRITE_LOCK:
        return delete_project_preset_locked(*args, **kwargs)


def _strip_html(html_str):
    """移除 HTML 标签，保留纯文本"""
    if not html_str:
        return ""
    text = re.sub(r'<img[^>]+alt="([^"]*)"[^>]*>', r'\1', html_str)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&amp;', '&').replace('&quot;', '"')
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()


def _eval_computed(expr, project_dict):
    """
    安全计算 computed 类型的表达式。

    支持的表达式：
    - 纯字段引用：estimated_annual_reduction
    - 算术运算：estimated_annual_reduction * 10
    - 支持的运算符：+ - * / ( )

    参数：
        expr: 表达式字符串
        project_dict: 项目字段字典

    返回：
        计算结果（float 或 0）
    """
    if not expr or not isinstance(expr, str):
        return 0
    expr = expr.strip()
    if not expr:
        return 0

    # 安全解析：只允许字段名、数字、基本算术运算符
    allowed_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ .+-*/()')
    for ch in expr:
        if ch not in allowed_chars:
            return f"[非法字符: {ch}]"

    # 构建评估环境：注入项目字段值
    env = {}
    for k, v in project_dict.items():
        if isinstance(v, (int, float)):
            env[k] = v
        elif isinstance(v, str) and v:
            try:
                env[k] = float(v)
            except ValueError:
                pass
    # 注入 hook_params 公式参数（如 has_oil、rated_pressure 等，不覆盖表列同名字段）
    try:
        raw_params = project_dict.get("hook_params", "{}")
        if isinstance(raw_params, str):
            raw_params = json.loads(raw_params)
        if isinstance(raw_params, dict):
            for k, v in raw_params.items():
                if k in env:
                    continue
                if isinstance(v, (int, float)):
                    env[k] = v
                elif isinstance(v, str) and v:
                    try:
                        env[k] = float(v)
                    except ValueError:
                        pass
    except Exception:
        pass
    # 也注入 0 为缺失字段默认值
    env.setdefault('estimated_annual_reduction', 0)
    env.setdefault('pending_validation_10yr', 0)

    # 安全 eval：只允许内置函数 __builtins__ 为空
    try:
        result = eval(expr, {"__builtins__": {}}, env)
        if isinstance(result, (int, float)):
            return result
        return f"[非数值结果]"
    except Exception as e:
        return f"[计算失败: {e}]"


def extract_project_preset_refs(code):
    """从代码中提取对 project_preset.<key> 的引用（AST 解析）。

    返回:
        set[str]: 被引用的预设 key 集合（如 {"install_address"}）
    """
    refs = set()
    if not code or not isinstance(code, str):
        return refs
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return refs
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "project_preset":
            refs.add(node.attr)
    return refs


def validate_global_code_refs(code, shared=None, own=None):
    """校验 code 类型全项目通用(global)预设的引用。

    语义澄清：global code 预设的「代码」对所有项目共享，但代码里引用
    project_preset.<key> 时，其值会随各项目的字段值变化——这是**合理且
    常见**的用法（如「项目省份 = install_address 取省」）。因此 global code
    允许引用 shared/project 字段，不做拦截。

    仅保留一项校验：代码不能引用「仅存在于某几个项目」的 project 独有字段
    时给出提示（避免其它项目缺失该字段导致渲染不一致），但不强制拦截——
    这里放宽为提示性返回 None，交给渲染时自然处理。

    返回:
        恒为 None（保留函数签名供调用方兼容）。
    """
    return None


def get_project_presets_context(project_id, project_dict=None, env=None):
    """获取项目预设的渲染上下文 dict，供报告模板使用。

    参数:
        project_id: 项目 ID
        project_dict: 项目字段字典（computed 类型需要）
        env: 代码(code)类型预设的执行环境（报告 context 等），可引用
             report.end_date、dew_point 等变量；缺省时用 project_dict 兜底。

    返回:
        dict: {"install_address": "广州市花都区...", "pre_estimated_10yr": 3023.9, ...}

    text → 纯字符串
    richtext → strip HTML
    image → 返回文件路径（在 DocxReportBuilder 阶段转为 InlineImage）
    computed → 计算表达式值
    code → 执行 Python 代码（函数体语义）返回的值转字符串；
            代码中可通过 project_preset.<key> 引用其它预设（按 sort_order 顺序求值）
    """
    presets = load_all_project_presets(project_id)

    if project_dict is None:
        project_dict = {}
    if env is None:
        env = dict(project_dict)
        # 注入 hook_params 裸变量
        try:
            raw_params = project_dict.get("hook_params", "{}")
            if isinstance(raw_params, str):
                raw_params = json.loads(raw_params)
            if isinstance(raw_params, dict):
                for k, v in raw_params.items():
                    if k not in env:
                        env[k] = v
        except Exception:
            pass

    result = {}
    code_items = []  # 延后求值的 code 类型，保证引用的字段先就绪

    for p in presets:
        key = p.get("key", "")
        ptype = p.get("type", "text")
        value = p.get("value", "")

        if not key:
            continue

        if ptype == "code":
            code_items.append((key, value))
        elif ptype == "computed":
            result[key] = _eval_computed(value, project_dict)
        elif ptype == "richtext":
            result[key] = _strip_html(value)
        elif ptype == "image":
            result[key] = ""  # 图片由 DocxReportBuilder 通过 InlineImage 注入，此处留空
        elif ptype == "table":
            # 渲染阶段返回占位符，报告生成后由 DocxReportBuilder 注入真实表格
            result[key] = f"[TABLE:{key}]"
        else:
            result[key] = value if value else ""

    # 第二轮：求值 code 类型（此时所有非 code 字段已就绪，可安全引用）
    from core.code_executor import run_to_string
    for key, value in code_items:
        local_env = dict(env or {})
        local_env["project_preset"] = result
        result[key] = run_to_string(value, local_env)

    return result


# 十年逐年预估表的三种呈现形式（对应 PDD 表 B.2 / B.3 / B.4）
#   baseline → 表 B.2 只列基准线排放
#   project  → 表 B.3 只列项目排放
#   full     → 表 B.4 基准线 / 项目 / 泄漏 / 减排量，带合计与计入期年数
_PROJECTION_TABLES = {
    "projection_10yr": "full",
    "projection_10yr_baseline": "baseline",
    "projection_10yr_project": "project",
}


def _cn_date(d):
    """2022-05-13 → 2022年05月13日（已格式化时原样返回）。"""
    m = re.fullmatch(r'(\d{4})-(\d{1,2})-(\d{1,2})', str(d or ''))
    if not m:
        return str(d or '')
    # 月日不补零：2022年5月13日（与备案案例一致，中文也不兴补零）
    return f"{int(m.group(1)):04d}年{int(m.group(2))}月{int(m.group(3))}日"


def _projection_year_label(row):
    """年份列取值：优先「起~止」日期区间（与备案案例一致），缺日期时退回「第N年」。"""
    start = _cn_date(row.get("start", ""))
    end = _cn_date(row.get("end", ""))
    if start and end:
        return f"{start}~{end}"
    return f"第{row.get('year', '')}年"


def _machine_num(row, idx, key):
    """取某行第 idx 台机器的某个值（缺明细时给 0，不炸）。"""
    ms = row.get("machines")
    if isinstance(ms, list) and idx < len(ms) and isinstance(ms[idx], dict):
        try:
            return float(ms[idx].get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _projection_machine_columns(proj_rows):
    """多机器时把每行的 machines 明细转成「一台一列」的列数据。

    返回 [{"name", "baseline": [...], "project": [...], "reduction": [...]}]。
    机器数 < 2（含老数据没有 machines 明细的情况）返回 [] —— 走原来的单列表头，
    保证单机器项目的表格与改造前逐格一致。
    """
    if not proj_rows:
        return []
    first = proj_rows[0].get("machines")
    if not isinstance(first, list) or len(first) < 2:
        return []
    cols = []
    for idx, m0 in enumerate(first):
        cols.append({
            "name": (m0.get("name") if isinstance(m0, dict) else None) or f"{idx + 1}号机器",
            "baseline": [_machine_num(r, idx, "baseline_emission") for r in proj_rows],
            "project": [_machine_num(r, idx, "project_emission") for r in proj_rows],
            "reduction": [_machine_num(r, idx, "reduction") for r in proj_rows],
        })
    return cols


def _build_multi_machine_table(proj_rows, cols, mode, period_years, labels):
    """多机器表：`年份 | 1号机器 | 2号机器 | … | 合计`。

    日期分段沿用单机器的口径（首年段/整年/末年段），只是把「一列指标」
    换成「每台机器一列 + 合计列」。按 mode 取哪一路指标：
      baseline → 基准线排放   project → 项目排放   full → 减排量（泄漏单列，恒 0）
    """
    names = [c["name"] for c in cols]
    is_full = (mode == "full")
    if mode == "baseline":
        series = [c["baseline"] for c in cols]
    elif mode == "project":
        series = [c["project"] for c in cols]
    else:
        series = [c["reduction"] for c in cols]

    headers = ["年份"] + names
    if is_full:
        headers.append("泄漏(tCO₂e)")
    headers.append("合计")

    rows = []
    for i, lb in enumerate(labels):
        vals = [s[i] for s in series]
        line = [lb] + [f"{v:.2f}" for v in vals]
        if is_full:
            line.append("0.00")
        line.append(f"{sum(vals):.2f}")   # 合计列 = 该年各机器之和
        rows.append(line)

    summary = []
    if proj_rows:
        py = period_years or len(proj_rows)
        totals = [sum(s) for s in series]
        grand = sum(totals)
        # 加粗规则与单机器一致：B.2/B.3 的合计加粗，B.4 的三行不加粗
        summary.append([len(rows), not is_full])
        rows.append(["合计"] + [f"{t:.2f}" for t in totals]
                    + (["0.00"] if is_full else []) + [f"{grand:.2f}"])
        summary.append([len(rows), False])
        rows.append(["计入期年数"] + [str(py)] * (len(names) + (2 if is_full else 1)))
        summary.append([len(rows), False])
        # 各列年均值 = 该列合计 ÷ 计入期年数（与单机器「合计÷年数」同一口径）
        rows.append(["计入期内年均值"] + [f"{t / py:.2f}" for t in totals]
                    + (["0.00"] if is_full else []) + [f"{grand / py:.2f}"])
    return headers, rows, summary


def _build_projection_table(project_dict, mode: str):
    """按 mode 生成十年预估表 (headers, rows)。

    项目尚不具备逐年预估条件（缺平均流量 / 基准线因子）时返回表头 + 空行，
    让报告里保留空表而不是整段消失——方便一眼看出哪张表缺数据。

    用 get_projection 而非 compute_projection：阶段一提交时会固化
    carbon_projects.projection_10yr，固化的逐年值与「十年预估减排总量」
    一起锁死，保证后续 PDD/核查报告与当初申报的数一致。
    """
    from core.projection import get_projection

    proj_rows = get_projection(project_dict) or []

    def _num(r, key):
        try:
            return float(r.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    labels = [_projection_year_label(r) for r in proj_rows]
    baseline = [_num(r, "baseline_emission") for r in proj_rows]
    project = [_num(r, "project_emission") for r in proj_rows]
    reduction = [_num(r, "reduction") for r in proj_rows]

    # 计入期年数 ≠ 行数：首末是部分年，10 年计入期会切出 11 行。
    # 优先用 yearly_projection 写进来的 period_years；老数据没有就按起止年份差推。
    period_years = 0
    if proj_rows:
        try:
            period_years = int(proj_rows[0].get("period_years") or 0)
        except (TypeError, ValueError):
            period_years = 0
        if not period_years:
            try:
                y0 = int(str(proj_rows[0].get("start", ""))[:4])
                y1 = int(str(proj_rows[-1].get("end", ""))[:4])
                period_years = max(y1 - y0, len(proj_rows))
            except (TypeError, ValueError):
                period_years = len(proj_rows)

    # ── 多机器（≥2 台）：表头换成「年份 | 1号机器 | … | 合计」──
    # 模板不用动：三个表还是同一个预设 key，由这里按机器数决定表头。
    machine_cols = _projection_machine_columns(proj_rows)
    if machine_cols:
        return _build_multi_machine_table(proj_rows, machine_cols, mode, period_years, labels)

    if mode == "baseline":
        headers = ["年份", "基准线排放(tCO₂e)"]
        rows = [[lb, f"{b:.2f}"] for lb, b in zip(labels, baseline)]
        summary = []
        if proj_rows:
            summary.append([len(rows), True])   # 案例里表 B.2 的合计是加粗的
            rows.append(["合计", f"{sum(baseline):.2f}"])
        return headers, rows, summary

    if mode == "project":
        headers = ["年份", "项目排放(tCO₂e)"]
        rows = [[lb, f"{p:.2f}"] for lb, p in zip(labels, project)]
        summary = []
        if proj_rows:
            summary.append([len(rows), True])   # 案例里表 B.3 的合计是加粗的
            rows.append(["合计", f"{sum(project):.2f}"])
        return headers, rows, summary

    # full = 表 B.4（对齐备案案例：11 个年份行 + 合计 / 计入期年数 / 计入期内年均值）
    headers = ["年份", "基准线排放(tCO₂e)", "项目排放(tCO₂e)", "泄漏(tCO₂e)", "减排量(tCO₂e)"]
    rows = [
        [lb, f"{b:.2f}", f"{p:.2f}", "0.00", f"{rd:.2f}"]
        for lb, b, p, rd in zip(labels, baseline, project, reduction)
    ]
    summary = []
    if proj_rows:
        tb, tp, tr = sum(baseline), sum(project), sum(reduction)
        py = period_years or len(proj_rows)
        # 案例里表 B.4 的这三行都不加粗（只有底纹），与 B.2/B.3 的合计不同
        summary.append([len(rows), False])
        rows.append(["合计", f"{tb:.2f}", f"{tp:.2f}", "0.00", f"{tr:.2f}"])
        # 本方法学不涉及泄漏，四列计入期年数相同
        summary.append([len(rows), False])
        rows.append(["计入期年数", str(py), str(py), str(py), str(py)])
        summary.append([len(rows), False])
        rows.append(["计入期内年均值", f"{tb / py:.2f}", f"{tp / py:.2f}", "0.00", f"{tr / py:.2f}"])
    return headers, rows, summary


def _build_table_data(value, project_dict=None):
    """把 table 类型预设的 value 解析成 (headers, rows, summary_rows)。

    value 为 JSON 字符串：
      - 静态：{"headers": [...], "rows": [[...], ...]}
      - 动态：{"generator": "projection_10yr"} → 按当前项目调 compute_projection 得到十年预估，
              另支持 projection_10yr_baseline（表 B.2）、projection_10yr_project（表 B.3）
    summary_rows 是「汇总行」的行下标（合计 / 计入期年数 / 计入期内年均值），
    渲染时会加底纹、加粗，和备案案例一致；静态表格为空。
    无有效表头时返回 (None, None, None)。
    """
    headers = []
    rows = []
    summary = []
    try:
        data = json.loads(value) if isinstance(value, str) else (value or {})
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    mode = _PROJECTION_TABLES.get(data.get("generator"))
    if mode:
        try:
            if project_dict:
                headers, rows, summary = _build_projection_table(project_dict, mode)
        except Exception:
            headers, rows, summary = [], [], []
    else:
        headers = [str(h) for h in data.get("headers", [])]
        for r in data.get("rows", []):
            rows.append([str(c) for c in r] if isinstance(r, list) else [])

    if not headers:
        return None, None, None
    return headers, rows, summary


def get_project_preset_inline_images(project_id):
    """获取 image 类型项目预设的绝对路径映射（供 DocxReportBuilder 渲染用）。

    返回:
        dict: {"site_photo": "E:/.../data/project_images/xxx.png", ...}
    """
    presets = load_all_project_presets(project_id)
    result = {}
    for p in presets:
        if p.get("type") == "image" and p.get("value"):
            # 复用企业预设的路径解析逻辑
            from core.presets import get_image_absolute_path
            abs_path = get_image_absolute_path(p["value"])
            if abs_path and os.path.exists(abs_path):
                result[p["key"]] = abs_path
    return result
