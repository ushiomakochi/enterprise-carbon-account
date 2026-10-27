"""
LED 照明改造碳减排计算公式

适用项目类型: 照明系统节能改造（LED 替换荧光灯/金卤灯/高压钠灯等）
参考方法学: CDM AMS-II.J — 高效照明技术

═══════════════════════════════════════════════════════════════

核心公式:
    减排量 = (P_old × n_old - P_new × n_new) × Operating_Hours × EF_grid / 1000

参数说明:
    P_old           — 旧灯具单灯功率 (W)
    n_old           — 旧灯具数量
    P_new           — 新 LED 灯具单灯功率 (W)
    n_new           — 新 LED 灯具数量（通常与旧数量相同）
    Operating_Hours — Δt 时间段内照明运行小时数
    EF_grid         — 电网排放因子 (tCO₂e/MWh)

═══════════════════════════════════════════════════════════════

简化方案（增量法，使用累计电度差）:
    节省电量 ΔE = (P_old × n_old - P_new × n_new) × Hours / 1000 (kWh)
    减排量 = ΔE × EF_grid / 1000 (tCO₂e)

公式参数 (formula_params):
    old_fixture_type    — 旧灯具类型: fluorescent(荧光灯) / metal_halide(金卤灯) / hps(高压钠灯)
    old_fixture_power   — 旧灯具单灯功率 (W)
    old_fixture_count   — 旧灯具数量 (盏)
    new_fixture_power   — 新 LED 灯具功率 (W)
    new_fixture_count   — 新 LED 灯具数量 (盏)
    daily_operating_hours — 日均运行小时数

原始数据字段 (raw_data):
    cumulative_runtime — 累计运行时间 (小时)，来自照明回路计时器/传感器
    或:
    cumulative_energy   — 累计照明用电量 (kWh)，来自照明回路电表累计值
"""

from .base_hook import BaseCarbonFormula


class LEDLightingFormula(BaseCarbonFormula):
    formula_name = "led_lighting"
    formula_label = "LED照明改造碳减排"
    formula_version = "1.0"
    description = ("适用于照明系统 LED 改造项目的碳减排量计算。"
                   "通过比较改造前后照明功率差异，结合运行时间，"
                   "计算项目实现的碳减排量。参考 CDM AMS-II.J 方法学。")

    required_raw_fields = ["cumulative_runtime"]
    required_formula_params = ["old_fixture_power", "old_fixture_count",
                               "new_fixture_power"]

    EF_GRID = 0.5777  # tCO₂e/MWh

    def __init__(self, formula_params, vcer_api_url):
        super().__init__(formula_params, vcer_api_url)
        self._old_power = float(self.formula_params.get("old_fixture_power", 0))        # W
        self._old_count = int(self.formula_params.get("old_fixture_count", 1))
        self._new_power = float(self.formula_params.get("new_fixture_power", 0))        # W
        self._new_count = int(self.formula_params.get("new_fixture_count", self._old_count))

        # 功率节省 (kW)
        self._power_saved_kw = (
            self._old_power * self._old_count - self._new_power * self._new_count
        ) / 1000.0

    def get_formula_text(self) -> str:
        return (
            "减排量 = (P_old × n_old - P_new × n_new) × Hours × EF_grid / 1000\n\n"
            "其中:\n"
            "  P_old, n_old = 旧灯具功率 (W) × 数量\n"
            "  P_new, n_new = 新灯具功率 (W) × 数量\n"
            "  Hours        = 照明运行小时数 (h)\n"
            "  EF_grid      = 电网排放因子 (tCO₂e/MWh)\n\n"
            "说明: 通过提高光效，在同等照度下减少用电量。\n"
            "减排量 = 节省电量 × 电网排放因子。\n"
            "参考 CDM AMS-II.J「高效照明技术」方法学。"
        )

    def get_parameter_descriptions(self) -> dict:
        return {
            "old_fixture_type": "旧灯具类型 (fluorescent=荧光灯, metal_halide=金卤灯, hps=高压钠灯)",
            "old_fixture_power": "旧灯具单灯功率 (W)",
            "old_fixture_count": "旧灯具数量 (盏)",
            "new_fixture_power": "新 LED 灯具单灯功率 (W)",
            "new_fixture_count": "新 LED 灯具数量 (盏，默认与旧数量相同)",
            "daily_operating_hours": "日均运行小时数 (h)",
            "cumulative_runtime": "累计运行时间 (小时) — 来自照明回路计时器/传感器",
            "power_saved_kw": f"功率节省: {self._power_saved_kw:.2f} kW",
            "ef_grid": f"电网排放因子: {self.EF_GRID} tCO₂e/MWh (2024年)",
        }

    def fetch_baseline_factors(self, raw_data=None):
        """不依赖外部 API"""
        return {
            "power_saved_kw": self._power_saved_kw,
            "old_total_power_kw": round(self._old_power * self._old_count / 1000.0, 2),
            "new_total_power_kw": round(self._new_power * self._new_count / 1000.0, 2),
        }

    def calculate(self, raw_data, cumulative_data):
        """
        执行一次碳减排计算（增量法）。

        reduction = ΔHours × power_saved (kW) × EF_grid / 1000
        """
        # ── 1. 提取数据 ──
        current_runtime = float(raw_data.get("cumulative_runtime", 0))

        # ── 2. 增量 (小时) ──
        prev_runtime = float(cumulative_data.get("prev_runtime", 0))
        delta_hours = current_runtime - prev_runtime

        if delta_hours < 0:
            delta_hours = 0

        # ── 3. 节省电量 (kWh) ──
        energy_saved_kwh = delta_hours * self._power_saved_kw

        # ── 4. 减排量 (tCO₂e) ──
        reduction = energy_saved_kwh / 1000.0 * self.EF_GRID
        if reduction < 0:
            reduction = 0

        prev_cumulative = float(cumulative_data.get("cumulative_reduction", 0))
        cumulative = prev_cumulative + reduction

        return {
            "carbon_reduction": round(reduction, 6),
            "cumulative_reduction": round(cumulative, 6),
            "breakdown": {
                "delta_hours": round(delta_hours, 2),
                "power_saved_kw": round(self._power_saved_kw, 4),
                "energy_saved_kwh": round(energy_saved_kwh, 2),
                "ef_grid": self.EF_GRID,
                "formula": (
                    f"{round(delta_hours, 2)} h × "
                    f"{round(self._power_saved_kw, 4)} kW / 1000 × "
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
        """LED照明：一种排放源（节电量 × 电网排放因子）"""
        breakdown = monitoring.get("latest_breakdown", {})
        if not isinstance(breakdown, dict):
            breakdown = {}

        energy_saved = breakdown.get("energy_saved_kwh", 0)
        ef_grid = breakdown.get("ef_grid", 0.5777)

        return [{
            "seq": 1,
            "name": "照明节电（替代电网电力）",
            "activity_data": f"{energy_saved:,.2f} kWh 节电量",
            "emission_factor": f"{ef_grid} tCO₂e/MWh",
            "emission_amount": f"{monitoring.get('total_reduction', 0):,.4f} tCO₂e",
        }]
