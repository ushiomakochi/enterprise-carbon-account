"""
生物质锅炉替代碳减排计算公式

适用项目类型: 生物质锅炉替代燃煤/燃油/燃气锅炉项目
参考方法学: CDM ACM0006 — 生物质发电/供热

═══════════════════════════════════════════════════════════════

核心公式:
    减排量 = Q_steam × (EF_coal / η_coal - EF_biomass / η_biomass)

参数说明:
    Q_steam         — Δt 时间内产出蒸汽的等效热量 (GJ)
    EF_coal         — 被替代化石燃料的排放因子 (tCO₂e/GJ)
    η_coal          — 被替代燃煤锅炉效率
    EF_biomass      — 生物质燃料的排放因子 (tCO₂e/GJ)，通常取 0
    η_biomass       — 生物质锅炉效率

═══════════════════════════════════════════════════════════════

简化说明:
    生物质燃料燃烧产生的 CO₂ 被认为是生物圈碳循环的一部分
    （此前通过光合作用吸收），因此 EF_biomass 通常视为净零。
    减排量主要来自替代化石燃料所避免的排放。

公式参数 (formula_params):
    replaced_fuel_type     — 被替代燃料: coal(燃煤) / oil(燃油) / gas(天然气)
    old_boiler_efficiency  — 旧锅炉效率 (%)，默认 65（典型燃煤锅炉）
    new_boiler_efficiency  — 新生物质锅炉效率 (%)，默认 80
    biomass_type           — 生物质类型: wood_chip(木片) / pellet(颗粒) / straw(秸秆)
    biomass_moisture       — 生物质含水率 (%)，默认 30

原始数据字段 (raw_data):
    cumulative_steam_output    — 累计蒸汽产出量 (吨)
    cumulative_biomass_consumed — 累计生物质消耗量 (吨)
"""

from .base_hook import BaseCarbonFormula


class BiomassBoilerFormula(BaseCarbonFormula):
    formula_name = "biomass_boiler"
    formula_label = "生物质锅炉替代碳减排"
    formula_version = "1.0"
    description = ("适用于生物质锅炉替代传统化石燃料锅炉的碳减排量计算。"
                   "生物质燃料被视为碳中性，减排量等于所替代化石燃料的排放量。"
                   "参考 CDM ACM0006「生物质发电/供热」方法学。")

    required_raw_fields = ["cumulative_steam_output"]
    required_formula_params = ["replaced_fuel_type", "old_boiler_efficiency"]

    # ── 化石燃料排放因子 (tCO₂e/GJ) ──
    FUEL_EF = {
        "coal": 0.098,   # 燃煤
        "oil": 0.077,    # 燃油
        "gas": 0.056,    # 天然气
    }

    # ── 蒸汽热焓 (GJ/吨) ──
    # 典型饱和蒸汽 (1.0 MPa, ~180°C) 热焓约 2.78 GJ/吨
    STEAM_ENTHALPY = 2.78

    def __init__(self, formula_params, vcer_api_url):
        super().__init__(formula_params, vcer_api_url)
        fuel = self.formula_params.get("replaced_fuel_type", "coal")
        self._ef_old = self.FUEL_EF.get(fuel, self.FUEL_EF["coal"])
        self._old_eff = float(self.formula_params.get("old_boiler_efficiency", 65)) / 100.0
        self._new_eff = float(self.formula_params.get("new_boiler_efficiency", 80)) / 100.0
        # 生物质排放因子视为 0（碳中性）
        self._ef_biomass = 0.0

    def get_formula_text(self) -> str:
        return (
            "减排量 = Q_steam × (EF_fossil / η_old - EF_biomass / η_new)\n\n"
            "其中:\n"
            "  Q_steam      = Δt 内产出蒸汽热量 (GJ) = 蒸汽吨数 × 2.78\n"
            "  EF_fossil    = 被替代化石燃料排放因子 (tCO₂e/GJ)\n"
            "  η_old        = 旧锅炉效率\n"
            "  EF_biomass   = 生物质排放因子 ≈ 0 (碳中性)\n"
            "  η_new        = 新生物质锅炉效率\n\n"
            "说明: 生物质燃烧释放的 CO₂ 等于植物生长时吸收的 CO₂，\n"
            "因此视为净零排放。减排量 = 替代的化石燃料排放量。\n"
            "参考 CDM ACM0006「生物质发电/供热」方法学。"
        )

    def get_parameter_descriptions(self) -> dict:
        return {
            "replaced_fuel_type": "被替代燃料 (coal=燃煤, oil=燃油, gas=天然气)",
            "old_boiler_efficiency": "旧锅炉效率 (%，默认 65)",
            "new_boiler_efficiency": "新生物质锅炉效率 (%，默认 80)",
            "biomass_type": "生物质类型 (wood_chip=木片, pellet=颗粒, straw=秸秆)",
            "biomass_moisture": "生物质含水率 (%，默认 30)",
            "cumulative_steam_output": "累计蒸汽产出量 (吨)",
            "cumulative_biomass_consumed": "累计生物质消耗量 (吨)",
            "steam_enthalpy": f"蒸汽热焓: {self.STEAM_ENTHALPY} GJ/吨 (1.0MPa 饱和蒸汽)",
            **{f"ef_{k}": f"{k} 排放因子: {v} tCO₂e/GJ" for k, v in self.FUEL_EF.items()},
        }

    def fetch_baseline_factors(self, raw_data=None):
        """不依赖外部 API"""
        return {
            "ef_fossil": self._ef_old,
            "ef_biomass": self._ef_biomass,
            "old_efficiency": self._old_eff,
            "new_efficiency": self._new_eff,
        }

    def calculate(self, raw_data, cumulative_data):
        """
        执行一次碳减排计算（增量法）。

        reduction = ΔQ_steam × (EF_fossil / η_old - EF_biomass / η_new)
        """
        # ── 1. 提取数据 ──
        current_steam = float(raw_data.get("cumulative_steam_output", 0))

        # ── 2. 增量 (吨蒸汽) ──
        prev_steam = float(cumulative_data.get("prev_steam_output", 0))
        delta_steam = current_steam - prev_steam

        if delta_steam < 0:
            delta_steam = 0

        # ── 3. 吨蒸汽 → GJ ──
        delta_heat_gj = delta_steam * self.STEAM_ENTHALPY

        # ── 4. 旧锅炉排放强度 = EF_fossil / η_old ──
        if self._old_eff > 0:
            old_emission_intensity = self._ef_old / self._old_eff
        else:
            old_emission_intensity = 0

        # ── 5. 新锅炉排放强度 = EF_biomass / η_new ≈ 0 ──
        if self._new_eff > 0:
            new_emission_intensity = self._ef_biomass / self._new_eff
        else:
            new_emission_intensity = 0

        # ── 6. 减排量 = 热量 × (旧排放强度 - 新排放强度) ──
        reduction = delta_heat_gj * (old_emission_intensity - new_emission_intensity)
        if reduction < 0:
            reduction = 0

        prev_cumulative = float(cumulative_data.get("cumulative_reduction", 0))
        cumulative = prev_cumulative + reduction

        return {
            "carbon_reduction": round(reduction, 6),
            "cumulative_reduction": round(cumulative, 6),
            "breakdown": {
                "delta_steam_tons": round(delta_steam, 2),
                "delta_heat_gj": round(delta_heat_gj, 4),
                "steam_enthalpy": self.STEAM_ENTHALPY,
                "old_emission_intensity": round(old_emission_intensity, 6),
                "new_emission_intensity": round(new_emission_intensity, 6),
                "ef_fossil": self._ef_old,
                "ef_biomass": self._ef_biomass,
                "formula": (
                    f"{round(delta_heat_gj, 4)} GJ × "
                    f"({self._ef_old}/{round(self._old_eff, 4)} - "
                    f"{self._ef_biomass}/{round(self._new_eff, 4)})"
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
        """生物质锅炉：替代化石燃料一行，生物质自身排放（碳中性）一行"""
        breakdown = monitoring.get("latest_breakdown", {})
        if not isinstance(breakdown, dict):
            breakdown = {}

        delta_steam = breakdown.get("delta_steam_tons", 0)
        delta_heat = breakdown.get("delta_heat_gj", 0)
        old_intensity = breakdown.get("old_emission_intensity", 0)
        new_intensity = breakdown.get("new_emission_intensity", 0)
        fuel_type = self.formula_params.get("replaced_fuel_type", "coal")
        fuel_labels = {"coal": "燃煤", "oil": "燃油", "gas": "天然气"}

        return [
            {
                "seq": 1,
                "name": f"替代{fuel_labels.get(fuel_type, fuel_type)}（避免的排放）",
                "activity_data": f"{delta_steam:,.2f} 吨蒸汽 ({delta_heat:,.2f} GJ)",
                "emission_factor": f"{old_intensity:.6f} tCO₂e/GJ",
                "emission_amount": f"{monitoring.get('total_reduction', 0):,.4f} tCO₂e",
            },
            {
                "seq": 2,
                "name": "生物质燃料燃烧（碳中性，净排放 ≈ 0）",
                "activity_data": "见监测数据",
                "emission_factor": f"{new_intensity:.6f} tCO₂e/GJ ≈ 0",
                "emission_amount": "≈ 0 tCO₂e",
            },
        ]
