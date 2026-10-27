"""
企业预设信息管理。

在 enterprise_config 表中存储企业自定义的模板变量（预设），
支持 text / richtext / image 三种类型。生成报告时自动注入为
{{ preset.<key> }} 上下文变量。

用法:
    from core.presets import (load_presets, save_presets,
                              add_preset, update_preset, delete_preset,
                              get_presets_context, migrate_db)

    # 确保 DB 有 presets 列
    migrate_db()

    # 加载所有预设
    presets = load_presets()

    # 添加
    add_preset("summary", "企业简介", "richtext", "<p>...</p>", "企业信息")

    # 获取渲染上下文
    ctx = get_presets_context()  # → {"summary": "纯文本...", "logo": InlineImage(...)}

图片目录: data/preset_images/
"""

import os
import re
import json
import time
from datetime import datetime

# 项目根目录
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 预设图片存储目录
import config as _config
_IMAGES_DIR = _config.data_path("preset_images")
# 企业预设外载包目录（每个 .py 定义一个企业专属预设包，删文件即恢复通用版）
_PACKS_DIR = _config.PRESET_PACKS_DIR

# 默认预设模板：key, label, type, value, category, sort_order
#
# 只放「企业级、且确实会变」的字段。判断标准：
#   - 一个公司几年才改一次（法人代表、统一社会信用代码、选用方法学）→ 直接写死在模板里，不做预设
#   - 会随年份/政策变（电网碳排放因子及其年份）→ 必须留成预设，方便改
#   - 随项目变（审定机构、审定联系人）→ 放项目预设，不放这里
_DEFAULT_PRESETS = [
    {"key": "summary",           "label": "企业简介",       "type": "richtext", "value": "", "category": "企业信息", "sort_order": 1},
    {"key": "verification_org",  "label": "核查机构名称",    "type": "text",     "value": "", "category": "核查信息", "sort_order": 10},
    {"key": "contact_person",    "label": "联系人",         "type": "text",     "value": "", "category": "联系方式", "sort_order": 20},
    {"key": "contact_phone",     "label": "联系电话",       "type": "text",     "value": "", "category": "联系方式", "sort_order": 21},
    {"key": "contact_address",   "label": "联系地址",       "type": "text",     "value": "", "category": "联系方式", "sort_order": 22},
    {"key": "contact_email",     "label": "联系人邮箱",     "type": "text",     "value": "", "category": "联系方式", "sort_order": 23},
    {"key": "contact_fax",       "label": "联系人传真",     "type": "text",     "value": "", "category": "联系方式", "sort_order": 24},
]

# 预设分类
CATEGORIES = ["企业信息", "核查信息", "联系方式", "自定义"]


def migrate_db():
    """确保 enterprise_config 表有 presets 列（兼容旧数据库）。

    在 init_database() 之后调用，会在缺少列时自动 ALTER TABLE。
    首次运行时同时写入默认预设模板。
    """
    try:
        from core.project_manager import get_db
        conn = get_db()
        try:
            # 检查 presets 列是否存在
            cols = conn.execute("PRAGMA table_info('enterprise_config')").fetchall()
            col_names = [c[1] for c in cols]

            if 'presets' not in col_names:
                conn.execute("ALTER TABLE enterprise_config ADD COLUMN presets TEXT DEFAULT '[]'")
                conn.commit()
                print("[migrate] enterprise_config 表已添加 presets 列")

            # 检查是否有预设数据，没有则初始化默认模板
            row = conn.execute("SELECT presets FROM enterprise_config WHERE id = 1").fetchone()
            if row:
                presets_str = row[0] or '[]'
                try:
                    presets = json.loads(presets_str)
                except Exception:
                    presets = []
                if not presets:
                    # 写入默认预设模板
                    conn.execute(
                        "UPDATE enterprise_config SET presets = ?, updated_at = ? WHERE id = 1",
                        (json.dumps(_DEFAULT_PRESETS, ensure_ascii=False),
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    )
                    conn.commit()
                    print("[migrate] 已初始化默认预设模板")
        finally:
            conn.close()
    except Exception as e:
        print(f"[migrate] 迁移失败（可忽略，首次运行可能正常）: {e}")

    ensure_required_presets()


# 报告模板依赖的预设：老库升级时补齐，避免模板里出现填不了的占位符。
# 只补不下发默认值的「结构字段」（value 为空），不动用户已填的值。
#
# ⚠️ 这里的 key 必须仍在 _DEFAULT_PRESETS 里，否则补的时候拿不到定义会被跳过。
# 删预设时务必同步删这里的条目，否则下次启动会把删掉的字段加回来。
_REQUIRED_PRESETS = [
    "contact_email", "contact_fax",
]


def ensure_required_presets():
    """补齐报告模板用到、但老库里还没有的预设字段（按 key 幂等）。

    用户已填过值的字段原样保留；只在字段整个不存在时按 _DEFAULT_PRESETS
    里的定义追加。在预设管理里删掉的字段下次启动会被补回来——它们对应
    审定备案申请书等模板的必填位，缺了模板就渲染不出来。
    """
    try:
        presets = load_presets()
        existing = {p.get("key") for p in presets}
        defaults = {p["key"]: p for p in _DEFAULT_PRESETS}
        added = []
        for key in _REQUIRED_PRESETS:
            if key in existing:
                continue
            tpl = defaults.get(key)
            if not tpl:
                continue
            presets.append(dict(tpl))
            added.append(key)
        if added:
            presets.sort(key=lambda p: p.get("sort_order", 99))
            save_presets(presets)
            print(f"[migrate] 已补齐预设字段: {'、'.join(added)}")
    except Exception as e:
        print(f"[migrate] 补齐预设字段失败（可忽略）: {e}")


def _ensure_images_dir():
    """确保图片目录存在。"""
    if not os.path.isdir(_IMAGES_DIR):
        os.makedirs(_IMAGES_DIR, exist_ok=True)


def get_images_dir():
    """返回预设图片存储目录的绝对路径。"""
    _ensure_images_dir()
    return _IMAGES_DIR


# ── CRUD ──

def load_presets(include_packs=True):
    """从 enterprise_config 加载所有预设，并合并外载企业预设包。

    返回:
        list[dict]: 预设列表，按 sort_order 排序。
        外载包预设带 _pack 字段（包名），只读、不可编辑/删除。
    """
    presets = []
    try:
        from core.project_manager import get_db
        conn = get_db()
        try:
            row = conn.execute("SELECT presets FROM enterprise_config WHERE id = 1").fetchone()
            if row and row[0]:
                try:
                    data = json.loads(row[0])
                    if isinstance(data, list):
                        presets = data
                except json.JSONDecodeError:
                    pass
        finally:
            conn.close()
    except Exception:
        pass

    if include_packs:
        for pack in discover_preset_packs():
            for p in pack["presets"]:
                if not isinstance(p, dict) or not p.get("key"):
                    continue
                item = dict(p)
                item["_pack"] = pack["pack_name"]
                presets.append(item)

    presets.sort(key=lambda x: x.get("sort_order", 99))
    return presets


def discover_preset_packs():
    """扫描 presets_packs/ 目录，加载所有企业预设外载包。

    每个包是一个 .py 文件，模块内定义 PRESETS 列表
    （元素含 key/label/type/value/category/sort_order）。
    文件名（不含 .py）即包名；_ 或 . 开头的文件忽略。

    返回:
        list[dict]: [{"pack_name": "xinzan", "presets": [...]}, ...]
    """
    packs = []
    if not os.path.isdir(_PACKS_DIR):
        return packs
    import importlib
    import sys as _sys
    if _BASE_DIR not in _sys.path:
        _sys.path.insert(0, _BASE_DIR)
    for filename in sorted(os.listdir(_PACKS_DIR)):
        if not filename.endswith(".py"):
            continue
        if filename.startswith("_") or filename.startswith("."):
            continue
        module_name = filename[:-3]
        try:
            module = importlib.import_module(f"presets_packs.{module_name}")
            presets = getattr(module, "PRESETS", None)
            config = getattr(module, "ENTERPRISE_CONFIG", None) or {}
            if isinstance(presets, list) or isinstance(config, dict) and config:
                packs.append({
                    "pack_name": module_name,
                    "presets": presets if isinstance(presets, list) else [],
                    "config": config if isinstance(config, dict) else {},
                })
            else:
                print(f"[预设包] {module_name} 未定义 PRESETS 或 ENTERPRISE_CONFIG，已忽略")
        except Exception as e:
            print(f"[预设包] 加载 {module_name} 失败: {e}")
    return packs


def get_pack_enterprise_config():
    """合并所有企业预设外载包的企业级配置（如 ef_grid_override、report_logo_path 等）。

    返回:
        dict: 企业配置字段 → 值（来自外载包，只读覆盖）。
    """
    config = {}
    for pack in discover_preset_packs():
        c = pack.get("config")
        if isinstance(c, dict):
            for k, v in c.items():
                if k.startswith("_"):
                    continue
                config[k] = v
    return config


def save_presets(presets):
    """保存预设列表到 enterprise_config。

    参数:
        presets: list[dict]
    """
    try:
        from core.project_manager import get_db
        conn = get_db()
        try:
            conn.execute(
                "UPDATE enterprise_config SET presets = ?, updated_at = ? WHERE id = 1",
                (json.dumps(presets, ensure_ascii=False),
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        raise RuntimeError(f"保存预设失败: {e}")


def add_preset(key, label, preset_type="text", value="", category="自定义", sort_order=None):
    """添加一个新的预设。

    返回:
        dict: {"status": "成功", "preset": {...}} 或 {"status": "失败", "msg": "..."}
    """
    if not key or not label:
        return {"status": "失败", "msg": "预设 key 和标签不能为空"}

    # 校验 key 格式
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
        return {"status": "失败", "msg": "Key 只能包含英文字母、数字和下划线，且以字母或下划线开头"}

    if preset_type not in ("text", "richtext", "image", "code"):
        return {"status": "失败", "msg": f"不支持的预设类型: {preset_type}"}

    presets = load_presets()

    # 检查重复
    for p in presets:
        if p.get("key") == key:
            return {"status": "失败", "msg": f"预设 key '{key}' 已存在"}

    if sort_order is None:
        sort_order = max((p.get("sort_order", 0) for p in presets), default=0) + 1

    new_preset = {
        "key": key,
        "label": label.strip(),
        "type": preset_type,
        "value": value,
        "category": category if category in CATEGORIES else "自定义",
        "sort_order": sort_order,
    }
    presets.append(new_preset)
    save_presets(presets)

    return {"status": "成功", "preset": new_preset}


def update_preset(key, data):
    """更新指定 key 的预设。

    参数:
        key: 要更新的预设 key
        data: {"label": ..., "type": ..., "value": ..., "category": ..., "new_key": ...}

    返回:
        dict: {"status": "成功", "preset": {...}} 或 {"status": "失败", "msg": "..."}
    """
    presets = load_presets()

    idx = None
    for i, p in enumerate(presets):
        if p.get("key") == key:
            idx = i
            break

    if idx is None:
        return {"status": "失败", "msg": f"预设 '{key}' 不存在"}

    new_key = data.get("new_key", "").strip() if data.get("new_key") else None
    if new_key and new_key != key:
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', new_key):
            return {"status": "失败", "msg": "新 Key 格式不合法"}
        for p in presets:
            if p.get("key") == new_key:
                return {"status": "失败", "msg": f"新 Key '{new_key}' 已存在"}
        presets[idx]["key"] = new_key

    if "label" in data:
        presets[idx]["label"] = data["label"].strip()
    if "type" in data and data["type"] in ("text", "richtext", "image", "code"):
        presets[idx]["type"] = data["type"]
    if "value" in data:
        presets[idx]["value"] = data["value"] if data["value"] else ""
    if "category" in data:
        presets[idx]["category"] = data["category"] if data["category"] in CATEGORIES else "自定义"
    if "sort_order" in data and isinstance(data["sort_order"], (int, float)):
        presets[idx]["sort_order"] = int(data["sort_order"])

    save_presets(presets)
    return {"status": "成功", "preset": presets[idx]}


def delete_preset(key):
    """删除指定 key 的预设。

    返回:
        dict: {"status": "成功"} 或 {"status": "失败", "msg": "..."}
    """
    presets = load_presets()
    new_list = [p for p in presets if p.get("key") != key]
    if len(new_list) == len(presets):
        return {"status": "失败", "msg": f"预设 '{key}' 不存在"}
    save_presets(new_list)
    return {"status": "成功"}


# ── 图片管理 ──

def save_preset_image(file_data, filename, preset_key=None):
    """保存预设图片到 data/preset_images/。

    参数:
        file_data: bytes 或 file-like object
        filename: 原始文件名
        preset_key: 关联的预设 key（用于生成文件名）

    返回:
        str: 保存后的相对路径，如 "data/preset_images/preset_logo_1712345678.png"
    """
    _ensure_images_dir()

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'):
        ext = '.png'

    safe_name = f"preset_{preset_key or 'img'}_{int(time.time())}{ext}"
    filepath = os.path.join(_IMAGES_DIR, safe_name)

    if isinstance(file_data, bytes):
        with open(filepath, 'wb') as f:
            f.write(file_data)
    else:
        file_data.save(filepath)

    # 返回相对于项目根目录的路径（统一用正斜杠，前端按 / 切分取文件名）
    return "data/preset_images/" + safe_name


def get_image_absolute_path(relative_path):
    """将相对路径转为绝对路径。"""
    if not relative_path:
        return ""
    return _config.resolve_data_path(relative_path)


# ── 渲染上下文 ──

def _strip_html(html_str):
    """移除 HTML 标签，保留纯文本。"""
    if not html_str:
        return ""
    # 移除 <img> 的 alt 文本
    text = re.sub(r'<img[^>]+alt="([^"]*)"[^>]*>', r'\1', html_str)
    # 移除所有标签
    text = re.sub(r'<[^>]+>', '', text)
    # 处理常见 HTML 实体
    text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&amp;', '&').replace('&quot;', '"')
    # 压缩多余空白
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = text.strip()
    return text


def get_presets_context(doc=None, env=None):
    """获取所有预设的渲染上下文 dict。

    参数:
        doc: DocxTemplate 实例（可选）。传入后 image 类型的预设会
             转为 InlineImage 对象，否则 image 类型返回空字符串。
        env: 代码(code)类型预设的执行环境（报告 context 等）。可引用
             report.end_date、dew_point 等变量。

    返回:
        dict: {"summary": "纯文本内容", "verification_org": "xx中心", ...}

    注意：
        - text 类型 → 纯字符串
        - richtext 类型 → strip HTML 标签后的纯文本
        - image 类型 → docxtpl InlineImage 对象（需 doc 参数）或空字符串
        - code 类型 → 执行 Python 代码（函数体语义）返回的值转字符串
    """
    presets = load_presets()
    result = {}

    for p in presets:
        key = p.get("key", "")
        ptype = p.get("type", "text")
        value = p.get("value", "")

        if not key:
            continue

        if ptype == "code":
            from core.code_executor import run_to_string
            result[key] = run_to_string(value, env)
        elif ptype == "richtext":
            result[key] = _strip_html(value)
        elif ptype == "image":
            if doc and value:
                abs_path = get_image_absolute_path(value)
                if os.path.exists(abs_path):
                    try:
                        from docxtpl import InlineImage
                        from docx.shared import Mm
                        result[key] = InlineImage(doc, abs_path)
                    except Exception:
                        result[key] = ""
                else:
                    result[key] = ""
            else:
                result[key] = ""
        else:
            # text 类型
            result[key] = value

    return result
