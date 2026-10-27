"""
全局自定义占位符管理。

在 templates/reports/docx/_custom_placeholders.json 中存储用户自定义的
Jinja2 模板变量。这些变量会通过 ReportContextBuilder 注入到报告渲染上下文中，
模板可通过 {{ custom.<name> }} 或简写的 {{ <name> }} 引用。

用法:
    from core.custom_placeholders import (
        load_custom_placeholders, add_custom_placeholder,
        update_custom_placeholder, delete_custom_placeholder,
        get_custom_placeholders_context
    )

    # 列出所有
    placeholders = load_custom_placeholders()

    # 添加
    result = add_custom_placeholder("reviewer", "核查人", "张三", "签署人")

    # 获取上下文 dict（注入渲染用）
    ctx = get_custom_placeholders_context()
"""

import os
import json
import re

from . import json_store as _json_store

# 存储路径：与模板放在同一目录
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import config as _config
_STORAGE_PATH = os.path.join(_config.REPORT_DOCX_TEMPLATE_DIR, "_custom_placeholders.json")

# 合法的 Jinja2 变量名：字母/下划线开头，后续可含数字
_VALID_NAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

# 与 ReportContextBuilder 顶层 key 冲突的名称（保留字）
_RESERVED_NAMES = {
    "report", "enterprise", "project", "carbon_assets", "stages",
    "monitoring", "formula", "emission_sources", "declarations",
    "pdd", "hook_params", "custom",
}


def _ensure_file():
    """确保 JSON 存储文件存在，不存在则创建空数组。"""
    parent = os.path.dirname(_STORAGE_PATH)
    if not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    if not os.path.exists(_STORAGE_PATH):
        with open(_STORAGE_PATH, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)


def load_custom_placeholders():
    """加载所有自定义占位符。

    返回:
        list[dict]: 每个元素 {"name", "label", "value", "description"}

    ⚠️ 读失败会重试后抛异常，**不再静默返回空列表** —— 调用方（增/改/删）都是
    读-改-写，拿到空列表写回去会把所有自定义占位符清空。
    """
    _ensure_file()
    return _json_store.read_json_list(_STORAGE_PATH, "自定义占位符")


def save_custom_placeholders(placeholders):
    """保存自定义占位符列表到文件（原子写，见 core/json_store.py）。"""
    _json_store.write_json_atomic(_STORAGE_PATH, placeholders)


def _validate_name(name, existing_list=None, skip_name=None):
    """校验占位符名称。返回 (valid: bool, error_msg: str)。"""
    if not name:
        return False, "名称不能为空"
    if not _VALID_NAME_RE.match(name):
        return False, "名称只能包含英文字母、数字和下划线，且以字母或下划线开头"
    if name.lower() in _RESERVED_NAMES:
        return False, f"名称 '{name}' 是系统保留字，请换一个"
    if existing_list is not None:
        for ph in existing_list:
            if ph.get("name", "").lower() == name.lower():
                if skip_name is None or ph.get("name", "").lower() != skip_name.lower():
                    return False, f"名称 '{name}' 已存在"
    return True, ""


def add_custom_placeholder(name, label="", value="", description=""):
    """添加一个自定义占位符。

    返回:
        dict: {"status": "成功", "placeholder": {...}}
              或 {"status": "失败", "msg": "错误原因"}
    """
    placeholders = load_custom_placeholders()

    valid, err = _validate_name(name, placeholders)
    if not valid:
        return {"status": "失败", "msg": err}

    if not label:
        return {"status": "失败", "msg": "显示标签不能为空"}

    new_ph = {
        "name": name,
        "label": label.strip(),
        "value": value.strip() if value else "",
        "description": description.strip() if description else "",
    }
    placeholders.append(new_ph)
    save_custom_placeholders(placeholders)

    return {"status": "成功", "placeholder": new_ph}


def update_custom_placeholder(name, data):
    """更新指定名称的自定义占位符。

    参数:
        name: 要更新的占位符名称
        data: {"label": ..., "value": ..., "description": ..., "new_name": ...}

    返回:
        dict: {"status": "成功", "placeholder": {...}}
              或 {"status": "失败", "msg": "错误原因"}
    """
    placeholders = load_custom_placeholders()

    # 查找目标
    idx = None
    for i, ph in enumerate(placeholders):
        if ph.get("name", "") == name:
            idx = i
            break

    if idx is None:
        return {"status": "失败", "msg": f"占位符 '{name}' 不存在"}

    new_name = data.get("new_name", "").strip() if data.get("new_name") else None

    # 如果改名，校验新名称
    if new_name and new_name != name:
        valid, err = _validate_name(new_name, placeholders, skip_name=name)
        if not valid:
            return {"status": "失败", "msg": err}
        placeholders[idx]["name"] = new_name

    if "label" in data:
        placeholders[idx]["label"] = data["label"].strip()
    if "value" in data:
        placeholders[idx]["value"] = str(data["value"]).strip() if data["value"] else ""
    if "description" in data:
        placeholders[idx]["description"] = data["description"].strip() if data["description"] else ""

    save_custom_placeholders(placeholders)

    return {"status": "成功", "placeholder": placeholders[idx]}


def delete_custom_placeholder(name):
    """删除指定名称的自定义占位符。

    返回:
        dict: {"status": "成功"} 或 {"status": "失败", "msg": "错误原因"}
    """
    placeholders = load_custom_placeholders()

    new_list = [ph for ph in placeholders if ph.get("name", "") != name]
    if len(new_list) == len(placeholders):
        return {"status": "失败", "msg": f"占位符 '{name}' 不存在"}

    save_custom_placeholders(new_list)
    return {"status": "成功"}


def get_custom_placeholders_context():
    """获取所有自定义占位符的上下文 dict，供报告渲染使用。

    同时注入两个层级：
    - custom.<name>: 命名空间访问，防止与系统变量冲突
    - <name>: 顶层简写，方便模板直接使用

    返回:
        dict: {"custom": {"name1": "value1", ...}, "name1": "value1", ...}
    """
    placeholders = load_custom_placeholders()
    custom_dict = {}
    for ph in placeholders:
        name = ph.get("name", "")
        value = ph.get("value", "")
        if name:
            custom_dict[name] = value

    # 同时注入两个层级
    result = {"custom": dict(custom_dict)}
    result.update(custom_dict)  # 顶层简写
    return result
