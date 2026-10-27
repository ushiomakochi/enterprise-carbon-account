"""
节能电机/变频器碳减排计算公式

适用项目类型: 电机系统节能改造（变频调速、高效电机替换等）
参考方法学: CDM AMS-II.D — 工业设施能效提高

═══════════════════════════════════════════════════════════════

核心公式:
    减排量 = (P_baseline - P_actual) × Operating_Hours × EF_grid / 1000

参数说明:
    P_baseline          — 改造前基准线功率 (kW)
    P_actual            — 改造后实际功率 (kW)
    Operating_Hours     — Δt 时间段内的运行小时数
    EF_grid             — 电网排放因子 (tCO₂e/MWh)

═══════════════════════════════════════════════════════════════

简化方案（增量法，使用累计电度差）:
    减排量 = (ΔE_baseline - ΔE_actual) × EF_grid

其中:
    ΔE_baseline = ΔE_actual / η_actual × η_baseline  （推算基准线电耗）

所以:
    减排量 = ΔE_actual × (1/η_actual × η_baseline - 1) × EF_grid

公式参数 (formula_params):
    motor_rated_power   — 电机额定功率 (kW)
    motor_count         — 改造数量 (台)
    baseline_efficiency — 改造前系统效率 (%)，默认 65
    actual_efficiency   — 改造后系统效率 (%)，默认 85
    annual_operating_hours — 年运行小时数 (h)，用于预估年减排量

原始数据字段 (raw_data):
    cumulative_energy_actual — 累计实际用电量 (kWh)，来自变频器/电表累计值
"""

from .base_hook import BaseCarbonFormula


class EnergySavingMotorFormula(BaseCarbonFormula):
    formula_name = "energy_saving_motor"
    formula_label = "节能电机/变频器碳减排"
    formula_version = "1.0"
    description = ("适用于电机系统节能改造项目的碳减排量计算。"
                   "包括变频调速改造、高效电机替换等。"
                   "通过比较改造前后电机系统效率的差异，"
                   "计算项目实现的碳减排量。参考 CDM AMS-II.D 方法学。")

    required_raw_fields = ["cumulative_energy_actual"]
    required_formula_params = ["motor_rated_power", "baseline_efficiency", "actual_efficiency"]

    EF_GRID = 0.5777  # tCO₂e/MWh

    def __init__(self, formula_params, vcer_api_url):
        super().__init__(formula_params, vcer_api_url)
        self._baseline_eff = float(self.formula_params.get("baseline_efficiency", 65)) / 100.0
        self._actual_eff = float(self.formula_params.get("actual_efficiency", 85)) / 100.0

    def get_formula_text(self) -> str:
        return (
            "减排量 = ΔE_actual × (η_baseline / η_actual - 1) × EF_grid\n\n"
            "其中:\n"
            "  ΔE_actual     = Δt 内实际用电量 (MWh)\n"
            "  η_baseline    = 改造前系统效率\n"
            "  η_actual      = 改造后系统效率\n"
            "  EF_grid       = 电网排放因子 (tCO₂e/MWh)\n\n"
            "说明: 电机系统效率提升后，同等工作量下用电量减少。\n"
            "减排量 = 节省的电量 × 电网排放因子。\n"
            "参考 CDM AMS-II.D「工业设施能效提高」方法学。"
        )

    def get_parameter_descriptions(self) -> dict:
        return {
            "motor_rated_power": "电机额定功率 (kW)",
            "motor_count": "改造数量 (台)",
            "baseline_efficiency": "改造前系统综合效率 (%，默认 65)",
            "actual_efficiency": "改造后系统综合效率 (%，默认 85)",
            "annual_operating_hours": "年运行小时数 (h)",
            "cumulative_energy_actual": "累计实际用电量 (kWh) — 来自变频器/电表累计值",
            "ef_grid": f"电网排放因子: {self.EF_GRID} tCO₂e/MWh (2024年)",
        }

    def fetch_baseline_factors(self, raw_data=None):
        """不依赖外部 API"""
        return {
            "baseline_efficiency": self._baseline_eff,
            "actual_efficiency": self._actual_eff,
            "efficiency_gain": round((1 / self._actual_eff - 1 / self._baseline_eff) * -1, 4)
            if self._baseline_eff > 0 and self._actual_eff > 0 else 0,
        }

    def calculate(self, raw_data, cumulative_data):
        """
        执行一次碳减排计算（增量法）。

        reduction = ΔE_actual × (η_baseline / η_actual - 1) × EF_grid
        """
        # ── 1. 提取数据 ──
        current_energy = float(raw_data.get("cumulative_energy_actual", 0))

        # ── 2. 增量 ──
        prev_energy = float(cumulative_data.get("prev_energy_actual", 0))
        delta_energy = current_energy - prev_energy  # kWh

        if delta_energy < 0:
            delta_energy = 0

        # ── 3. 效率比 ──
        if self._actual_eff > 0 and self._baseline_eff > 0:
            efficiency_ratio = self._baseline_eff / self._actual_eff
            savings_factor = efficiency_ratio - 1  # 节电比例
        else:
            efficiency_ratio = 0
            savings_factor = 0

        # ── 4. 减排量 = 实际用电 × (效率比-1) × EF_grid ──
        # kWh → MWh 转换
        delta_energy_mwh = delta_energy / 1000.0
        reduction = delta_energy_mwh * savings_factor * self.EF_GRID
        if reduction < 0:
            reduction = 0

        prev_cumulative = float(cumulative_data.get("cumulative_reduction", 0))
        cumulative = prev_cumulative + reduction

        # ── 5. 推算节省电量 ──
        energy_saved_mwh = delta_energy_mwh * savings_factor if savings_factor > 0 else 0

        return {
            "carbon_reduction": round(reduction, 6),
            "cumulative_reduction": round(cumulative, 6),
            "breakdown": {
                "delta_energy_kwh": round(delta_energy, 2),
                "delta_energy_mwh": round(delta_energy_mwh, 6),
                "baseline_efficiency": round(self._baseline_eff, 4),
                "actual_efficiency": round(self._actual_eff, 4),
                "efficiency_ratio": round(efficiency_ratio, 4),
                "energy_saved_mwh": round(energy_saved_mwh, 6),
                "ef_grid": self.EF_GRID,
                "formula": (
                    f"{round(delta_energy_mwh, 6)} MWh × "
                    f"({round(self._baseline_eff, 4)}/{round(self._actual_eff, 4)} - 1) × "
                    f"{self.EF_GRID}"
                ),
            },
        }

    def estimate_annual(self, period_data):
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
        """节能电机：一种排放源（节电量 × 电网排放因子）"""
        breakdown = monitoring.get("latest_breakdown", {})
        if not isinstance(breakdown, dict):
            breakdown = {}

        energy_saved = breakdown.get("energy_saved_kwh", 0)
        ef_grid = breakdown.get("ef_grid", 0.5777)

        return [{
            "seq": 1,
            "name": "电机系统节电（替代电网电力）",
            "activity_data": f"{energy_saved:,.2f} kWh 节电量",
            "emission_factor": f"{ef_grid} tCO₂e/MWh",
            "emission_amount": f"{monitoring.get('total_reduction', 0):,.4f} tCO₂e",
        }]
