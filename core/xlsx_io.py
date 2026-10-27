"""
极简 XLSX 读写（纯标准库，零依赖）。

XLSX 本质是 zip 包，内含 XML。这里实现最常用的两类操作：
- 写：把二维表格（list[list]）写成 .xlsx（文本单元格 + 简单样式可选）
- 读：把 .xlsx 解析回二维表格（list[list]，单元格值为字符串）

不依赖 openpyxl/pandas，生产环境无需额外安装。

注意：读取时所有单元格返回字符串（数字也转字符串），日期单元格
可能返回 Excel 序列号或格式化字符串，调用方自行处理。
"""

import os
import re
import zipfile
from xml.etree import ElementTree as ET

_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_ET_NS = {"m": _XLSX_NS}


def _esc_xml(s):
    return (str(s)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _col_letter(idx):
    """0 → A, 25 → Z, 26 → AA"""
    s = ""
    n = idx
    while n >= 0:
        s = chr(65 + (n % 26)) + s
        n = n // 26 - 1
    return s


def write_xlsx(rows, path):
    """把二维数组 rows（list[list]，元素转 str）写成 .xlsx。

    参数:
        rows: 二维数组
        path: 输出文件路径（.xlsx）
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    # 计算 sheet 尺寸
    nrows = len(rows)
    ncols = max((len(r) for r in rows), default=0)

    # ── sheet1.xml ──
    sheet_xml = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                 f'<worksheet xmlns="{_XLSX_NS}"><sheetData>']
    for ri, row in enumerate(rows, start=1):
        sheet_xml.append(f'<row r="{ri}">')
        for ci in range(ncols):
            val = row[ci] if ci < len(row) else ""
            cell_ref = f"{_col_letter(ci)}{ri}"
            if val == "" or val is None:
                sheet_xml.append(f'<c r="{cell_ref}"/>')
            else:
                sheet_xml.append(
                    f'<c r="{cell_ref}" t="inlineStr"><is><t>{_esc_xml(val)}</t></is></c>')
        sheet_xml.append('</row>')
    sheet_xml.append('</sheetData></worksheet>')

    # ── [Content_Types].xml ──
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     '</Types>')

    # ── workbook.xml ──
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<workbook xmlns="{_XLSX_NS}" xmlns:r="{_R_NS}">'
                '<sheets><sheet name="预设批量编辑" sheetId="1" r:id="rId1"/></sheets>'
                '</workbook>')

    # ── rels ──
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
               '</Relationships>')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        zf.writestr("xl/worksheets/sheet1.xml", "".join(sheet_xml))


def read_xlsx(path):
    """读取 .xlsx，返回二维数组（list[list]，单元格为字符串）。

    支持 inlineStr（我们写出的）和共享字符串表（sharedStrings.xml）。
    """
    result = []
    with zipfile.ZipFile(path, "r") as zf:
        # 共享字符串表
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{{{_XLSX_NS}}}si"):
                text = "".join(t.text or "" for t in si.iter(f"{{{_XLSX_NS}}}t"))
                shared.append(text)

        # 找 sheet
        sheet_name = None
        for name in zf.namelist():
            if re.match(r"xl/worksheets/sheet\d+\.xml", name):
                sheet_name = name
                break
        if not sheet_name:
            return result

        root = ET.fromstring(zf.read(sheet_name))
        rows_el = root.find(f"{{{_XLSX_NS}}}sheetData")
        if rows_el is None:
            return result

        max_col = 0
        parsed_rows = []
        for row_el in rows_el.findall(f"{{{_XLSX_NS}}}row"):
            cells = {}
            for c in row_el.findall(f"{{{_XLSX_NS}}}c"):
                ref = c.get("r") or ""
                m = re.match(r"([A-Z]+)(\d+)", ref)
                if not m:
                    continue
                col_idx = 0
                for ch in m.group(1):
                    col_idx = col_idx * 26 + (ord(ch) - 64)
                col_idx -= 1  # 0-based

                t = c.get("t")
                val = ""
                if t == "inlineStr":
                    is_el = c.find(f"{{{_XLSX_NS}}}is")
                    if is_el is not None:
                        val = "".join(x.text or "" for x in is_el.iter(f"{{{_XLSX_NS}}}t"))
                elif t == "s":
                    v_el = c.find(f"{{{_XLSX_NS}}}v")
                    if v_el is not None and v_el.text:
                        try:
                            val = shared[int(v_el.text)]
                        except (ValueError, IndexError):
                            val = ""
                else:
                    v_el = c.find(f"{{{_XLSX_NS}}}v")
                    if v_el is not None:
                        val = v_el.text or ""
                cells[col_idx] = val
                max_col = max(max_col, col_idx + 1)
            parsed_rows.append((int(row_el.get("r", 0)), cells))

        # 按行号排序并填充
        parsed_rows.sort(key=lambda x: x[0])
        for _, cells in parsed_rows:
            row = [cells.get(i, "") for i in range(max_col)]
            result.append(row)
    return result
