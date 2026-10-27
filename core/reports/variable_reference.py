"""
报告模板可用字段 — 动态生成。

根据选定项目，返回两类可供 Word 模板引用的字段：
1. 项目中自有字段：项目本身存储的数据（企业/项目/碳资产/公式参数/阶段）。
2. 自定义字段：在预设（企业预设 + 项目预设）中定义、且已填写值的字段。

同时提供 Jinja2 控制结构参考（{%tr for %}、{% if %} 等）。

用法:
    from core.reports.variable_reference import get_available_fields
    ref = get_available_fields(project_id)
    # → {"project_fields": [...], "custom_fields": [...], "controls": [...]}
"""

import json

# 表格预设的自动生成器 → 面板里显示的中文说明。
# 与 core/project_presets.py::_PROJECTION_TABLES 及前端表格编辑器的选项保持一致。
_TABLE_GENERATOR_LABELS = {
    "projection_10yr": "十年预估",
    "projection_10yr_baseline": "十年预估·基准线排放",
    "projection_10yr_project": "十年预估·项目排放",
}


def _fmt(value):
    """将值格式化为展示字符串。"""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".") if value else "0"
    if isinstance(value, int):
        return f"{value}"
    return str(value)


def _get_project(project_id):
    try:
        from core.project_manager import get_project
        return get_project(project_id)
    except Exception:
        return None


def _get_enterprise():
    try:
        from core.project_manager import get_enterprise_config
        return get_enterprise_config() or {}
    except Exception:
        return {}


def _parse_hook_params(project):
    try:
        raw = project.get("hook_params", "{}")
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return {}


def _get_hook_label(project):
    hook_name = project.get("hook_name", "")
    if not hook_name:
        return ""
    try:
        from core.carbon_calculator import CarbonCalculator
        cls = CarbonCalculator._formulas_registry.get(hook_name)
        if cls:
            return getattr(cls, "formula_label", hook_name)
    except Exception:
        pass
    return hook_name


def _build_project_fields(project, enterprise, hook_params):
    """构建「项目中自有字段」分组列表（含实际值）。

    project 为 None 时仍返回完整字段清单（值留空），方便未选项目时
    也能浏览全部可引用的系统字段。
    """
    has_project = bool(project)
    project = project or {}
    enterprise = enterprise or {}
    hook_params = hook_params or {}

    groups = []

    # ── 报告信息（生成报告时由表单/系统注入，模板可直接引用）──
    groups.append({
        "key": "report",
        "label": "报告信息",
        "variables": [
            {"path": "{{ report.type_label }}", "label": "报告类型名称",
             "value": "核查报告 / 项目设计文档（PDD）/ 审定报告", "type": "string"},
            {"path": "{{ report.start_date }}", "label": "起始日期（生成报告表单）",
             "value": "YYYY-MM-DD，默认近30天", "type": "string"},
            {"path": "{{ report.end_date }}", "label": "截止日期（生成报告表单）",
             "value": "YYYY-MM-DD，默认今天", "type": "string"},
            {"path": "{{ report.generated_at }}", "label": "报告生成时间",
             "value": "YYYY-MM-DD HH:MM:SS", "type": "string"},
            {"path": "{{ report.today }}", "label": "今天（落款/提交日期用）",
             "value": "YYYY年MM月DD日", "type": "string"},
            {"path": "{{ report.generator }}", "label": "生成器名称",
             "value": "通用碳账户管理系统 v1.0", "type": "string"},
        ],
    })

    groups.append({
        "key": "enterprise",
        "label": "企业信息",
        "variables": [
            {"path": "{{ enterprise.name }}", "label": "企业名称",
             "value": enterprise.get("enterprise_name", ""), "type": "string"},
            {"path": "{{ enterprise.id }}", "label": "企业序号",
             "value": enterprise.get("enterprise_id", ""), "type": "string"},
            {"path": "{{ enterprise.logo_path }}", "label": "报告Logo路径",
             "value": enterprise.get("report_logo_path", ""), "type": "string"},
        ],
    })

    groups.append({
        "key": "project",
        "label": "项目信息",
        "variables": [
            {"path": "{{ project.name }}", "label": "项目名称",
             "value": project.get("project_name", ""), "type": "string"},
            {"path": "{{ project.code }}", "label": "项目编号",
             "value": project.get("project_code") or ("（未分配）" if has_project else ""), "type": "string"},
            {"path": "{{ project.type }}", "label": "项目类型",
             "value": project.get("project_type", project.get("hook_name", "")), "type": "string"},
            {"path": "{{ project.summary }}", "label": "项目简介",
             "value": project.get("project_summary", ""), "type": "string"},
            {"path": "{{ project.baseline_date }}", "label": "基准日期",
             "value": project.get("baseline_date", ""), "type": "string"},
            {"path": "{{ project.hook_label }}", "label": "公式显示名",
             "value": _get_hook_label(project), "type": "string"},
        ],
    })

    if hook_params:
        vars_list = [
            {"path": f"{{{{ project.{k} }}}}", "label": k,
             "value": _fmt(v), "type": "string"}
            for k, v in hook_params.items()
        ]
        groups.append({
            "key": "hook_params",
            "label": "公式参数",
            "variables": vars_list,
        })

    groups.append({
        "key": "carbon_assets",
        "label": "碳资产数据 (tCO₂e)",
        "variables": [
            {"path": "{{ carbon_assets.total }}", "label": "总碳资产",
             "value": _fmt(project.get("total_carbon_assets")), "type": "number"},
            {"path": "{{ carbon_assets.available_gross }}", "label": "可用碳资产 A",
             "value": _fmt(project.get("available_assets_gross")), "type": "number"},
            {"path": "{{ carbon_assets.available_net }}", "label": "可用碳资产 B",
             "value": _fmt(project.get("available_assets_net")), "type": "number"},
            {"path": "{{ carbon_assets.traded }}", "label": "已交易量",
             "value": _fmt(project.get("traded_amount")), "type": "number"},
            {"path": "{{ carbon_assets.cumulative_reduction }}", "label": "全历史累计减排量",
             "value": _fmt(project.get("lifetime_reduction")), "type": "number"},
            {"path": "{{ carbon_assets.pending_verification }}", "label": "待核证减排量（以核查区间为准）",
             "value": (_fmt(project.get("pending_verification_cumulative"))
                       if project.get("pending_status") != "unknown" else "待核对"), "type": "number"},
            {"path": "{{ carbon_assets.annual_estimate }}", "label": "预估年减排量",
             "value": _fmt(project.get("estimated_annual_reduction")), "type": "number"},
        ],
    })

    # ── 监测数据（统计周期 = 报告起始~截止日期）──
    groups.append({
        "key": "monitoring",
        "label": "监测数据",
        "variables": [
            {"path": "{{ monitoring.total_records }}", "label": "监测记录数",
             "value": "", "type": "number"},
            {"path": "{{ monitoring.total_reduction_fmt }}", "label": "期内总减排量 (tCO₂e)",
             "value": "", "type": "number"},
            {"path": "{{ monitoring.days }}", "label": "监测天数",
             "value": "", "type": "number"},
            {"path": "{{ monitoring.daily_avg_fmt }}", "label": "日均减排量",
             "value": "", "type": "number"},
            {"path": "{{ monitoring.annual_estimate_fmt }}", "label": "折算年减排量",
             "value": "", "type": "number"},
            {"path": "{{ monitoring.pre_estimated_reduction_fmt }}", "label": "项目预估年减排量",
             "value": _fmt(project.get("estimated_annual_reduction")), "type": "number"},
            {"path": "{{ monitoring.runtime_fmt }}", "label": "运行时长（分钟）",
             "value": "", "type": "number"},
            {"path": "{{ monitoring.consumed_power_fmt }}", "label": "累计消耗电量 (kWh)",
             "value": "", "type": "number"},
            {"path": "{{ monitoring.compressed_air_volume_fmt }}", "label": "累计压缩空气量 (m³)",
             "value": "", "type": "number"},
            {"path": "{{ monitoring.baseline_emissions_fmt }}", "label": "累计基准线排放 (tCO₂e)",
             "value": "", "type": "number"},
            {"path": "{{ monitoring.project_emissions_fmt }}", "label": "累计项目排放 (tCO₂e)",
             "value": "", "type": "number"},
        ],
    })

    # ── 公式/方法学 ──
    groups.append({
        "key": "formula",
        "label": "公式信息",
        "variables": [
            {"path": "{{ formula.label }}", "label": "公式显示名",
             "value": _get_hook_label(project), "type": "string"},
            {"path": "{{ formula.text }}", "label": "计算公式文本",
             "value": "", "type": "string"},
            {"path": "{{ formula.version }}", "label": "公式版本",
             "value": "", "type": "string"},
            {"path": "{{ formula.description }}", "label": "公式描述",
             "value": "", "type": "string"},
            {"path": "{{ formula.ef_grid }}", "label": "电网排放因子",
             "value": "", "type": "number"},
        ],
    })

    # ── 排放源清单（动态表格行循环）──
    groups.append({
        "key": "emission_sources",
        "label": "排放源清单（表格行循环）",
        "variables": [
            {"path": "{%tr for src in emission_sources %}", "label": "行循环-开头",
             "value": "", "type": "control"},
            {"path": "{{ src.seq }}", "label": "序号", "value": "", "type": "string"},
            {"path": "{{ src.name }}", "label": "排放源名称", "value": "", "type": "string"},
            {"path": "{{ src.activity_data }}", "label": "活动数据", "value": "", "type": "string"},
            {"path": "{{ src.emission_factor }}", "label": "排放因子", "value": "", "type": "string"},
            {"path": "{{ src.emission_amount }}", "label": "排放量", "value": "", "type": "string"},
            {"path": "{%tr endfor %}", "label": "行循环-结尾",
             "value": "", "type": "control"},
        ],
    })

    # ── 声明（列表循环）──
    groups.append({
        "key": "declarations",
        "label": "声明（列表循环）",
        "variables": [
            {"path": "{% for d in declarations %}", "label": "循环-开头",
             "value": "", "type": "control"},
            {"path": "{{ d }}", "label": "声明内容", "value": "", "type": "string"},
            {"path": "{% endfor %}", "label": "循环-结尾",
             "value": "", "type": "control"},
        ],
    })

    groups.append({
        "key": "stages",
        "label": "项目阶段（列表循环）",
        "variables": [
            {"path": "{% for stage in stages %}", "label": "遍历5个阶段",
             "value": "", "type": "control"},
            {"path": "{{ stage.name }}", "label": "阶段名称",
             "value": "", "type": "string"},
            {"path": "{{ stage.status }}", "label": "阶段状态",
             "value": "", "type": "string"},
            {"path": "{% endfor %}", "label": "结束循环",
             "value": "", "type": "control"},
        ],
    })

    return groups


def _build_custom_fields(project_id, project):
    """构建「自定义字段」分组列表（来自企业预设 + 项目预设，有值才可用）。"""
    groups = []

    # ── 企业预设 ──
    try:
        from core.presets import load_presets, _strip_html
        enterprise_presets = load_presets()
    except Exception:
        enterprise_presets = []

    ent_vars = []
    for p in enterprise_presets:
        key = p.get("key", "")
        if not key:
            continue
        ptype = p.get("type", "text")
        raw_value = p.get("value", "")
        if ptype == "richtext":
            value = _strip_html(raw_value) if raw_value else ""
        elif ptype == "image":
            value = raw_value
        else:
            value = raw_value
        available = bool(value)
        ent_vars.append({
            "path": f"{{{{ preset.{key} }}}}", "label": p.get("label", key),
            "value": value, "available": available,
        })
    groups.append({"key": "preset", "label": "企业预设字段", "variables": ent_vars})

    # ── 项目预设（通用字段 + 项目独有，合并视图）──
    if project_id:
        try:
            from core.project_presets import load_all_project_presets, _strip_html, _eval_computed
            project_presets = load_all_project_presets(project_id)
        except Exception:
            project_presets = []
    else:
        project_presets = []

    proj_vars = []
    for p in project_presets:
        key = p.get("key", "")
        if not key:
            continue
        ptype = p.get("type", "text")
        raw_value = p.get("value", "")
        if ptype == "computed":
            if not raw_value or not str(raw_value).strip():
                display = ""
                available = False
            else:
                value = _eval_computed(str(raw_value), project or {})
                available = isinstance(value, (int, float))
                display = _fmt(value) if available else str(value)
        elif ptype == "richtext":
            display = _strip_html(raw_value) if raw_value else ""
            available = bool(display)
        elif ptype == "image":
            display = raw_value
            available = bool(raw_value)
        else:
            display = raw_value
            available = bool(raw_value)
        proj_vars.append({
            "path": f"{{{{ project_preset.{key} }}}}", "label": p.get("label", key),
            "value": display, "available": available,
        })
    groups.append({"key": "project_preset", "label": "项目预设字段", "variables": proj_vars})

    return groups


# ── computed 表达式可引用的 project 数值列（与 _eval_computed 的注入保持一致）──
_COMPUTED_COLUMN_LABELS = {
    "total_carbon_assets": "总碳资产 (tCO₂e)",
    "available_assets_gross": "可用碳资产A (tCO₂e)",
    "available_assets_net": "可用碳资产B (tCO₂e)",
    "traded_amount": "已交易量 (tCO₂e)",
    "estimated_annual_reduction": "预估年减排量 (tCO₂e)",
    "pending_validation_10yr": "待审定10年减排量 (tCO₂e)",
    "pending_verification_cumulative": "待核证减排量 (tCO₂e)",
    "lifetime_reduction": "全历史累计减排量 (tCO₂e)",
}

# 常见基准线因子的中文说明（缺省回退「基准线因子 {key}」）
_BASELINE_FACTOR_LABELS = {
    "z_b": "单位用气碳排放因子 Z_b (10⁻⁵ tCO₂/m³)",
    "w_b": "基准线用电单耗 W_b (kWh/m³)",
}


def get_computed_vars(project):
    """构建「计算参数」列表：computed 类型预设表达式中可引用的变量。

    来源两部分（与 core.project_presets._eval_computed 的注入规则一致）：
    1. project 对象的数值字段（carbon_projects 表数值列）
    2. hook_params 公式参数（项目 hook_params JSON 中的键值）

    参数:
        project: 项目字典，可为 None（未选项目时仅列出变量名）

    返回:
        list[dict]: [{"object": "project", "name": "...", "label": "...", "value": "..."}]
    """
    variables = []

    for col, label in _COMPUTED_COLUMN_LABELS.items():
        variables.append({
            "object": "project",
            "name": col,
            "label": label,
            "value": _fmt(project.get(col)) if project else "",
        })

    # hook_params 公式参数（数值型才可在表达式中使用）
    hook_params = _parse_hook_params(project) if project else {}
    for k, v in hook_params.items():
        is_number = isinstance(v, (int, float))
        if not is_number and isinstance(v, str):
            try:
                float(v)
                is_number = True
            except ValueError:
                pass
        variables.append({
            "object": "hook_params",
            "name": k,
            "label": "公式参数" + ("" if is_number else "（非数值，不可参与计算）"),
            "value": _fmt(v),
        })

    return variables


def _code_env_fallback(project, project_id=None):
    """为 all_objects 里 code 类型预设构建兜底执行环境。

    模拟报告渲染时的环境（`ReportContextBuilder._build_code_env`），让「所有对象及其值」
    面板里的示例值和真正生成报告时算出来的一致：

    - project 字段 + 扁平化的 hook_params
    - `get_reduction(start, end)`：按项目查任意区间减排量
    - `report`：报告元信息桩。面板里没有真实的生成表单，用项目基准日期当
      `report.start_date`（`range_reduction` 之类会引用它），`end_date` 取今天。
      真正生成报告时这两个值来自表单日期区间，和这里的预览值可能不同。
    """
    env = dict(project or {})
    try:
        raw_params = (project or {}).get("hook_params", "{}")
        if isinstance(raw_params, str):
            raw_params = json.loads(raw_params)
        if isinstance(raw_params, dict):
            for k, v in raw_params.items():
                if k not in env:
                    env[k] = v
    except Exception:
        pass

    # get_reduction：报告路径由 _build_code_env 注入，这里之前漏了，
    # 导致 range_reduction 在面板里报 NameError
    try:
        if project_id:
            from core.reduction import make_get_reduction
            env.setdefault("get_reduction", make_get_reduction(project_id))
    except Exception:
        pass

    # report 桩：code 预设里引用的 report.start_date / end_date
    try:
        from datetime import datetime as _dt
        env.setdefault("report", {
            "type": "", "type_label": "（预览）",
            "start_date": (project or {}).get("baseline_date") or "",
            "end_date": _dt.now().strftime("%Y-%m-%d"),
            "generated_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            "generator": "通用碳账户管理系统 v1.0",
            "annual_code": "",
        })
    except Exception:
        pass
    return env


def get_all_objects(project, project_id=None):
    """构建「所有对象及其值」清单：报告 context 中全部顶层对象 → 变量 → 当前值。

    分组覆盖：report / enterprise / project / hook_params / carbon_assets /
    monitoring / formula / preset / project_preset / stages / emission_sources /
    declarations / pdd / computed 表达式可用变量。

    每条变量: {"path": "report.start_date", "label": "...", "value": "...",
               "computable": True/False}  — computable=True 表示可在 computed
    类型预设的表达式中直接引用（规则与 _eval_computed 注入一致）。

    参数:
        project: 项目字典，可为 None
        project_id: 项目 ID（加载项目预设与监测快照需要）
    """
    has_project = bool(project)
    project = project or {}
    enterprise = _get_enterprise()
    groups = []

    def _v(path, label, value="", computable=False):
        return {"path": path, "label": label, "value": value, "computable": computable}

    def _brief(text, n=40):
        text = str(text or "")
        return text if len(text) <= n else text[:n] + "…"

    # ── report（报告信息）──
    annual_code = ""
    if has_project:
        try:
            from .context_builder import ReportContextBuilder
            annual_code = ReportContextBuilder.get_annual_code(project, "verification")
        except Exception:
            annual_code = ""
    groups.append({
        "key": "report", "label": "report（报告信息）",
        "hint": "生成报告时由系统和表单注入",
        "vars": [
            _v("report.type", "报告类型 key", "verification / pdd"),
            _v("report.type_label", "报告类型名称", "核查报告 / 项目设计文档（PDD）"),
            _v("report.start_date", "起始日期", "来自生成报告表单"),
            _v("report.end_date", "截止日期", "来自生成报告表单"),
            _v("report.generated_at", "生成时间", "生成时自动写入"),
            _v("report.generator", "生成器名称", "通用碳账户管理系统 v1.0"),
            _v("report.annual_code", "年度核查编号（设置中开启后生效）",
               annual_code or "（开关未开启或项目无编号）"),
        ],
    })

    # ── enterprise（企业信息）──
    groups.append({
        "key": "enterprise", "label": "enterprise（企业信息）",
        "hint": "来自系统设置-企业配置",
        "vars": [
            _v("enterprise.name", "企业名称", enterprise.get("enterprise_name", "")),
            _v("enterprise.id", "企业序号", enterprise.get("enterprise_id", "")),
            _v("enterprise.logo_path", "报告Logo路径", enterprise.get("report_logo_path", "")),
        ],
    })

    # ── project（项目基本信息，context 中的 project 对象）──
    proj_vars = [
        _v("project.name", "项目名称", project.get("project_name", "")),
        _v("project.code", "项目编号", project.get("project_code") or ("（未分配）" if has_project else "")),
        _v("project.type", "项目类型", project.get("project_type", project.get("hook_name", ""))),
        _v("project.summary", "项目简介", _brief(project.get("project_summary", ""))),
        _v("project.baseline_date", "基准日期", project.get("baseline_date", "")),
        _v("project.hook_name", "公式标识", project.get("hook_name", "")),
        _v("project.hook_label", "公式显示名", _get_hook_label(project)),
    ]
    groups.append({
        "key": "project", "label": "project（项目基本信息）",
        "hint": "模板中 {{ project.xxx }} 引用",
        "vars": proj_vars,
    })

    # ── hook_params（公式参数，已扁平化进 project 对象）──
    hook_params = _parse_hook_params(project) if has_project else {}
    if hook_params:
        hp_vars = []
        for k, val in hook_params.items():
            is_number = isinstance(val, (int, float))
            if not is_number and isinstance(val, str):
                try:
                    float(val)
                    is_number = True
                except ValueError:
                    pass
            hp_vars.append(_v(f"project.{k}", f"公式参数 {k}" + ("" if is_number else "（非数值）"),
                              _fmt(val), computable=is_number))
        groups.append({
            "key": "hook_params", "label": "hook_params（公式参数，模板中 {{ project.参数名 }}）",
            "hint": "数值型参数可参与 computed 计算",
            "vars": hp_vars,
        })

    # ── carbon_assets（碳资产数据）──
    groups.append({
        "key": "carbon_assets", "label": "carbon_assets（碳资产数据 tCO₂e）",
        "hint": "来自项目碳资产台账",
        "vars": [
            _v("carbon_assets.total", "总碳资产", _fmt(project.get("total_carbon_assets"))),
            _v("carbon_assets.available_gross", "可用碳资产 A", _fmt(project.get("available_assets_gross"))),
            _v("carbon_assets.available_net", "可用碳资产 B", _fmt(project.get("available_assets_net"))),
            _v("carbon_assets.traded", "已交易量", _fmt(project.get("traded_amount"))),
            _v("carbon_assets.cumulative_reduction", "全历史累计减排量", _fmt(project.get("lifetime_reduction"))),
            _v("carbon_assets.pending_verification", "待核证减排量",
               _fmt(project.get("pending_verification_cumulative"))
               if project.get("pending_status") != "unknown" else "待核对"),
            _v("carbon_assets.annual_estimate", "预估年减排量", _fmt(project.get("estimated_annual_reduction"))),
        ],
    })

    # ── monitoring（监测数据，近30天快照值；报告按所选日期区间另算）──
    mon_snapshot = {}
    if project_id:
        try:
            from datetime import datetime, timedelta
            from .context_builder import ReportContextBuilder
            now = datetime.now()
            mon_snapshot = ReportContextBuilder._get_monitoring_data(
                project_id, (now - timedelta(days=30)).strftime("%Y-%m-%d"),
                now.strftime("%Y-%m-%d")) or {}
        except Exception:
            mon_snapshot = {}
    groups.append({
        "key": "monitoring", "label": "monitoring（监测数据，值为近30天快照）",
        "hint": "报告中按生成时选择的起始~截止日期重新统计",
        "vars": [
            _v("monitoring.total_records", "监测记录数", _fmt(mon_snapshot.get("total_records"))),
            _v("monitoring.total_reduction_fmt", "期内总减排量（格式化）", mon_snapshot.get("total_reduction_fmt", "")),
            _v("monitoring.days", "监测天数", _fmt(mon_snapshot.get("days"))),
            _v("monitoring.daily_avg_fmt", "日均减排量（格式化）", mon_snapshot.get("daily_avg_fmt", "")),
            _v("monitoring.annual_estimate_fmt", "折算年减排量（格式化）", mon_snapshot.get("annual_estimate_fmt", "")),
            _v("monitoring.pre_estimated_reduction_fmt", "项目预估年减排量（格式化）", mon_snapshot.get("pre_estimated_reduction_fmt", "")),
            _v("monitoring.ten_year_total", "十年预估减排总量", _fmt(mon_snapshot.get("ten_year_total"))),
            _v("monitoring.ten_year_total_fmt", "十年预估减排总量（格式化）", mon_snapshot.get("ten_year_total_fmt", "")),
            _v("monitoring.runtime_fmt", "运行时长（分钟）", mon_snapshot.get("runtime_fmt", "")),
            _v("monitoring.consumed_power_fmt", "累计消耗电量 kWh", mon_snapshot.get("consumed_power_fmt", "")),
            _v("monitoring.compressed_air_volume_fmt", "累计压缩空气量 m³", mon_snapshot.get("compressed_air_volume_fmt", "")),
            _v("monitoring.baseline_emissions_fmt", "累计基准线排放", mon_snapshot.get("baseline_emissions_fmt", "")),
            _v("monitoring.project_emissions_fmt", "累计项目排放", mon_snapshot.get("project_emissions_fmt", "")),
            _v("monitoring.yearly_projection", "十年逐年预估（列表，循环用）", "{%tr for y in monitoring.yearly_projection %}"),
        ],
    })

    # ── formula（公式信息，含 API 获取的基准线因子）──
    baseline_factors = {}
    if has_project:
        try:
            import config
            from core.carbon_calculator import CarbonCalculator
            from core.project_manager import get_primary_machine
            # 公式实例按**机器**缓存，这里取项目主机器的那个
            mid = (get_primary_machine(project.get("id")) or {}).get("id") if project.get("id") else None
            if not (mid and mid in CarbonCalculator._instances):
                CarbonCalculator.init_project_formula(project, config.VCER_API_URL)
                mid = (get_primary_machine(project.get("id")) or {}).get("id")
            formula = CarbonCalculator.get_instance(mid) if mid else None
            if formula:
                baseline_factors = formula.get_baseline_factors() or {}
        except Exception:
            baseline_factors = {}
    formula_vars = [
        _v("formula.label", "公式显示名", _get_hook_label(project)),
        _v("formula.text", "计算公式文本", "（生成时从公式引擎获取）"),
        _v("formula.version", "公式版本", ""),
        _v("formula.description", "公式描述", ""),
        _v("formula.ef_grid", "电网排放因子", ""),
    ]
    for k, val in baseline_factors.items():
        formula_vars.append(_v(f"formula.{k}", _BASELINE_FACTOR_LABELS.get(k, f"基准线因子 {k}"), _fmt(val)))
    groups.append({
        "key": "formula", "label": "formula（公式信息）",
        "hint": "来自项目绑定的计算公式；基准线因子由 API 按项目公式参数获取",
        "vars": formula_vars,
    })

    # ── preset（企业预设）──
    try:
        from core.presets import load_presets, _strip_html
        ent_presets = load_presets()
    except Exception:
        ent_presets = []
    preset_vars = []
    for p in ent_presets:
        raw = p.get("value", "")
        if p.get("type") == "code":
            from core.code_executor import run_to_string
            # 带上 project_id：企业 code 预设同样可以调 get_reduction（报告路径里能调到）
            disp = _brief(run_to_string(str(raw), env=_code_env_fallback(project, project_id)))
        elif p.get("type") == "richtext":
            disp = _brief(_strip_html(raw))
        else:
            disp = _brief(raw)
        preset_vars.append(_v(f"preset.{p.get('key', '')}", p.get("label", p.get("key", "")), disp))
    groups.append({
        "key": "preset", "label": "preset（企业预设字段）",
        "hint": "在预设信息管理-企业预设中维护",
        "vars": preset_vars,
    })

    # ── project_preset（项目预设，通用+独有合并）──
    pp_vars = []
    if project_id:
        try:
            from core.project_presets import (
                load_all_project_presets, get_project_presets_context,
                _strip_html as _pp_strip,
            )
            # 走和报告渲染完全同一条求值路径。之前这里是各自 run_to_string + 精简 env，
            # 既能不到 get_reduction、也没把 project_preset 绑给 code 字段引用，
            # 于是 install_location / crediting_period_range / range_reduction 全报 NameError。
            ctx = get_project_presets_context(
                project_id, project, env=_code_env_fallback(project, project_id))
            for p in load_all_project_presets(project_id):
                key = p.get("key", "")
                raw = p.get("value", "")
                ptype = p.get("type")
                if ptype in ("code", "computed", "text"):
                    # 这三种的最终值都在 ctx 里（code 已求值、computed 已算、text 已合并项目覆盖值）
                    val = ctx.get(key, raw)
                    disp = _fmt(val) if isinstance(val, (int, float)) else _brief(str(val or ""))
                elif ptype == "richtext":
                    disp = _brief(_pp_strip(raw))
                elif ptype == "table":
                    try:
                        import json as _j
                        d = _j.loads(raw) if isinstance(raw, str) else (raw or {})
                        gen = d.get("generator") if isinstance(d, dict) else None
                        if gen in _TABLE_GENERATOR_LABELS:
                            disp = f"[表格：自动生成（{_TABLE_GENERATOR_LABELS[gen]}）]"
                        elif isinstance(d, dict):
                            nrow = len(d.get("rows", []) or [])
                            ncol = len(d.get("headers", []) or [])
                            disp = f"[表格：{nrow} 行 × {ncol} 列]"
                        else:
                            disp = _brief(raw)
                    except Exception:
                        disp = _brief(raw)
                else:
                    disp = _brief(raw)
                pp_vars.append(_v(f"project_preset.{p.get('key', '')}",
                                  p.get("label", p.get("key", "")), disp))
        except Exception:
            pass
    groups.append({
        "key": "project_preset", "label": "project_preset（项目预设字段）",
        "hint": "在预设信息管理-项目预设中维护；选择项目后显示",
        "vars": pp_vars,
    })

    # ── stages（项目阶段，列表循环）──
    stage_names = {1: "项目申请", 2: "项目审定", 3: "项目核查", 4: "项目交易", 5: "碳减排量抵消"}
    stage_summary = " | ".join(
        f"{s}.{stage_names[s]}:{'已通过' if project.get(f'stage{s}_passed') else '未提交'}" for s in range(1, 6)
    ) if has_project else ""
    groups.append({
        "key": "stages", "label": "stages（项目阶段，列表循环）",
        "hint": "{% for stage in stages %} 循环使用",
        "vars": [
            _v("stage.name", "阶段名称（循环内）", stage_summary),
            _v("stage.status", "阶段状态（循环内）", ""),
            _v("stage.num", "阶段序号（循环内）", ""),
            _v("stage.is_completed", "是否已通过（循环内）", ""),
        ],
    })

    # ── emission_sources（排放源清单，表格行循环）──
    groups.append({
        "key": "emission_sources", "label": "emission_sources（排放源清单，表格行循环）",
        "hint": "{%tr for src in emission_sources %} 表格行循环",
        "vars": [
            _v("src.seq", "序号（循环内）", ""),
            _v("src.name", "排放源名称（循环内）", ""),
            _v("src.activity_data", "活动数据（循环内）", ""),
            _v("src.emission_factor", "排放因子（循环内）", ""),
            _v("src.emission_amount", "排放量（循环内）", ""),
        ],
    })

    # ── declarations（声明，列表循环）──
    groups.append({
        "key": "declarations", "label": "declarations（声明，列表循环）",
        "hint": "{% for d in declarations %} 循环使用",
        "vars": [_v("d", "声明内容（循环内）", "")],
    })

    # ── pdd（PDD 特有对象）──
    groups.append({
        "key": "pdd", "label": "pdd（PDD 特有数据）",
        "hint": "PDD 模板用 {% if pdd %} 控制显隐",
        "vars": [
            _v("pdd.methodology.reference", "方法学引用", ""),
            _v("pdd.methodology.applicability", "方法学适用性", ""),
            _v("pdd.methodology.formula_text", "方法学公式文本", ""),
            _v("pdd.additionality.barrier_analysis", "额外性屏障分析", ""),
            _v("pdd.additionality.common_practice", "普遍实践分析", ""),
            _v("pdd.additionality.investment_analysis", "投资分析", ""),
            _v("pdd.environmental_impact", "环境影响评价", ""),
            _v("pdd.stakeholder_comments", "利益相关方意见", ""),
            _v("pdd.monitoring_plan.qa_qc", "监测计划QA/QC", ""),
            _v("pdd.crediting_period.first_period", "计入期", ""),
            _v("pdd.crediting_period.start_date", "计入期起始", ""),
            _v("pdd.overview.baseline_scenario", "基准线情景", ""),
            _v("pdd.overview.project_scenario", "项目情景", ""),
            _v("pdd.projection", "十年逐年预估（列表，循环用）", "{%tr for y in pdd.projection %}"),
            _v("y.year", "预估年份（循环内）", ""),
            _v("y.start", "年份起始（循环内）", ""),
            _v("y.end", "年份结束（循环内）", ""),
            _v("y.baseline_emission", "基准线排放 tCO₂e（循环内）", ""),
            _v("y.project_emission", "项目排放 tCO₂e（循环内）", ""),
            _v("y.reduction", "减排量 tCO₂e（循环内）", ""),
        ],
    })

    # ── computed 表达式可用变量（可参与计算的一类）──
    computed_vars = []
    for col, label in _COMPUTED_COLUMN_LABELS.items():
        computed_vars.append(_v(col, label, _fmt(project.get(col)) if has_project else "",
                                computable=True))
    if hook_params:
        for k, val in hook_params.items():
            is_number = isinstance(val, (int, float))
            if not is_number and isinstance(val, str):
                try:
                    float(val)
                    is_number = True
                except ValueError:
                    pass
            if is_number:
                computed_vars.append(_v(k, f"公式参数 {k}", _fmt(val), computable=True))
    groups.append({
        "key": "computed", "label": "🔢 computed 表达式可用变量（可参与计算）",
        "hint": "在「自动计算」类型预设的表达式中直接引用变量名，支持 + - * / ( )",
        "vars": computed_vars,
    })

    # ── 可用函数（代码预设/控制台可调用）──
    func_rows = [
        ("get_reduction(start, end)", "任意日期区间累计减排量 (tCO₂e)",
         "get_reduction('2022-05-13', '2023-12-31')"),
        ("round(x, n)", "四舍五入到 n 位小数",
         "round(get_reduction('2022-05-13', '2023-12-31'), 2)"),
        ("str(x) / int(x) / float(x)", "类型转换",
         "str(123.45) · int('5') · float('1.5')"),
        ("len / min / max / sum / abs", "常用内置函数",
         "max(1, 2, 3) · sum([1, 2, 3]) · round(3.14159, 2)"),
        ("datetime / date / timedelta", "日期对象（代码环境已注入）",
         "datetime.now().strftime('%Y-%m-%d')"),
    ]
    # 给 get_reduction 附上当前项目的实时示例结果
    if project_id:
        try:
            from core.reduction import get_reduction
            val = get_reduction(project_id, '2024-01-01', '2024-12-31')
            func_rows[0] = (func_rows[0][0], func_rows[0][1],
                            f"get_reduction('2024-01-01', '2024-12-31') → {val:.2f} tCO₂e")
        except Exception:
            pass
    groups.append({
        "key": "functions", "label": "⚙ 可用函数（代码预设/控制台可调用）",
        "hint": "在「代码(code)」类型预设或控制台里直接调用；get_reduction 示例结果按当前项目实时计算",
        "vars": [_v(path, label, example) for path, label, example in func_rows],
    })

    return groups


def get_controls_reference():
    """返回 Jinja2 控制结构参考（模板编写参考，与具体项目无关）。"""
    return [
        {
            "syntax": "{%tr for src in emission_sources %}",
            "label": "动态表格行循环-开头",
            "description": "docxtpl 专用语法。此行和 {%tr endfor %} 行各占一个独立表格行，"
                           "中间模板行被自动克隆。渲染后控制行消失。注意：{% 和 tr 之间不能有空格。",
        },
        {
            "syntax": "{%tr endfor %}",
            "label": "动态表格行循环-结尾",
            "description": "独占一个表格行，与 {%tr for %} 配对使用。",
        },
        {
            "syntax": "{% for item in list %} ... {% endfor %}",
            "label": "列表/数组循环",
            "description": "遍历任意列表，如 stages / emission_sources / declarations 等。",
        },
        {
            "syntax": "{% if condition %} ... {% endif %}",
            "label": "条件判断",
            "description": "根据条件显隐内容。如 {% if pdd %} 控制 PDD 特有内容的显示。"
                           "注意：不要用 {% if %} 包裹 {%tr for %}，会导致 Jinja2 嵌套错误。",
        },
        {
            "syntax": '{{ "%.2f"|format(value) }}',
            "label": "数字格式化",
            "description": "Jinja2 内置 format 过滤器。%.2f=两位小数，%,.2f=千分位+两位小数。",
        },
        {
            "syntax": "{{ value|default('默认值') }}",
            "label": "默认值过滤器",
            "description": "变量为空时使用默认值。可用于可选字段。",
        },
        {
            "syntax": "{% if not loop.last %}, {% endif %}",
            "label": "循环分隔符",
            "description": "在循环中插入分隔符（如逗号），最后一个元素后不加。",
        },
    ]


def get_available_fields(project_id=None):
    """返回报告模板可用的字段（动态，基于选定项目）。

    参数:
        project_id: 可选，项目 ID。传入后返回该项目实际字段值。

    返回:
        dict: {"project_fields": [...], "custom_fields": [...], "controls": [...]}
    """
    project = _get_project(project_id) if project_id else None
    enterprise = _get_enterprise()

    if project:
        hook_params = _parse_hook_params(project)
        project_fields = _build_project_fields(project, enterprise, hook_params)
        custom_fields = _build_custom_fields(project_id, project)
    else:
        # 未选择项目：仍展示全部系统自有字段（值留空），自定义字段仅展示企业预设
        project_fields = _build_project_fields(None, enterprise, {})
        custom_fields = _build_custom_fields(None, None)

    return {
        "project_fields": project_fields,
        "custom_fields": custom_fields,
        "computed_vars": get_computed_vars(project),
        "all_objects": get_all_objects(project, project_id),
        "controls": get_controls_reference(),
    }
