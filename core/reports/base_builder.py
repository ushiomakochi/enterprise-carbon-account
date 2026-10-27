"""
通用 Word 报告构建器 — 基于 docxtpl。

职责:
- 加载 .docx 模板
- 用 Jinja2 上下文渲染
- 保存生成的文档

与 python-docx 直接操作相比:
- 不需要手动处理 Run 拆分（docxtpl 自动合并）
- 支持 {% for %}/{% if %}/{% include %} 等完整 Jinja2 语法
- 支持 {% tr for %} 动态表格行
- 支持 {{ image_var }} 动态图片
"""

import os
from datetime import datetime


class DocxReportBuilder:
    """通用 docxtpl 报告构建器。

    使用方式:
        DocxReportBuilder.render(
            template_path="/path/to/verification.docx",
            context={"project_name": "...", "emission_sources": [...]},
            output_path="/path/to/output.docx"
        )
    """

    @classmethod
    def render(cls, template_path: str, context: dict, output_path: str,
               inline_images: dict = None):
        """
        加载模板、渲染上下文、保存文档。

        参数:
            template_path: .docx 模板文件路径
            context: Jinja2 上下文字典
            output_path: 输出文件路径
            inline_images: 可选，图片变量映射。支持两种 key：
                           - 顶层变量名 "logo" → context["logo"]
                           - 点路径 "preset.logo" / "project_preset.site_photo"
                             → 递归注入到嵌套 dict 的对应位置
                           值为图片绝对路径，自动转为 InlineImage 对象。

        异常:
            ImportError: 未安装 docxtpl
            FileNotFoundError: 模板文件不存在
        """
        from docxtpl import DocxTemplate

        if not os.path.exists(template_path):
            raise FileNotFoundError(f"模板文件不存在: {template_path}")

        doc = DocxTemplate(template_path)
        # 底层 Document 只取一次；render 后再调 get_docx() 会因 is_rendered=True 而重载模板，故先缓存
        _docx = doc.get_docx()

        # 计算页面可用宽度（EMU），用于图片缩放
        usable_width = None
        try:
            section = _docx.sections[-1]
            usable_width = section.page_width - section.left_margin - section.right_margin
        except Exception:
            usable_width = None

        # 处理内联图片：将路径转为 InlineImage 对象（支持点路径嵌套）
        if inline_images:
            from docxtpl import InlineImage
            for var_name, image_path in inline_images.items():
                if not (image_path and os.path.exists(image_path)):
                    continue
                try:
                    img = InlineImage(doc, image_path,
                                      width=cls._fit_image_width(image_path, usable_width))
                except Exception:
                    continue
                # 支持点路径嵌套：preset.logo / project_preset.site_photo
                if "." in var_name:
                    parts = var_name.split(".")
                    node = context
                    for part in parts[:-1]:
                        if not isinstance(node, dict):
                            node = None
                            break
                        node = node.get(part)
                        if not isinstance(node, dict):
                            node = None
                            break
                    if isinstance(node, dict):
                        node[parts[-1]] = img
                else:
                    context[var_name] = img

        old_inline_count = len(_docx.inline_shapes)
        doc.render(context)
        cls._center_new_images(_docx, old_inline_count)
        cls._inject_tables(_docx, context.get("_table_data"))
        doc.save(output_path)

    @staticmethod
    def _fit_image_width(image_path, usable_width_emu):
        """只收缩不放大：图片原始宽度超过可用宽度时返回可用宽度（锁宽高比），否则 None（保持原尺寸）。"""
        if not usable_width_emu:
            return None
        try:
            from PIL import Image as PILImage
            with PILImage.open(image_path) as im:
                px_w = im.size[0]
                dpi = im.info.get("dpi", (72, 72))
                if isinstance(dpi, (tuple, list)):
                    dpi_x = dpi[0] if len(dpi) > 0 and dpi[0] else 72
                else:
                    dpi_x = dpi or 72
            natural_emu = int(round(px_w / float(dpi_x) * 914400))
        except Exception:
            # 读不到尺寸时按可用宽度收缩，避免溢出
            return usable_width_emu
        return usable_width_emu if natural_emu > usable_width_emu else None

    @staticmethod
    def _inject_tables(docx, table_data):
        """把占位符段落 [TABLE:key] 替换成真实表格（渲染后注入）。

        table 类型预设渲染时返回占位符字符串，本方法在此处用 python-docx
        在占位段落位置插入真正的带框线表格，再删除占位段落。

        样式对齐已备案 PDD 案例（首个接入企业的审定文档）：
        - 表格整体居中、单元格垂直居中
        - 表头行加粗 + D9D9D9 底纹
        - 汇总行（合计 / 计入期年数 / 计入期内年均值）加 D9D9D9 底纹
        - 正文 Times New Roman + 中文宋体、10.5pt
        """
        if not table_data:
            return
        try:
            from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.oxml.ns import qn
            from docx.oxml import OxmlElement
            from docx.shared import Pt
        except Exception:
            return

        def _shade(cell, fill):
            """给单元格加底纹。"""
            try:
                tcPr = cell._tc.get_or_add_tcPr()
                shd = tcPr.find(qn('w:shd'))
                if shd is None:
                    shd = OxmlElement('w:shd')
                    tcPr.append(shd)
                shd.set(qn('w:val'), 'clear')
                shd.set(qn('w:color'), 'auto')
                shd.set(qn('w:fill'), fill)
            except Exception:
                pass

        def _style_cell(cell, bold=False, center=True):
            """设置字体（含中文 eastAsia）与对齐；段前段后收一点，免得行太高。"""
            try:
                cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            except Exception:
                pass
            for para in cell.paragraphs:
                if center:
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                try:
                    para.paragraph_format.space_before = Pt(2)
                    para.paragraph_format.space_after = Pt(2)
                except Exception:
                    pass
                for run in para.runs:
                    run.font.size = Pt(10.5)
                    run.font.bold = bold
                    run.font.name = "Times New Roman"
                    try:
                        rPr = run._element.get_or_add_rPr()
                        rFonts = rPr.find(qn('w:rFonts'))
                        if rFonts is None:
                            rFonts = OxmlElement('w:rFonts')
                            rPr.append(rFonts)
                        rFonts.set(qn('w:ascii'), 'Times New Roman')
                        rFonts.set(qn('w:hAnsi'), 'Times New Roman')
                        rFonts.set(qn('w:eastAsia'), '宋体')
                    except Exception:
                        pass

        try:
            for p in list(docx.paragraphs):
                text = (p.text or "").strip()
                if not (text.startswith("[TABLE:") and text.endswith("]")):
                    continue
                key = text[len("[TABLE:"):-1]
                td = table_data.get(key) or {}
                headers = td.get("headers") or []
                rows = td.get("rows") or []
                # [[行下标, 是否加粗], ...]；行下标是相对 rows（不含表头）的
                summary_map = {int(i): bool(b) for i, b in (td.get("summary_rows") or [])}
                if not headers:
                    # 该表没有可用数据：删掉占位段落，避免 "[TABLE:xxx]" 字样留在报告里
                    p._p.getparent().remove(p._p)
                    continue
                table = docx.add_table(rows=1, cols=len(headers))
                table.style = "Table Grid"
                try:
                    table.alignment = WD_TABLE_ALIGNMENT.CENTER
                except Exception:
                    pass

                # 表头行
                for j, h in enumerate(headers):
                    cell = table.cell(0, j)
                    cell.text = str(h)
                    _style_cell(cell, bold=True)
                    _shade(cell, "D9D9D9")

                # 数据行（summary_map 的下标相对 rows，不含表头）
                for i, r in enumerate(rows):
                    cells = table.add_row().cells
                    is_summary = i in summary_map
                    for j in range(len(headers)):
                        cells[j].text = str(r[j]) if j < len(r) else ""
                        _style_cell(cells[j], bold=summary_map.get(i, False))
                        if is_summary:
                            _shade(cells[j], "D9D9D9")

                # 移到占位段落之后，再删除占位段落
                p._p.addnext(table._tbl)
                p._p.getparent().remove(p._p)
        except Exception:
            pass

    @staticmethod
    def _center_new_images(docx, old_count):
        """把本次 render 新增的内联图片所在段落设为居中（docx 为已渲染的底层 Document）。"""
        try:
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.text.paragraph import Paragraph
            for shape in list(docx.inline_shapes)[old_count:]:
                try:
                    p_el = shape._inline.getparent().getparent().getparent()
                    if p_el is not None and p_el.tag.endswith("}p"):
                        Paragraph(p_el, docx).alignment = WD_ALIGN_PARAGRAPH.CENTER
                except Exception:
                    continue
        except Exception:
            pass

    @classmethod
    def render_with_richtext(cls, template_path: str, context: dict, output_path: str):
        """
        渲染模板（含富文本支持）。

        与 render() 的区别：
        - 支持 RichText 对象用于在段落内混合格式
        - 支持 InlineImage 对象用于内联图片
        - 速度略慢于 render()
        """
        from docxtpl import DocxTemplate, RichText, InlineImage

        if not os.path.exists(template_path):
            raise FileNotFoundError(f"模板文件不存在: {template_path}")

        doc = DocxTemplate(template_path)
        doc.render(context)
        doc.save(output_path)

    @classmethod
    def render_from_db_template(cls, db_template: dict, context: dict, output_path: str):
        """
        从数据库模板记录渲染（兼容旧的上传模板功能）。

        参数:
            db_template: 数据库模板记录，需包含 file_path
            context: Jinja2 上下文
            output_path: 输出文件路径
        """
        template_path = db_template.get("file_path", "")
        if not template_path or not os.path.exists(template_path):
            raise FileNotFoundError(f"数据库模板文件不存在: {template_path}")
        return cls.render(template_path, context, output_path)

    @classmethod
    def create_template_from_html(cls, html_content: str, output_path: str):
        """
        从 HTML 内容创建 .docx 模板（用于快速原型）。

        注意：这是辅助方法，排版效果不如直接在 Word 中制作模板。
        推荐做法：在 Word 中手动创建模板，使用 Jinja2 语法标记占位符。
        """
        try:
            from docx import Document
            from docx.shared import Inches, Pt, Cm
        except ImportError:
            raise ImportError("未安装 python-docx")

        doc = Document()

        # 将 HTML 粗略地转换为段落
        # 注：这只是一种快速原型方式，复杂的表格/样式请直接在 Word 中设计
        lines = html_content.replace("<p>", "\n").replace("</p>", "\n")
        lines = lines.replace("<br>", "\n").replace("<br/>", "\n")

        for line in lines.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("<h1>"):
                doc.add_heading(line.replace("<h1>", "").replace("</h1>", ""), level=1)
            elif line.startswith("<h2>"):
                doc.add_heading(line.replace("<h2>", "").replace("</h2>", ""), level=2)
            elif line.startswith("<h3>"):
                doc.add_heading(line.replace("<h3>", "").replace("</h3>", ""), level=3)
            else:
                doc.add_paragraph(line)

        doc.save(output_path)
        return output_path
