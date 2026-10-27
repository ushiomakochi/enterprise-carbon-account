"""
任意日期区间减排量查询函数（供「代码(code)」类型预设调用）。

code 预设里可直接写：
    return round(get_reduction('2022-05-13', '2023-12-31'), 2)
或引用其它预设的日期值：
    return round(get_reduction(project_preset.rd_start, project_preset.rd_end), 2)
"""

import re


def _normalize_date(s):
    """把 YYYY-MM-DD / YYYY/MM/DD / YYYY年M月D日 归一为 YYYY-MM-DD；失败返回 None。"""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    m = re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日', s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.fullmatch(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return None


def get_reduction(project_id, start, end):
    """返回 [start, end] 区间内的累计减排量 tCO₂e（float）。无数据/失败返回 0.0。"""
    s = _normalize_date(start)
    e = _normalize_date(end)
    if not s or not e or not project_id:
        return 0.0
    try:
        from core.project_manager import get_db
        conn = get_db()
        try:
            row = conn.execute(
                """SELECT COALESCE(SUM(carbon_reduction), 0) AS total
                   FROM monitoring_data
                   WHERE project_id = ? AND timestamp >= ? AND timestamp <= ?""",
                (project_id, f"{s} 00:00:00", f"{e} 23:59:59")
            ).fetchone()
            return float(row["total"] or 0) if row else 0.0
        finally:
            conn.close()
    except Exception:
        return 0.0


def make_get_reduction(project_id):
    """返回绑定 project_id 的 get_reduction 闭包，供 code 执行环境注入。"""
    def _fn(start, end):
        return get_reduction(project_id, start, end)
    return _fn
