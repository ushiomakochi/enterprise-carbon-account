"""
余热回收碳减排计算公式

适用项目类型: 工业余热回收利用项目（烟气余热、废水余热、工艺余热等）
参考方法学: CDM ACM0012 — 废能回收利用

═══════════════════════════════════════════════════════════════

核心公式:
    减排量 = Q_recovered × EF_thermal

参数说明:
    Q_recovered     — Δt 内回收的余热量 (GJ)
    EF_thermal      — 基准热源排放因子 (tCO₂e/GJ)
                      取决于被替代的热源类型（燃煤/燃气/电热）

═══════════════════════════════════════════════════════════════

公式参数 (formula_params):
    heat_source_type    — 余热来源: flue_gas(烟气) / waste_water(废水) / process(工艺)
    replaced_fuel_type  — 被替代热源: coal(燃煤) / gas(天然气) / electric(电热)
    boiler_efficiency   — 被替代锅炉效率 (%)，默认 75
    operating_temp      — 余热回收温度 (℃)

原始数据字段 (raw_data):
    cumulative_heat_recovered — 累计回收热量 (MJ)，来自热表累计值
"""

from .base_hook import BaseCarbonFormula


class WasteHeatRecoveryFormula(BaseCarbonFormula):
    formula_name = "waste_heat_recovery"
    formula_label = "余热回收碳减排"
    formula_version = "1.0"
    description = ("适用于工业余热回收利用项目的碳减排量计算。"
                   "通过回收烟气、废水或工艺余热替代传统热源，"
                   "计算项目实现的碳减排量。参考 CDM ACM0012 方法学。")

    required_raw_fields = ["cumulative_heat_recovered"]
    required_formula_params = ["heat_source_type", "replaced_fuel_type", "boiler_efficiency"]

    # ── 不同燃料的排放因子 (tCO₂e/GJ) ──
    # 来源: IPCC 2006 Guidelines + 中国国家温室气体清单
    FUEL_EMISSION_FACTORS = {
        "coal": 0.098,      # 燃煤 ~98 kgCO₂/GJ
        "gas": 0.056,       # 天然气 ~56 kgCO₂/GJ
        "electric": 0.160,  # 电热 ~160 kgCO₂/GJ (基于电网排放因子)
    }

    def __init__(self, formula_params, vcer_api_url):
        super().__init__(formula_params, vcer_api_url)
        fuel = self.formula_params.get("replaced_fuel_type", "coal")
        self._ef_thermal = self.FUEL_EMISSION_FACTORS.get(fuel, self.FUEL_EMISSION_FACTORS["coal"])
        self._boiler_efficiency = float(self.formula_params.get("boiler_efficiency", 75)) / 100.0

    def get_formula_text(self) -> str:
        return (
            "减排量 = Q_recovered / η_boiler × EF_thermal\n\n"
            "其中:\n"
            "  Q_recovered = Δt 内回收的余热量 (GJ)\n"
            "  η_boiler    = 被替代锅炉的热效率\n"
            "  EF_thermal  = 被替代热源的排放因子 (tCO₂e/GJ)\n\n"
            "说明: 余热回收替代了原锅炉的燃料消耗，减排量等于\n"
            "回收热量折算为燃料消耗量再乘以对应排放因子。\n"
            "参考 CDM ACM0012「废能回收利用」方法学。"
        )

    def get_parameter_descriptions(self) -> dict:
        return {
            "heat_source_type": "余热来源 (flue_gas=烟气, waste_water=废水, process=工艺余热)",
            "replaced_fuel_type": "被替代热源类型 (coal=燃煤, gas=天然气, electric=电热)",
            "boiler_efficiency": "被替代锅炉热效率 (%，默认 75)",
            "operating_temp": "余热回收温度 (℃)",
            "cumulative_heat_recovered": "累计回收热量 (MJ) — 来自热表/流量计累计值",
            **{f"ef_{k}": f"{k} 排放因子: {v} tCO₂e/GJ" for k, v in self.FUEL_EMISSION_FACTORS.items()},
        }

    def fetch_baseline_factors(self, raw_data=None):
        """余热回收基准线因子为被替代热源的排放因子（固定值），不依赖外部 API"""
        return {
            "ef_thermal": self._ef_thermal,
            "boiler_efficiency": self._boiler_efficiency,
        }

    def calculate(self, raw_data, cumulative_data):
        """
        执行一次碳减排计算（增量法）。

        reduction = ΔQ / 1000 × (1 / η_boiler) × EF_thermal
        其中 ΔQ 是增量回收热量 (MJ → GJ)
        """
        # ── 1. 提取原始数据 ──
        current_heat = float(raw_data.get("cumulative_heat_recovered", 0))

        # ── 2. 计算增量 (MJ) ──
        prev_heat = float(cumulative_data.get("prev_heat_recovered", 0))
        delta_heat = current_heat - prev_heat

        if delta_heat < 0:
            delta_heat = 0

        # ── 3. MJ → GJ ──
        delta_heat_gj = delta_heat / 1000.0

        # ── 4. 等效燃料消耗 (GJ) = 回收热量 / 锅炉效率 ──
        fuel_displaced = delta_heat_gj / self._boiler_efficiency if self._boiler_efficiency > 0 else 0

        # ── 5. 减排量 ──
        reduction = fuel_displaced * self._ef_thermal

        prev_cumulative = float(cumulative_data.get("cumulative_reduction", 0))
        cumulative = prev_cumulative + reduction

        return {
            "carbon_reduction": round(reduction, 6),
            "cumulative_reduction": round(cumulative, 6),
            "breakdown": {
                "delta_heat_mj": round(delta_heat, 2),
                "delta_heat_gj": round(delta_heat_gj, 6),
                "boiler_efficiency": round(self._boiler_efficiency, 4),
                "fuel_displaced_gj": round(fuel_displaced, 6),
                "ef_thermal": self._ef_thermal,
                "ef_thermal_unit": "tCO₂e/GJ",
                "formula": (
                    f"{round(delta_heat_gj, 6)} GJ / {round(self._boiler_efficiency, 4)} × "
                    f"{self._ef_thermal} tCO₂e/GJ"
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
        """余热回收：替代传统热源一种排放源"""
        breakdown = monitoring.get("latest_breakdown", {})
        if not isinstance(breakdown, dict):
            breakdown = {}

        delta_heat_gj = breakdown.get("delta_heat_gj", 0)
        fuel_displaced = breakdown.get("fuel_displaced_gj", 0)
        fuel_type = self.formula_params.get("replaced_fuel_type", "coal")
        fuel_labels = {"coal": "燃煤", "gas": "天然气", "electric": "电热"}

        return [{
            "seq": 1,
            "name": f"余热回收替代{fuel_labels.get(fuel_type, fuel_type)}",
            "activity_data": f"{delta_heat_gj:,.4f} GJ 回收热量（等效 {fuel_displaced:,.4f} GJ 燃料）",
            "emission_factor": f"{self._ef_thermal} tCO₂e/GJ",
            "emission_amount": f"{monitoring.get('total_reduction', 0):,.4f} tCO₂e",
        }]
