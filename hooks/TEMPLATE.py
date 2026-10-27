"""
新公式开发模板
═══════════════════════════════════════════════════════════════

使用方法:
    1. 复制此文件，重命名为你的公式名（如 biomass.py）
    2. 修改类名（建议与文件名一致）和 formula_name / formula_label
    3. 实现 fetch_baseline_factors() — 获取基准线因子（不需要则返回 {}）
    4. 实现 calculate() — ★ 核心计算公式
    5. 可选: 覆盖 estimate_annual()、get_formula_text() 等方法
    6. 将文件放入 hooks/ 目录，重启系统即可自动加载

注意事项:
    - formula_name 必须与文件名一致（不含 .py），这是自动发现的标识
    - 所有公式参数通过 formula_params 字典传入
    - 原始数据字段定义在 required_raw_fields 中
    - 计算结果必须包含 carbon_reduction 和 cumulative_reduction
    - 如果公式不需要外部参数，设置 required_formula_params = []
"""

from .base_hook import BaseCarbonFormula


class TemplateFormula(BaseCarbonFormula):
    """
    模板碳减排计算公式

    修改以下属性以适配你的项目类型:
    """
    formula_name = "template"           # ★ 改为唯一标识（必须与文件名一致）
    formula_label = "模板碳减排计算"     # ★ 改为显示名称
    formula_version = "1.0"
    description = "在此描述适用项目类型和计算方法"

    # ★ 定义需要的原始数据字段
    required_raw_fields = ["field_name_1", "field_name_2"]

    # ★ 定义需要的公式参数（不需要外部参数时设为空列表 []）
    required_formula_params = ["param_name_1", "param_name_2"]

    def get_formula_text(self) -> str:
        """返回计算公式的可读文本描述"""
        return "你的公式描述..."

    def get_parameter_descriptions(self) -> dict:
        """返回参数的中文说明"""
        return {
            "param_name_1": "参数1的中文说明",
            "param_name_2": "参数2的中文说明",
            "field_name_1": "数据字段1的说明",
            "field_name_2": "数据字段2的说明",
        }

    def fetch_baseline_factors(self, raw_data=None):
        """
        获取基准线排放因子。

        示例实现:
            1. 从促进会 API 获取
            2. 从本地配置读取
            3. 使用固定系数
            4. 直接返回 {} 表示不需要外部因子

        Returns:
            dict: 基准线因子，如 {"factor_x": 0.95}，不需要则返回 {}
        """
        # TODO: 实现你的基准线因子获取逻辑（不需要则返回 {}）
        return {}

    def calculate(self, raw_data, cumulative_data):
        """
        ★ 核心计算方法

        参数:
            raw_data: 当前原始数据
            cumulative_data: 累计/上期数据

        返回:
            dict: {
                "carbon_reduction": float,       # 本期减排量 tCO₂e
                "cumulative_reduction": float,   # 累计减排量 tCO₂e
                "breakdown": {...}               # 计算过程分解
            }
        """
        # ── 1. 提取原始数据 ──
        # field1 = float(raw_data.get("field_name_1", 0))

        # ── 2. 获取基准线因子（不需要外部参数的公式可跳过）──
        # factors = self.fetch_baseline_factors()

        # ── 3. ★ 你的计算公式 ──
        # reduction = ...  # tCO₂e

        # ── 4. 累计 ──
        # prev_cumulative = float(cumulative_data.get("cumulative_reduction", 0))
        # cumulative = prev_cumulative + reduction

        # ── 5. 返回结果 ──
        return {
            "carbon_reduction": 0,
            "cumulative_reduction": 0,
            "breakdown": {
                "note": "请在此实现你的计算公式",
            },
        }

    def estimate_annual(self, period_data):
        """
        预估年度减排量。
        默认实现: 日均值 × 365。
        可根据项目特点覆盖。
        """
        if not period_data:
            return 0.0
        total = sum(d.get("carbon_reduction", 0) or 0 for d in period_data)
        days = len(period_data)
        if days == 0:
            return 0.0
        return round(total / days * 365, 2)
