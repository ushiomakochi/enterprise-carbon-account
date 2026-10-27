"""
碳减排报告自动生成器（Word 版）。

使用 docxtpl + Jinja2 渲染 .docx 模板，模板由文件夹自动发现
（templates/reports/docx/，支持企业专属子目录）。

报告类型:
- verification: 核查报告
- pdd: 项目设计文档（PDD）
"""

import os
import json
from datetime import datetime

import config
from .project_manager import get_db


class ReportGenerator:
    """碳减排报告生成器"""

    REPORT_TYPES = {
        "verification": "核查报告",
        "pdd": "项目设计文档（PDD）",
        "approval": "审定报告",
    }

    OUTPUT_DIR = config.REPORT_DIR

    # ══════════════════════════════════════════════════════════
    # Word 报告生成（docxtpl 优先，python-docx 兜底）
    # ══════════════════════════════════════════════════════════

    @classmethod
    def generate_word_report(cls, project: dict, report_type: str = "verification",
                             start_date: str = None, end_date: str = None,
                             template_file: str = None, filename_template: str = None,
                             continuity_ack: str = None) -> dict:
        """
        生成 Word 报告（docxtpl + Jinja2 模板）。

        优先使用 docxtpl 渲染 .docx 模板（支持 {% for %}/{% if %}/动态表格），
        docxtpl 不可用时回退到 python-docx 直接操作（兜底）。

        参数:
            project: 项目字典
            report_type: 报告类型 key，如 "verification"、"pdd"
            start_date: 核查起始日期 (YYYY-MM-DD)，默认取近30天
            end_date: 核查结束日期 (YYYY-MM-DD)，默认今天
            template_file: 指定的模板文件名/相对路径（来自前端模板下拉框），
                          为空时自动发现默认模板
            filename_template: 自定义文件名模板。支持 {{ key }} 变量引用与
                              类 Python 字符串表达式，如
                              "{{ project.name }}-{{ report.type_label }}"
                              或 "project.name + '_abc' + project.code"
                              为空时用默认「项目名_报告类型_时间戳.docx」

        返回:
            dict: {status: "成功", download_url: ..., file_path: ...}
        """
        project_id = project["id"]
        now = datetime.now()

        # ── 日期范围处理 ──
        if not end_date:
            end_date = now.strftime("%Y-%m-%d")
        if not start_date:
            from datetime import timedelta
            start_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")

        from .meter_continuity import preflight,acknowledge,normalize_period
        start_date,end_date=normalize_period(start_date,end_date)
        quality_conn=get_db()
        try:
            scope=preflight(quality_conn,project_id,start_date,end_date)
            if scope["requires_confirmation"] and continuity_ack!=scope["signature"]:
                return {"status":"需要确认","code":"continuity_confirmation_required","msg":"核算期包含未确认缺口或计量阶段边界，请查看提示后确认生成","preflight":scope}
        finally: quality_conn.close()
        quality_cases=scope["warnings"]

        # ── 解析模板路径（指定模板优先，否则自动发现默认模板）──
        template_path = None
        template_display_name = "默认"
        if template_file:
            try:
                from .reports import resolve_template_file
                resolved = resolve_template_file(template_file)
                if resolved:
                    template_path = resolved
                    template_display_name = template_file
            except Exception:
                pass
        if not template_path:
            try:
                from .reports import get_template_path as discover_path

                # 未指定模板时，按报告类型自动发现默认模板
                enterprise_name = cls._get_enterprise_name(project)
                discovered = discover_path(report_type, enterprise_name=enterprise_name)
                if discovered:
                    template_path = discovered
                    template_display_name = os.path.basename(discovered)
            except Exception:
                pass

        # ── 输出路径 ──
        os.makedirs(cls.OUTPUT_DIR, exist_ok=True)
        from .reports import get_report_type_label
        report_title = get_report_type_label(report_type)
        if not report_title or report_title == report_type:
            report_title = cls.REPORT_TYPES.get(report_type, "核查报告")

        filename = cls._render_filename(
            project, report_type, report_title, start_date, end_date, now,
            filename_template
        )
        safe_filename = "".join(c for c in filename if c.isalnum() or c in "._-() （）")
        if not safe_filename.strip():
            safe_filename = f"{project.get('project_name', '未命名')}_{report_title}"
        output_path = os.path.join(cls.OUTPUT_DIR, safe_filename)
        if os.path.exists(output_path):
            from uuid import uuid4
            stem,extension=os.path.splitext(output_path)
            output_path=stem+"_"+uuid4().hex[:8]+extension

        # ── 生成前校验：模板用到的 code 预设其依赖的预设是否已填值 ──
        if template_path and os.path.exists(template_path):
            try:
                dep_errors = cls._validate_preset_deps(template_path, project_id)
                if dep_errors:
                    return {"status": "失败", "msg": "；".join(dep_errors)}
            except Exception:
                pass

        # ── 尝试 docxtpl 路径 ──
        used_engine = "python-docx"
        ten_year_total = None
        ten_year_ready = None
        try:
            from .reports import DocxReportBuilder, ReportContextBuilder

            # 构建完整上下文
            context = ReportContextBuilder.build(
                project, report_type, start_date, end_date
            )

            # 十年预估减排总量：与报告中「预计的温室气体总减排量」同源，
            # 回传给前端展示（PDD 生成后用于 VCER 阶段一填报），并落库备查。
            try:
                _m = context.get("monitoring") or {}
                ten_year_total = _m.get("ten_year_total")
                # False = 计入期首年段没有监测数据，这个 0 不是「算出来是 0」而是「没法算」
                ten_year_ready = _m.get("ten_year_ready")
            except Exception:
                ten_year_total = None
                ten_year_ready = None

            # 获取预设中的图片映射（供 InlineImage 使用）
            # 合并企业预设 + 项目预设的图片；key 带命名空间前缀，
            # DocxReportBuilder 按点路径注入到 preset.xxx / project_preset.xxx
            preset_images = {}
            for k, v in ReportContextBuilder.get_preset_inline_images().items():
                preset_images[f"preset.{k}"] = v
            for k, v in ReportContextBuilder.get_project_preset_inline_images(
                    project_id).items():
                preset_images[f"project_preset.{k}"] = v

            if template_path and os.path.exists(template_path):
                DocxReportBuilder.render(template_path, context, output_path,
                                         inline_images=preset_images)
            else:
                # 无模板时使用默认模板
                default_tmpl = cls._get_or_create_default_template(report_type)
                if default_tmpl:
                    DocxReportBuilder.render(default_tmpl, context, output_path,
                                             inline_images=preset_images)
                    template_display_name = os.path.basename(default_tmpl)
                else:
                    # 最后回退：用 python-docx 生成基础结构
                    raise Exception("无可用的 docxtpl 模板")

            used_engine = "docxtpl"

        except ImportError:
            # docxtpl 未安装，回退到旧方法
            print("[报告] docxtpl 未安装，回退到 python-docx 方式")
            legacy_result = cls._generate_word_report_legacy(
                project, report_type, start_date, end_date,
                template_path, output_path
            )
            if legacy_result is not None:
                return legacy_result  # 失败，返回错误信息

        except Exception as e:
            # docxtpl 渲染失败，尝试回退
            print(f"[报告] docxtpl 渲染失败: {e}，尝试回退到 python-docx")
            try:
                legacy_result = cls._generate_word_report_legacy(
                    project, report_type, start_date, end_date,
                    template_path, output_path
                )
                if legacy_result is not None:
                    return legacy_result
            except Exception:
                pass
            return {"status": "失败", "msg": f"Word 文档生成失败: {str(e)}"}

        # ── 记录到数据库 ──
        file_size = os.path.getsize(output_path)
        conn = get_db()
        try:
            current_scope=preflight(conn,project_id,start_date,end_date)
            if current_scope["signature"]!=scope["signature"]:
                os.remove(output_path)
                return {"status":"需要确认","code":"continuity_confirmation_required","msg":"生成期间数据已变化，请重新确认","preflight":current_scope}
            ack_id=acknowledge(conn,project_id,"report_"+report_type,scope,continuity_ack)
            cursor = conn.execute(
                """INSERT INTO reports (project_id, report_type, report_title, file_path, file_size_bytes, parameters)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    report_type,
                    f"{project.get('project_name', '')} — {report_title} ({start_date} ~ {end_date})",
                    config.portable_data_path(output_path),
                    file_size,
                    json.dumps({
                        "start_date": start_date,
                        "end_date": end_date,
                        "template": template_display_name,
                        "engine": used_engine,
                        "ten_year_total": ten_year_total,
                        "ten_year_ready": ten_year_ready,
                        "quality_draft": False,
                        "quality_cases": quality_cases,
                        "continuity_ack_id":ack_id,
                        "continuity_signature":scope["signature"],
                    }, ensure_ascii=False),
                )
            )
            conn.commit()
            report_id = cursor.lastrowid
        finally:
            conn.close()

        return {
            "status": "成功",
            "report_id": report_id,
            "report_title": f"{report_title} ({start_date} ~ {end_date})",
            "file_path": output_path,
            "file_size_bytes": file_size,
            "download_url": f"/api/reports/{report_id}/download",
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "format": "docx",
            "engine": used_engine,
            # 十年预估减排总量（tCO₂e）；无预估数据时为 None
            "ten_year_total": ten_year_total,
            # False = 计入期首年段没有监测数据，预估值算不出来（不是真的为 0）
            "ten_year_ready": ten_year_ready,
            "quality_draft": False,
            "quality_cases": quality_cases,
        }

    @classmethod
    def _extract_template_placeholders(cls, template_path: str) -> set:
        """提取 docx 模板中 {{ }} 占位符里引用的 project_preset.<key>。"""
        keys = set()
        try:
            import docx
            doc = docx.Document(template_path)
            texts = [p.text for p in doc.paragraphs]
            for tbl in doc.tables:
                for row in tbl.rows:
                    for cell in row.cells:
                        texts.append(cell.text)
            full = "\n".join(texts)
            for m in re.finditer(r"\{\{\s*(.*?)\s*\}\}", full):
                for pm in re.finditer(r"project_preset\.(\w+)", m.group(1)):
                    keys.add(pm.group(1))
        except Exception:
            pass
        return keys

    @classmethod
    def _extract_all_placeholders(cls, template_path: str, strict: bool = False) -> dict:
        """提取 docx 模板中所有 {{ }} 占位符，按命名空间分类。

        参数:
            strict: False（默认）时吞掉异常返回空结果，保持调用方永不炸。
                    True 时把异常抛出去——**缓存写入前必须用 strict=True**，
                    否则一次偶发失败（Word 占着模板、网络盘抖动）会被当成
                    「这个模板没有占位符」永久缓存下来，之后既不高亮字段，
                    _validate_preset_deps 也会因为拿到空集合而静默跳过依赖校验。

        返回:
            {"project_preset": [...], "preset": [...], "other": [...]}
            用于在预设信息管理中高亮模板用到的字段。
        """
        import re as _re
        result = {"project_preset": set(), "preset": set(), "other": set()}
        try:
            import docx
            doc = docx.Document(template_path)
            texts = [p.text for p in doc.paragraphs]
            for tbl in doc.tables:
                for row in tbl.rows:
                    for cell in row.cells:
                        texts.append(cell.text)
            full = "\n".join(texts)
            for m in _re.finditer(r"\{\{\s*(.*?)\s*\}\}", full):
                expr = m.group(1).strip()
                expr = _re.split(r"[|%]", expr)[0].strip()  # 去掉 filter / 控制
                for pm in _re.finditer(r"project_preset\.(\w+)", expr):
                    result["project_preset"].add(pm.group(1))
                # 负向后顾：别把 project_preset.x 里的 "preset.x" 也算成企业预设
                for pm in _re.finditer(r"(?<!project_)preset\.(\w+)", expr):
                    result["preset"].add(pm.group(1))
                for pm in _re.finditer(r"^\s*([a-zA-Z_]\w*)\s*$", expr):
                    result["other"].add(pm.group(1))
        except Exception:
            if strict:
                raise
        return {k: sorted(v) for k, v in result.items()}

    @classmethod
    def _validate_preset_deps(cls, template_path: str, project_id: int) -> list:
        """校验模板用到的 code 类型预设，其依赖的其它预设是否已填值。

        逻辑：for 模板里的每个 {{ project_preset.x }}，若 x 是 code 类型，
        提取其代码引用的 project_preset.<dep>，检查 dep 在当前项目是否有值；
        缺失则返回错误信息（模板里没有的预设不检查）。
        """
        keys = cls._extract_template_placeholders(template_path)
        if not keys:
            return []
        from .project_presets import load_all_project_presets, extract_project_preset_refs
        presets = load_all_project_presets(project_id)
        preset_map = {p.get("key"): p for p in presets}
        errors = []
        for key in sorted(keys):
            p = preset_map.get(key)
            if not p or p.get("type") != "code":
                continue
            refs = extract_project_preset_refs(p.get("value", ""))
            missing = []
            for r in sorted(refs):
                rp = preset_map.get(r)
                if not rp or not (rp.get("value") or "").strip():
                    missing.append(rp.get("label", r) if rp else r)
            if missing:
                label = p.get("label", key)
                errors.append(f"预设「{label}」的代码需要以下预设填写：{'、'.join(missing)}，请填写后重试")
        return errors

    @classmethod
    def _render_filename(cls, project, report_type, report_title,
                         start_date, end_date, now, filename_template):
        """根据文件名模板生成报告文件名。

        支持两种写法：
        - {{ key }} 变量引用：{{ project.name }}、{{ report.type_label }} 等
        - 类 Python 字符串表达式：project.name + '_abc' + project.code
          也可混合，如 "{{ project.name }}-abc-{{ report.type_label }}"

        未提供模板或解析失败时，回退默认「项目名_报告类型_时间戳.docx」。
        """
        default = f"{project.get('project_name', '未命名')}_{report_title}_{now.strftime('%Y%m%d_%H%M%S')}.docx"
        if not filename_template or not str(filename_template).strip():
            return default

        tmpl = str(filename_template).strip()

        # 构建求值环境：与报告 context 的字段命名保持一致（project.name 等）
        env = {
            "project": {
                "name": project.get("project_name", ""),
                "code": project.get("project_code") or "",
                "type": project.get("project_type", project.get("hook_name", "")),
                "summary": project.get("project_summary", ""),
                "baseline_date": project.get("baseline_date", ""),
                "hook_name": project.get("hook_name", ""),
            },
            "report": {
                "type": report_type,
                "type_label": report_title,
                "start_date": start_date,
                "end_date": end_date,
                "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            },
            "enterprise": {
                "name": (project.get("enterprise_name") or ""),
            },
            "now": now,
            "ts": now.strftime("%Y%m%d_%H%M%S"),
        }

        try:
            from .code_executor import run
            import re
            # 先探测是否含 {{ }} 占位符
            has_placeholder = bool(re.search(r"\{\{.*?\}\}", tmpl))
            if has_placeholder:
                # 把 {{ expr }} 替换为 str(expr)，其余文字转字符串字面量拼接
                parts = []
                last = 0
                for m in re.finditer(r"\{\{(.*?)\}\}", tmpl):
                    if m.start() > last:
                        parts.append(repr(tmpl[last:m.start()]))
                    parts.append(f"str({m.group(1).strip()})")
                    last = m.end()
                if last < len(tmpl):
                    parts.append(repr(tmpl[last:]))
                expr = " + ".join(parts)
            else:
                # 无 {{ }}：整体作为类 Python 表达式求值（如 project.name + '_abc'）
                expr = tmpl

            result, err = run(expr, env)
            if err and not has_placeholder:
                # 无 {{ }} 且求值失败（如纯中文固定名），当作字面量
                name = tmpl
            elif err:
                # 有 {{ }} 但求值失败，回退默认
                return default
            else:
                name = "" if result is None else str(result)
            if not name.strip():
                return default
            # 确保 .docx 扩展名
            if not name.lower().endswith(".docx"):
                name += ".docx"
            return name
        except Exception:
            return default

    @classmethod
    def _get_enterprise_name(cls, project: dict) -> str:
        """获取企业名称，用于查找企业专属报告模板。

        优先级：enterprise_config > project 中的 enterprise_name 字段
        返回 None 时使用通用模板。
        """
        try:
            from .project_manager import get_enterprise_config
            cfg = get_enterprise_config()
            if cfg and cfg.get("enterprise_name"):
                return cfg["enterprise_name"].strip()
        except Exception:
            pass
        name = project.get("enterprise_name", "")
        return name.strip() if name else None

    @classmethod
    def _get_or_create_default_template(cls, report_type: str) -> str:
        """获取或创建默认 docx 模板"""
        from .reports import get_template_dir as get_docx_dir

        tmpl_dir = get_docx_dir()
        os.makedirs(tmpl_dir, exist_ok=True)

        default_path = os.path.join(tmpl_dir, f"{report_type}.docx")
        if os.path.exists(default_path):
            return default_path

        # 如果该类型没有模板，尝试用 verification 模板
        verification_tmpl = os.path.join(tmpl_dir, "verification.docx")
        if os.path.exists(verification_tmpl) and report_type != "verification":
            return verification_tmpl

        return None

    @classmethod
    def _generate_word_report_legacy(cls, project, report_type, start_date, end_date,
                                     template_path, output_path) -> str:
        """[兜底] python-docx 回退路径（docxtpl 不可用或失败时）。
        成功返回 None（由主方法统一记录 DB），失败返回错误 dict。
        """
        try:
            from docx import Document
        except ImportError:
            return {"status": "失败", "msg": "未安装 python-docx"}

        project_id = project["id"]
        monitoring = cls._get_date_range_monitoring(project_id, start_date, end_date)

        enterprise_cfg = {}
        try:
            from .project_manager import get_enterprise_config
            cfg = get_enterprise_config()
            if cfg:
                enterprise_cfg = cfg
        except Exception:
            pass

        report_title = cls.REPORT_TYPES.get(report_type, "核查报告")

        placeholders = cls._build_legacy_placeholders(
            project, report_type, start_date, end_date,
            monitoring, enterprise_cfg
        )

        try:
            if template_path and os.path.exists(template_path):
                doc = Document(template_path)
            else:
                doc = Document()
                doc.add_heading(f"{report_title}", 0)
                cls._build_default_word_content(doc, placeholders)

            for paragraph in doc.paragraphs:
                cls._replace_in_paragraph(paragraph, placeholders)
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for paragraph in cell.paragraphs:
                            cls._replace_in_paragraph(paragraph, placeholders)
            for section in doc.sections:
                for paragraph in section.header.paragraphs:
                    cls._replace_in_paragraph(paragraph, placeholders)
                for paragraph in section.footer.paragraphs:
                    cls._replace_in_paragraph(paragraph, placeholders)

            doc.save(output_path)
            return None  # 成功，由主方法处理 DB 记录
        except Exception as e:
            return {"status": "失败", "msg": f"Word 文档生成失败 (legacy): {str(e)}"}

    @classmethod
    def _build_legacy_placeholders(cls, project, report_type, start_date, end_date,
                                    monitoring, enterprise_cfg):
        """兜底路径的占位符构建（不依赖 docxtpl，用于 python-docx 直接替换）"""
        now = datetime.now()
        hook_params = {}
        try:
            raw = project.get("hook_params", "{}")
            if isinstance(raw, str):
                raw = json.loads(raw)
            if isinstance(raw, dict):
                hook_params = raw
        except Exception:
            pass

        breakdown_text = ""
        if monitoring.get("latest_breakdown"):
            try:
                bd = json.loads(monitoring["latest_breakdown"]) if isinstance(monitoring["latest_breakdown"], str) else monitoring["latest_breakdown"]
                for k, v in bd.items():
                    breakdown_text += f"{k}: {v}\n"
            except Exception:
                pass

        # 十年口径的预估值：走 core.projection 的统一模型
        #（首年段实测 + 其余按「首年段值 ÷ 首年段天数 × 该段天数」折算）。
        # 兜底路径以前没有这两个数，模板一渲染失败就会缺值或误用 annual_estimate。
        ten_year_total = 0.0
        pre_estimated = 0.0
        ten_year_ready = False
        try:
            from core.projection import build_credit_period_segments
            seg = build_credit_period_segments(project)
            if seg.get("has_first_segment_data"):
                ten_year_total = seg["total_reduction"]
                pre_estimated = seg["annual_reduction"]
                ten_year_ready = True
        except Exception:
            pass

        return {
            "enterprise_name": enterprise_cfg.get("enterprise_name", ""),
            "enterprise_id": enterprise_cfg.get("enterprise_id", ""),
            "project_name": project.get("project_name", ""),
            "project_code": project.get("project_code") or "（未分配）",
            "project_type": project.get("project_type", project.get("hook_name", "")),
            "baseline_date": project.get("baseline_date") or "",
            "report_start_date": start_date,
            "report_end_date": end_date,
            "report_generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "total_records": str(monitoring.get("total_records", 0)),
            "total_reduction": f"{monitoring.get('total_reduction', 0):.4f}",
            "cumulative_reduction": f"{project.get('lifetime_reduction', 0) or 0:.4f}",
            "daily_avg_reduction": f"{monitoring.get('daily_avg', 0):.4f}",
            "annual_estimate": f"{monitoring.get('annual_estimate', 0):.2f}",
            # 十年口径（与 docxtpl 路径的 monitoring.pre_estimated_reduction / ten_year_total 对齐）
            "pre_estimated_reduction": f"{pre_estimated:.2f}",
            "ten_year_total": f"{ten_year_total:.2f}",
            "ten_year_total_ready": str(ten_year_ready),
            "formula_text": "",
            # 同 context_builder：NULL 才是"没配"，0 是合法因子，别用 or 顶掉
            "ef_grid": str(config.EF_GRID_DEFAULT if project.get("ef_grid_override") is None
                           else project["ef_grid_override"]),
            "report_type_label": cls.REPORT_TYPES.get(report_type, "核查报告"),
            "breakdown_summary": breakdown_text,
            **{k: str(v) for k, v in hook_params.items()},
        }

    @classmethod
    def _replace_in_paragraph(cls, paragraph, placeholders: dict):
        """替换段落中的 {{变量}} 占位符。

        处理 python-docx 可能将占位符拆到多个 run 的情况。
        策略：
        1. 快速路径：所有占位符都在单个 run 内 → 直接替换（保留格式）
        2. 跨 run 路径：合并到第一个 run，其余 run 清空
        """
        import re

        full_text = paragraph.text
        if "{{" not in full_text:
            return

        pattern = re.compile(r'\{\{(\w+)\}\}')
        matches = list(pattern.finditer(full_text))
        if not matches:
            return

        runs = paragraph.runs
        if not runs:
            return

        # 检查哪些占位符跨了 run 边界
        any_cross_run = False
        for m in matches:
            match_start, match_end = m.start(), m.end()
            char_pos = 0
            spanned = 0
            for run in runs:
                run_len = len(run.text)
                if char_pos < match_end and char_pos + run_len > match_start:
                    spanned += 1
                char_pos += run_len
            if spanned > 1:
                any_cross_run = True
                break

        if not any_cross_run:
            # 快速路径：单个 run 内替换
            for run in runs:
                for key, val in placeholders.items():
                    placeholder = f"{{{{{key}}}}}"
                    if placeholder in run.text:
                        run.text = run.text.replace(placeholder, str(val))
            return

        # 跨 run 路径：全文替换后写入第一个 run
        replaced = full_text
        for key, val in placeholders.items():
            replaced = replaced.replace(f"{{{{{key}}}}}", str(val))

        if runs:
            runs[0].text = replaced
            for run in runs[1:]:
                run.text = ""

    @classmethod
    def _get_date_range_monitoring(cls, project_id: int, start_date: str, end_date: str) -> dict:
        """获取指定日期范围内的监测数据摘要"""
        conn = get_db()
        try:
            row = conn.execute(
                """SELECT COUNT(*) as cnt,
                   COALESCE(SUM(carbon_reduction), 0) as total_reduction
                   FROM monitoring_data
                   WHERE project_id = ? AND timestamp >= ? AND timestamp <= ?""",
                (project_id, f"{start_date} 00:00:00", f"{end_date} 23:59:59")
            ).fetchone()

            # 最新一条的 breakdown
            latest = conn.execute(
                """SELECT breakdown, cumulative_reduction FROM monitoring_data
                   WHERE project_id = ? AND timestamp >= ? AND timestamp <= ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (project_id, f"{start_date} 00:00:00", f"{end_date} 23:59:59")
            ).fetchone()

            total_records = row["cnt"] if row else 0
            total_reduction = row["total_reduction"] or 0 if row else 0

            # 计算天数
            try:
                d1 = datetime.strptime(start_date, "%Y-%m-%d")
                d2 = datetime.strptime(end_date, "%Y-%m-%d")
                days = max((d2 - d1).days + 1, 1)
            except Exception:
                days = 30

            daily_avg = total_reduction / days if days > 0 else 0
            annual_estimate = daily_avg * 365

            # 注意：这里的 annual_estimate 是「本区间折算年减排量」（核查报告口径），
            # 分母是报告表单填的区间天数。十年口径的预估值不走这里，
            # 由 _build_legacy_placeholders 按 core/projection 的统一模型另算
            # —— 拿十年计入期当分母会把日均稀释几十倍。
            return {
                "total_records": total_records,
                "total_reduction": round(total_reduction, 6),
                "days": days,
                "daily_avg": round(daily_avg, 6),
                "annual_estimate": round(annual_estimate, 4),
                "latest_breakdown": latest["breakdown"] if latest else None,
                "cumulative_reduction_end": latest["cumulative_reduction"] if latest else None,
            }
        finally:
            conn.close()

    @classmethod
    def _build_default_word_content(cls, doc, placeholders: dict):
        """当没有模板时，生成默认的 Word 文档结构"""
        doc.add_heading(f"碳减排项目核查报告", 1)
        doc.add_paragraph(f"企业名称：{{{{enterprise_name}}}}")
        doc.add_paragraph(f"项目名称：{{{{project_name}}}}")
        doc.add_paragraph(f"项目编号：{{{{project_code}}}}")
        doc.add_paragraph(f"核查期间：{{{{report_start_date}}}} 至 {{{{report_end_date}}}}")
        doc.add_paragraph(f"报告生成时间：{{{{report_generated_at}}}}")
        doc.add_heading("一、项目基本信息", 2)
        doc.add_paragraph(f"项目类型：{{{{project_type}}}}")
        doc.add_paragraph(f"基准日期：{{{{baseline_date}}}}")
        doc.add_paragraph(f"项目简介：{{{{project_summary}}}}")
        doc.add_heading("二、减排量统计", 2)
        doc.add_paragraph(f"核查期间监测记录数：{{{{total_records}}}} 条")
        doc.add_paragraph(f"核查期间总减排量：{{{{total_reduction}}}} tCO₂e")
        doc.add_paragraph(f"日均减排量：{{{{daily_avg_reduction}}}} tCO₂e")
        doc.add_paragraph(f"预估年减排量：{{{{annual_estimate}}}} tCO₂e")
        doc.add_paragraph(f"累计核证减排量：{{{{cumulative_reduction}}}} tCO₂e")
        doc.add_heading("三、碳资产概况", 2)
        doc.add_paragraph(f"总碳资产：{{{{total_carbon_assets}}}} tCO₂e")
        doc.add_paragraph(f"可用碳资产(A)：{{{{available_assets_gross}}}} tCO₂e")
        doc.add_paragraph(f"可用碳资产(B)：{{{{available_assets_net}}}} tCO₂e")
        doc.add_paragraph(f"已交易量：{{{{traded_amount}}}} tCO₂e")
        doc.add_heading("四、计算公式", 2)
        doc.add_paragraph("{{formula_text}}")
        doc.add_paragraph(f"电网排放因子：{{{{ef_grid}}}} tCO₂/MWh")
        doc.add_heading("五、项目阶段", 2)
        doc.add_paragraph(f"阶段1-申请：{{{{stage1_status}}}}")
        doc.add_paragraph(f"阶段2-审定：{{{{stage2_status}}}}")
        doc.add_paragraph(f"阶段3-核查：{{{{stage3_status}}}}")
        doc.add_paragraph(f"阶段4-交易：{{{{stage4_status}}}}")
        doc.add_paragraph(f"阶段5-抵消：{{{{stage5_status}}}}")
        doc.add_heading("六、计算分解", 2)
        doc.add_paragraph("{{breakdown_summary}}")
