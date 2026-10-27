"""
光伏发电碳减排计算公式

适用项目类型: 分布式/集中式光伏发电项目
参考方法学: CDM ACM0002 — 可再生能源发电并网

═══════════════════════════════════════════════════════════════

核心公式:
    减排量 = EG × EF_grid

参数说明:
    EG         — Δt 时间内光伏系统的发电量 (MWh)
    EF_grid    — 电网排放因子 (tCO₂e/MWh)
                 默认 0.5777，来源: 生态环境部《2024年电力碳足迹因子》

═══════════════════════════════════════════════════════════════

公式参数 (formula_params):
    installed_capacity  — 装机容量 (kW)
    panel_type          — 组件类型: mono(单晶硅) / poly(多晶硅) / thin_film(薄膜)
    degradation_rate    — 年衰减率 (%/年)，默认 0.5
    latitude            — 纬度（用于估算发电量，可选）

原始数据字段 (raw_data):
    cumulative_generation — 累计发电量 (kWh)，来自逆变器/电表累计值
"""

from .base_hook import BaseCarbonFormula


class SolarPVFormula(BaseCarbonFormula):
    formula_name = "solar_pv"
    formula_label = "光伏发电碳减排"
    formula_version = "1.0"
    description = ("适用于分布式或集中式光伏发电项目的碳减排量计算。"
                   "通过光伏发电量替代电网电力的方式，"
                   "计算项目实现的碳减排量。参考 CDM ACM0002 方法学。")

    required_raw_fields = ["cumulative_generation"]
    required_formula_params = ["installed_capacity", "panel_type"]

    # 电网排放因子（2024年，来源：生态环境部）
    EF_GRID = 0.5777  # tCO₂e/MWh

    # 光伏组件默认衰减率
    DEFAULT_DEGRADATION_RATE = 0.5  # %/年

    def __init__(self, formula_params, vcer_api_url):
        super().__init__(formula_params, vcer_api_url)
        # 不需要调用外部 API，基准线因子就是电网排放因子（固定值）
        self._ef_grid = self.EF_GRID

    def get_formula_text(self) -> str:
        return (
            "减排量 = EG × EF_grid\n\n"
            "其中:\n"
            "  EG      = Δt 内光伏系统发电量 (MWh)\n"
            "  EF_grid = 电网排放因子 (tCO₂e/MWh)\n\n"
            "说明: 光伏发电替代电网电力，减排量等于发电量乘以电网排放因子。\n"
            "参照 CDM ACM0002「可再生能源发电并网」方法学。"
        )

    def get_parameter_descriptions(self) -> dict:
        return {
            "installed_capacity": "光伏装机容量 (kW)",
            "panel_type": "组件类型 (mono=单晶硅, poly=多晶硅, thin_film=薄膜)",
            "degradation_rate": f"年衰减率 (%/年，默认 {self.DEFAULT_DEGRADATION_RATE}%)",
            "latitude": "项目所在地纬度（可选，用于估算月发电量）",
            "cumulative_generation": "累计发电量 (kWh) — 来自逆变器/电表累计值",
            "ef_grid": f"电网排放因子: {self.EF_GRID} tCO₂e/MWh (2024年)",
        }

    def fetch_baseline_factors(self, raw_data=None):
        """
        光伏项目的基准线因子即电网排放因子（固定值）。
        不需要调用外部 API。
        """
        # 可选的衰减率
        degradation = float(self.formula_params.get("degradation_rate", self.DEFAULT_DEGRADATION_RATE))
        self._degradation = degradation / 100.0  # 转为小数

        return {
            "ef_grid": self._ef_grid,
            "degradation_rate": degradation,
        }

    def calculate(self, raw_data, cumulative_data):
        """
        执行一次碳减排计算（增量法）。

        用累计发电量的差值来计算 Δt 时间内的减排量:
            ΔEG = current_generation - prev_generation
            reduction = ΔEG / 1000 × EF_grid  (kWh → MWh)
        """
        # ── 1. 提取原始数据 ──
        current_gen = float(raw_data.get("cumulative_generation", 0))

        # ── 2. 计算增量 (kWh) ──
        prev_gen = float(cumulative_data.get("prev_generation", 0))
        delta_gen = current_gen - prev_gen

        # 防御: 累计值回退
        if delta_gen < 0:
            delta_gen = 0

        # ── 3. 减排量 = 增量发电量 (MWh) × 电网排放因子 ──
        delta_gen_mwh = delta_gen / 1000.0
        reduction = delta_gen_mwh * self._ef_grid

        # ── 4. 累计 ──
        prev_cumulative = float(cumulative_data.get("cumulative_reduction", 0))
        cumulative = prev_cumulative + reduction

        return {
            "carbon_reduction": round(reduction, 6),
            "cumulative_reduction": round(cumulative, 6),
            "breakdown": {
                "delta_generation_kwh": round(delta_gen, 2),
                "delta_generation_mwh": round(delta_gen_mwh, 6),
                "ef_grid": self._ef_grid,
                "ef_grid_unit": "tCO₂e/MWh",
                "formula": (
                    f"{round(delta_gen_mwh, 6)} MWh × "
                    f"{self._ef_grid} tCO₂e/MWh"
                ),
            },
        }

    def estimate_annual(self, period_data):
        """根据已有数据预估年度减排量"""
        if not period_data:
            return 0.0

        total = sum(d.get("carbon_reduction", 0) or 0 for d in period_data)
        days = len(period_data)
        if days == 0:
            return 0.0

        if days >= 365:
            recent = period_data[-365:]
            return round(sum(d.get("carbon_reduction", 0) or 0 for d in recent), 2)
        else:
            return round(total / days * 365, 2)

    # ══════════════════════════════════════════════════════════
    # 报告排放源清单
    # ══════════════════════════════════════════════════════════

    def get_emission_sources_for_report(self, monitoring: dict) -> list:
        """光伏项目只有一种排放源：替代电网电力"""
        total_gen_mwh = 0
        breakdown = monitoring.get("latest_breakdown", {})
        if isinstance(breakdown, dict):
            total_gen_mwh = breakdown.get("delta_generation_mwh", 0)
        if not total_gen_mwh and monitoring.get("total_reduction", 0) > 0:
            total_gen_mwh = monitoring.get("total_reduction", 0) / self.EF_GRID

        # 没配才回落到默认因子：填 0 是合法的（全绿电场景），
        # 用 `if ef_grid_override` 会把 0 换成 0.5777，报告里印出错的因子。
        ef_grid_override = self.formula_params.get("ef_grid_override")
        ef = self.EF_GRID if ef_grid_override is None else ef_grid_override

        return [{
            "seq": 1,
            "name": "光伏发电（替代电网电力）",
            "activity_data": f"{total_gen_mwh:,.2f} MWh",
            "emission_factor": f"{ef} tCO₂e/MWh",
            "emission_amount": f"{monitoring.get('total_reduction', 0):,.4f} tCO₂e",
        }]
