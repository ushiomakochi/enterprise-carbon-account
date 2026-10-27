"""
热泵替代化石燃料碳减排计算公式

适用项目类型: 热泵替代燃煤/燃气/燃油锅炉的供热项目
参考方法学: CDM AMS-II.C — 高效热泵替代化石燃料

═══════════════════════════════════════════════════════════════

核心公式:
    减排量 = Q_heat × (EF_fuel / COP_old - EF_grid / COP_hp)

参数说明:
    Q_heat      — Δt 时间内热泵提供的热输出 (GJ)
    EF_fuel     — 被替代化石燃料排放因子 (tCO₂e/GJ)
    COP_old     — 被替代锅炉系统能效比（锅炉 COP 取 1 或 η_boiler）
    EF_grid     — 电网排放因子 (tCO₂e/MWh)
    COP_hp      — 热泵能效比

═══════════════════════════════════════════════════════════════

说明:
    COP (Coefficient of Performance) = 输出热量 / 输入电量
    空气源热泵典型 COP: 2.5~4.0
    地源热泵典型 COP: 3.5~5.0

    由于电网排放因子单位是 tCO₂e/MWh，需要换算:
    1 MWh = 3.6 GJ
    所以 EF_grid (tCO₂e/GJ) = EF_grid (tCO₂e/MWh) / 3.6

公式参数 (formula_params):
    heat_pump_type       — 热泵类型: air_source(空气源) / ground_source(地源) / water_source(水源)
    heating_capacity     — 额定制热量 (kW)
    cop_value            — 热泵 COP 值，默认 3.5
    replaced_fuel_type   — 被替代燃料: coal(燃煤) / gas(天然气) / oil(燃油)
    old_boiler_efficiency — 旧锅炉效率 (%)，默认 75

原始数据字段 (raw_data):
    cumulative_heat_output   — 累计热输出 (MJ)，来自热泵控制器累计值
    cumulative_electricity   — 累计耗电量 (kWh)，来自电表累计值
"""

from .base_hook import BaseCarbonFormula


class HeatPumpFormula(BaseCarbonFormula):
    formula_name = "heat_pump"
    formula_label = "热泵替代碳减排"
    formula_version = "1.0"
    description = ("适用于热泵替代传统化石燃料锅炉的碳减排量计算。"
                   "包括空气源、地源、水源热泵等类型。"
                   "热泵利用少量电能提取环境热量，COP 通常在 2.5~5.0，"
                   "比直接电热或化石燃料供热减排效果显著。"
                   "参考 CDM AMS-II.C 方法学。")

    required_raw_fields = ["cumulative_heat_output", "cumulative_electricity"]
    required_formula_params = ["heat_pump_type", "cop_value", "replaced_fuel_type"]

    EF_GRID = 0.5777  # tCO₂e/MWh

    # 化石燃料排放因子 (tCO₂e/GJ)
    FUEL_EF = {
        "coal": 0.098,
        "oil": 0.077,
        "gas": 0.056,
    }

    def __init__(self, formula_params, vcer_api_url):
        super().__init__(formula_params, vcer_api_url)
        fuel = self.formula_params.get("replaced_fuel_type", "gas")
        self._ef_fuel = self.FUEL_EF.get(fuel, self.FUEL_EF["gas"])
        self._cop = float(self.formula_params.get("cop_value", 3.5))
        self._old_eff = float(self.formula_params.get("old_boiler_efficiency", 75)) / 100.0

        # 电网排放因子: tCO₂e/MWh → tCO₂e/GJ
        self._ef_grid_gj = self.EF_GRID / 3.6

    def get_formula_text(self) -> str:
        return (
            "减排量 = Q_heat × (EF_fuel / η_old - EF_grid / COP_hp / 3.6)\n\n"
            "其中:\n"
            "  Q_heat      = Δt 内热泵提供的热量 (GJ)\n"
            "  EF_fuel     = 被替代燃料排放因子 (tCO₂e/GJ)\n"
            "  η_old       = 旧锅炉效率（化石燃料锅炉 COP ≈ η）\n"
            "  EF_grid     = 电网排放因子 (tCO₂e/MWh)\n"
            "  COP_hp      = 热泵能效比\n"
            "  3.6         = MWh → GJ 换算系数\n\n"
            "说明: 第一部分 = 旧锅炉排放强度，第二部分 = 热泵排放强度，\n"
            "两者之差乘以供热量即为减排量。COP 越高，减排效果越好。\n"
            "参考 CDM AMS-II.C「高效热泵替代化石燃料」方法学。"
        )

    def get_parameter_descriptions(self) -> dict:
        return {
            "heat_pump_type": "热泵类型 (air_source=空气源, ground_source=地源, water_source=水源)",
            "heating_capacity": "额定制热量 (kW)",
            "cop_value": "热泵 COP 值 (典型值: 2.5~5.0)",
            "replaced_fuel_type": "被替代燃料 (coal=燃煤, gas=天然气, oil=燃油)",
            "old_boiler_efficiency": "旧锅炉热效率 (%，默认 75)",
            "cumulative_heat_output": "累计热输出 (MJ) — 来自热泵控制器累计值",
            "cumulative_electricity": "累计耗电量 (kWh) — 来自电表累计值",
            **{f"ef_{k}": f"{k} 排放因子: {v} tCO₂e/GJ" for k, v in self.FUEL_EF.items()},
            "ef_grid_gj": f"电网排放因子: {self.EF_GRID} tCO₂e/MWh = {round(self._ef_grid_gj, 4)} tCO₂e/GJ",
        }

    def fetch_baseline_factors(self, raw_data=None):
        """不依赖外部 API"""
        return {
            "ef_fuel": self._ef_fuel,
            "cop": self._cop,
            "old_efficiency": self._old_eff,
            "ef_grid_gj": self._ef_grid_gj,
            "old_emission_intensity": round(self._ef_fuel / self._old_eff, 6)
            if self._old_eff > 0 else 0,
            "heat_pump_emission_intensity": round(self._ef_grid_gj / self._cop, 6)
            if self._cop > 0 else 0,
        }

    def calculate(self, raw_data, cumulative_data):
        """
        执行一次碳减排计算（增量法）。

        reduction = ΔQ × (EF_fuel / η_old - EF_grid / COP / 3.6)
        """
        # ── 1. 提取数据 ──
        current_heat = float(raw_data.get("cumulative_heat_output", 0))
        current_elec = float(raw_data.get("cumulative_electricity", 0))

        # ── 2. 增量 ──
        prev_heat = float(cumulative_data.get("prev_heat_output", 0))
        prev_elec = float(cumulative_data.get("prev_electricity", 0))

        delta_heat = current_heat - prev_heat      # MJ
        delta_elec = current_elec - prev_elec      # kWh

        if delta_heat < 0:
            delta_heat = 0
        if delta_elec < 0:
            delta_elec = 0

        # ── 3. MJ → GJ ──
        delta_heat_gj = delta_heat / 1000.0

        # ── 4. 旧锅炉排放强度 ──
        if self._old_eff > 0:
            old_intensity = self._ef_fuel / self._old_eff
        else:
            old_intensity = 0

        # ── 5. 热泵排放强度 = EF_grid (tCO₂e/GJ) / COP ──
        if self._cop > 0:
            hp_intensity = self._ef_grid_gj / self._cop
        else:
            hp_intensity = 0

        # ── 6. 如果用了 delta_elec，也可以从实测 COP 核实 ──
        # 实测 COP = delta_heat / 1000 / (delta_elec / 1000 / 3.6)
        #         = delta_heat (GJ) / delta_elec (MWh) / 3.6
        # 这里主要用公式参数 COP 计算，同时输出实测 COP 供参考
        if delta_elec > 0 and delta_heat > 0:
            observed_cop = (delta_heat / 1000.0) / (delta_elec / 1000.0 * 3.6)
        else:
            observed_cop = 0

        # ── 7. 减排量 ──
        reduction = delta_heat_gj * (old_intensity - hp_intensity)
        if reduction < 0:
            reduction = 0

        prev_cumulative = float(cumulative_data.get("cumulative_reduction", 0))
        cumulative = prev_cumulative + reduction

        return {
            "carbon_reduction": round(reduction, 6),
            "cumulative_reduction": round(cumulative, 6),
            "breakdown": {
                "delta_heat_mj": round(delta_heat, 2),
                "delta_heat_gj": round(delta_heat_gj, 4),
                "delta_electricity_kwh": round(delta_elec, 2),
                "cop_nominal": self._cop,
                "cop_observed": round(observed_cop, 3),
                "old_emission_intensity": round(old_intensity, 6),
                "hp_emission_intensity": round(hp_intensity, 6),
                "ef_fuel": self._ef_fuel,
                "ef_grid_gj": round(self._ef_grid_gj, 6),
                "formula": (
                    f"{round(delta_heat_gj, 4)} GJ × "
                    f"({self._ef_fuel}/{round(self._old_eff, 4)} - "
                    f"{round(self._ef_grid_gj, 4)}/{self._cop})"
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
        """热泵替代：被替代化石燃料一行，热泵用电一行"""
        breakdown = monitoring.get("latest_breakdown", {})
        if not isinstance(breakdown, dict):
            breakdown = {}

        old_emissions = breakdown.get("old_emissions_tco2", 0)
        new_emissions = breakdown.get("new_emissions_tco2", 0)
        heat_output = breakdown.get("delta_heat_gj", 0)
        power_used = breakdown.get("delta_power_kwh", 0)
        fuel_type = self.formula_params.get("replaced_fuel_type", "coal")
        fuel_labels = {"coal": "燃煤", "gas": "天然气", "oil": "燃油"}

        return [
            {
                "seq": 1,
                "name": f"替代{fuel_labels.get(fuel_type, fuel_type)}锅炉（避免的排放）",
                "activity_data": f"{heat_output:,.2f} GJ 供热",
                "emission_factor": f"{self._ef_fuel:.6f} tCO₂e/GJ" if hasattr(self, '_ef_fuel') and self._ef_fuel else "—",
                "emission_amount": f"{old_emissions:,.4f} tCO₂e",
            },
            {
                "seq": 2,
                "name": "热泵用电（项目排放，扣除）",
                "activity_data": f"{power_used:,.2f} kWh",
                "emission_factor": f"{self.EF_GRID} tCO₂e/MWh",
                "emission_amount": f"{-new_emissions:,.4f} tCO₂e" if new_emissions else "—",
            },
        ]
