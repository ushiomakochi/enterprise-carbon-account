"""
报告生成子系统 — 基于 docxtpl 的 Word 报告自动生成。

架构:
- DocxReportBuilder: 通用 docxtpl 渲染器，可用于任意模板
- ReportContextBuilder: 构建报告所需的完整数据上下文
- discover_templates(): 自动扫描模板目录，发现可用报告类型
- discover_enterprises(): 列出有专属模板的企业

企业专属模板:
    每个企业可以在 templates/reports/docx/<企业名>/ 下放置自己的模板。
    企业模板优先于通用模板使用。企业模板中可直接写入固定文本
    （如公司简介、项目描述），只需将动态数据（日期、数字）用
    {{ }} 占位符标记。

用法:
    from core.reports import discover_templates, DocxReportBuilder, ReportContextBuilder

    # 查看可用模板（含企业专属）
    formats = discover_templates(enterprise_name="<企业名>")
    # → 返回企业专属模板，不存在则回退通用模板

    # 获取模板路径（企业优先）
    path = get_template_path("verification", enterprise_name="<企业名>")

    # 生成报告
    context = ReportContextBuilder.build(project, "verification", start_date, end_date)
    DocxReportBuilder.render(template_path, context, output_path)
"""

import os
import json

# 模板目录（相对于项目根目录）
import config as _config
_TEMPLATE_DIR = _config.REPORT_DOCX_TEMPLATE_DIR

# 各模板显示名映射文件
_LABELS_FILE = os.path.join(_TEMPLATE_DIR, "_labels.json")


def get_template_dir():
    """返回 docx 模板目录绝对路径"""
    return _TEMPLATE_DIR


def _scan_dir_for_docx(directory: str, labels: dict) -> list:
    """扫描指定目录中的 .docx 模板文件。

    参数:
        directory: 要扫描的目录路径
        labels: 标签映射 {"key": "显示名"}

    返回:
        list[dict]: 模板信息列表
    """
    templates = []
    if not os.path.isdir(directory):
        return templates
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".docx"):
            continue
        if filename.startswith("_") or filename.startswith("~"):
            continue
        key = filename[:-5]  # 去掉 .docx
        template_path = os.path.join(directory, filename)
        templates.append({
            "key": key,
            "label": labels.get(key, key),
            "file": filename,
            "template_path": template_path,
        })
    return templates


def discover_templates(enterprise_name: str = None):
    """
    自动扫描 templates/reports/docx/ 目录，发现所有可用的 .docx 模板。
    如果提供 enterprise_name，还会扫描 <enterprise_name>/ 子目录，
    子目录中的模板覆盖同名根目录模板（企业模板优先）。

    命名约定:
    - 文件名（不含扩展名）即为报告类型 key，如 verification.docx → "verification"
    - _labels.json 提供中文显示名映射
    - 企业专属模板放在 docx/<企业名>/ 子目录下
    - 未在 _labels.json 中定义的模板，使用文件名作为显示名

    参数:
        enterprise_name: 可选，企业名称。传入后会同时扫描企业子目录。

    返回:
        list[dict]: [{"key": "verification", "label": "核查报告", "file": "verification.docx",
                       "template_path": "...", "enterprise": false}, ...]
    """
    # 加载标签映射
    labels = {}
    if os.path.exists(_LABELS_FILE):
        try:
            with open(_LABELS_FILE, "r", encoding="utf-8") as f:
                labels = json.load(f)
        except Exception:
            pass

    # 确保目录存在
    if not os.path.isdir(_TEMPLATE_DIR):
        os.makedirs(_TEMPLATE_DIR, exist_ok=True)
        return []

    # 1. 扫描根目录（通用模板）
    generic = _scan_dir_for_docx(_TEMPLATE_DIR, labels)
    for t in generic:
        t["enterprise"] = False

    # 2. 如果指定了企业名称，扫描企业子目录
    enterprise_templates = {}
    if enterprise_name:
        enterprise_dir = os.path.join(_TEMPLATE_DIR, enterprise_name)
        if os.path.isdir(enterprise_dir):
            ent_list = _scan_dir_for_docx(enterprise_dir, labels)
            for t in ent_list:
                t["enterprise"] = True
                t["label"] = t["label"] + f"（{enterprise_name}）"
                enterprise_templates[t["key"]] = t

    # 3. 合并：企业模板覆盖同名通用模板
    if enterprise_templates:
        merged = []
        seen_keys = set()
        # 先放企业模板
        for key, tmpl in enterprise_templates.items():
            merged.append(tmpl)
            seen_keys.add(key)
        # 再放未覆盖的通用模板
        for tmpl in generic:
            if tmpl["key"] not in seen_keys:
                merged.append(tmpl)
        return merged

    return generic


def discover_enterprises():
    """
    列出 templates/reports/docx/ 下所有有专属模板的企业名称。

    返回:
        list[str]: 企业名称列表（子目录名）
    """
    enterprises = []
    if not os.path.isdir(_TEMPLATE_DIR):
        return enterprises
    for name in sorted(os.listdir(_TEMPLATE_DIR)):
        subdir = os.path.join(_TEMPLATE_DIR, name)
        if not os.path.isdir(subdir):
            continue
        if name.startswith("_") or name.startswith("~"):
            continue
        # 检查是否有 .docx 文件
        has_docx = any(
            f.endswith(".docx") and not f.startswith("_") and not f.startswith("~")
            for f in os.listdir(subdir)
        )
        if has_docx:
            enterprises.append(name)
    return enterprises


def get_template_path(report_type: str, enterprise_name: str = None):
    """
    根据报告类型 key 获取模板文件路径。企业模板优先。

    参数:
        report_type: 报告类型 key，如 "verification"、"pdd"
        enterprise_name: 可选，企业名称。传入后优先使用企业专属模板。

    返回:
        str | None: 模板文件的绝对路径，不存在则返回 None
    """
    # 1. 优先：企业专属模板
    if enterprise_name:
        ent_path = os.path.join(_TEMPLATE_DIR, enterprise_name, f"{report_type}.docx")
        if os.path.exists(ent_path):
            return ent_path

    # 2. 根目录精确匹配
    path = os.path.join(_TEMPLATE_DIR, f"{report_type}.docx")
    if os.path.exists(path):
        return path

    # 3. 扫描根目录查找匹配
    for tmpl in discover_templates():
        if tmpl["key"] == report_type:
            return tmpl["template_path"]

    return None


def get_report_type_label(report_type: str) -> str:
    """获取报告类型的中文显示名。

    先查 _labels.json——模板只放在企业子目录（如 某企业/approval.docx）时
    discover_templates() 扫不到根目录，会拿不到中文名。
    """
    labels = _load_labels()
    if labels.get(report_type):
        return labels[report_type]
    for tmpl in discover_templates():
        if tmpl["key"] == report_type:
            return tmpl["label"]
    return report_type


def _load_labels() -> dict:
    """加载模板显示名映射 _labels.json"""
    labels = {}
    if os.path.exists(_LABELS_FILE):
        try:
            with open(_LABELS_FILE, "r", encoding="utf-8") as f:
                labels = json.load(f)
        except Exception:
            pass
    return labels


# 固定的报告类型 key 集合。
# ⚠️ 必须与 get_report_types() 返回的 key 完全一致：list_templates() 靠它判断
# 「这个文件明确属于别的类型，不该出现在当前类型下」。漏一个，该类模板就会
# 混进所有类型的模板下拉框（加 approval 时漏过一次）。
REPORT_TYPE_KEYS = ("verification", "pdd", "approval")


def get_report_types() -> list:
    """返回固定的报告类型（含中文显示名）。

    报告类型是固定的三类：核查报告 / 项目设计文档（PDD）/ 审定报告，
    不随模板文件夹变化，也不再区分「通用 / 企业专属」。
    """
    labels = _load_labels()
    return [
        {"key": "verification", "label": labels.get("verification", "核查报告")},
        {"key": "pdd", "label": labels.get("pdd", "项目设计文档（PDD）")},
        {"key": "approval", "label": labels.get("approval", "审定报告")},
    ]


def _filename_matches_type(filename: str, report_type: str) -> bool:
    """判断模板文件名是否属于指定报告类型。

    匹配规则：文件名（不含 .docx 扩展名）等于类型 key，或以「类型_」/「类型-」
    开头、或以「_类型」/「-类型」结尾。支持一家企业放多个同类型模板（如按
    审定机构分：approval_质高.docx / 德凯_approval.docx），否则它们会被当成
    「通用模板」混进所有报告类型的下拉框。

    例：verification.docx / verification_标准版.docx / verification-v2.docx
        approval_质高.docx / 德凯-approval.docx
    """
    if not filename.endswith(".docx"):
        return False
    base = filename[:-5]
    return (
        base == report_type
        or base.startswith(report_type + "_")
        or base.startswith(report_type + "-")
        or base.endswith("_" + report_type)
        or base.endswith("-" + report_type)
    )


def list_templates(report_type: str = None) -> list:
    """列出模板目录中所有 .docx 模板文件（递归扫描根目录及子目录）。

    参数:
        report_type: 可选，仅返回匹配该类型的模板。

    返回:
        list[dict]: [{"file": "verification.docx", "label": "verification.docx",
                       "template_path": "...", "rel_path": "verification.docx"}, ...]

    label 使用文件相对模板目录的路径（如 "verification.docx" / "<企业名>/verification.docx"），
    便于区分同名模板。
    """
    if not os.path.isdir(_TEMPLATE_DIR):
        return []

    templates = []
    for root, _dirs, files in os.walk(_TEMPLATE_DIR):
        for filename in sorted(files):
            if not filename.endswith(".docx"):
                continue
            if filename.startswith("_") or filename.startswith("~"):
                continue
            if report_type and not _filename_matches_type(filename, report_type):
                # 明确属于其他报告类型的文件才过滤；
                # 不匹配任何已知类型的文件名视为「通用模板」，各类型下都可选
                if any(_filename_matches_type(filename, k) for k in REPORT_TYPE_KEYS):
                    continue
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, _TEMPLATE_DIR).replace("\\", "/")
            templates.append({
                "file": filename,
                "label": rel_path,
                "rel_path": rel_path,
                "template_path": full_path,
            })
    return templates


def resolve_template_file(template_ref: str) -> str:
    """根据模板引用（文件名或相对路径）解析为模板绝对路径。

    仅允许模板目录内的 .docx 文件，防止路径穿越。

    参数:
        template_ref: 文件名（如 "verification.docx"）或相对路径（如 "<企业名>/verification.docx"）

    返回:
        str | None: 解析成功返回绝对路径，否则 None
    """
    if not template_ref:
        return None
    # 规范化并去除可能的路径穿越
    safe = template_ref.replace("\\", "/").strip()
    if safe.startswith("/") or ".." in safe.split("/"):
        return None
    full_path = os.path.join(_TEMPLATE_DIR, safe)
    # 确保解析后的路径仍在模板目录内
    if not os.path.abspath(full_path).startswith(os.path.abspath(_TEMPLATE_DIR)):
        return None
    if not full_path.endswith(".docx") or not os.path.isfile(full_path):
        return None
    return full_path


from .base_builder import DocxReportBuilder
from .context_builder import ReportContextBuilder

__all__ = [
    "DocxReportBuilder",
    "ReportContextBuilder",
    "discover_templates",
    "discover_enterprises",
    "get_template_path",
    "get_template_dir",
    "get_report_type_label",
    "get_report_types",
    "list_templates",
    "resolve_template_file",
]
