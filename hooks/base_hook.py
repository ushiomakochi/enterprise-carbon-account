"""
公式基类 — 每种项目类型继承此类，实现自己的碳减排计算公式。

使用方式:
    1. 在 hooks/ 目录创建新文件，如 solar_pv.py
    2. 定义继承 BaseCarbonFormula 的类
    3. 类名任意，但 formula_name 必须与文件名一致
    4. 实现 fetch_baseline_factors()、calculate()、estimate_annual()

公式自动发现:
    系统启动时扫描 hooks/ 目录，自动注册所有有效公式。
"""


class BaseCarbonFormula:
    """碳减排计算公式基类"""

    # ── 元信息（子类必须定义）──
    formula_name: str = ""          # 公式唯一标识，如 "air_compressor"（必须与文件名一致）
    formula_label: str = ""         # 显示名称，如 "空压站节能碳减排"
    formula_version: str = "1.0"    # 版本号
    description: str = ""           # 适用项目类型描述

    # ── 需要的字段（子类定义）──
    required_raw_fields: list = []       # 原始数据必须包含的字段，如 ["cumulative_flow", "cumulative_power"]
    required_formula_params: list = []   # 公式参数必须包含的键，如 ["dew_point", "rated_pressure"]
                                          # （若不需要外部参数，设置为空列表 []）

    def __init__(self, formula_params: dict, vcer_api_url: str):
        """
        初始化公式实例。

        参数:
            formula_params: 项目配置的公式参数（从数据库 hook_params JSON 字段读取）
                            例如: {"dew_point": 0, "rated_pressure": 0.3, "avg_flow_rate": 400}
            vcer_api_url: 促进会 VCER API 地址
        """
        # 防御：确保 formula_params 始终是 dict
        if not isinstance(formula_params, dict):
            formula_params = {}
        self.formula_params = formula_params or {}
        self.vcer_api_url = vcer_api_url
        self._baseline_cache = None   # 基准线因子通用缓存（首次 get_baseline_factors 时填充）
        self._validate_on_init()

    def _validate_on_init(self):
        """初始化时校验参数完整性"""
        errors = self.validate_params()
        if errors:
            print(f"[WARN] 公式 {self.formula_name} 参数不完整: {', '.join(errors)}")

    def fetch_baseline_factors(self, raw_data: dict = None) -> dict:
        """
        获取基准线排放因子。
        通常从促进会 API 或其他数据源获取。

        参数:
            raw_data: 当前原始数据（可选，某些公式需要数据来判断调用哪个 API）

        返回:
            dict: 基准线因子，如 {"z_b": 5.345, "w_b": 0.092}
                  如果没有需要获取的因子，返回 {}
        """
        raise NotImplementedError(
            f"公式 {self.formula_name} 未实现 fetch_baseline_factors()"
        )

    def get_baseline_factors(self) -> dict:
        """返回基准线因子字典（带缓存）。报告引用时优先用缓存，未获取过才调 API。"""
        if self._baseline_cache is None:
            try:
                self._baseline_cache = self.fetch_baseline_factors() or {}
            except Exception:
                self._baseline_cache = {}
        return self._baseline_cache

    def calculate(self, raw_data: dict, cumulative_data: dict) -> dict:
        """
        ★ 核心计算方法 — 每个公式必须实现。

        参数:
            raw_data: 当前采集的原始数据
                      例如: {"cumulative_flow": 5000, "cumulative_power": 200, "timestamp": "..."}
            cumulative_data: 累计/上期数据，用于计算增量
                             例如: {"prev_flow": 4500, "prev_power": 180, "cumulative_reduction": 120.5}

        返回:
            dict: {
                "carbon_reduction": float,       # 本期减排量 tCO₂e
                "cumulative_reduction": float,   # 累计减排量 tCO₂e
                "breakdown": {                   # 计算过程分解（用于前端展示和报告生成）
                    ...
                }
            }
        """
        raise NotImplementedError(
            f"公式 {self.formula_name} 未实现 calculate()"
        )

    def estimate_annual(self, period_data: list) -> float:
        """
        根据历史数据预估年度减排量。

        参数:
            period_data: 一段时间的监测数据列表，每个元素包含 carbon_reduction
                         例如: [{"carbon_reduction": 10.5}, {"carbon_reduction": 12.3}, ...]

        返回:
            float: 年化减排量 tCO₂e
        """
        if not period_data:
            return 0.0
        total = sum(d.get("carbon_reduction", 0) or 0 for d in period_data)
        days = len(period_data)
        if days == 0:
            return 0.0
        return round(total / days * 365, 2)

    def yearly_projection(self, project: dict, start_date: str, years: int = 10) -> list:
        """
        计算计入期逐年（基准线排放 / 项目排放 / 减排量）预估。

        参数:
            project: 项目字典（含 id、hook_params 等）
            start_date: 计入期开始日 (YYYY-MM-DD)
            years: 预估年数（默认 10）

        返回:
            list[dict]: 每项 {year, start, end, baseline_emission,
                              project_emission, reduction}
                        默认返回空列表（无此能力的公式）。
        """
        return []

    def validate_params(self) -> list:
        """校验 formula_params 是否包含所有必要参数，返回缺失项列表"""
        errors = []
        for key in self.required_formula_params:
            if key not in self.formula_params or self.formula_params[key] is None:
                errors.append(f"缺少参数: {key}")
        return errors

    def validate_raw_data(self, raw_data: dict) -> list:
        """校验采集数据是否包含所有必要字段，返回缺失项列表"""
        if not isinstance(raw_data, dict):
            return [f"原始数据类型错误，期望 dict，实际: {type(raw_data).__name__}"]
        errors = []
        for key in self.required_raw_fields:
            if key not in raw_data or raw_data[key] is None:
                errors.append(f"原始数据缺少字段: {key}")
        return errors

    def get_formula_text(self) -> str:
        """
        返回计算公式的可读文本描述。
        用于前端展示和报告生成。子类可覆盖。
        """
        return "（未定义公式描述）"

    def get_parameter_descriptions(self) -> dict:
        """
        返回参数的中文说明。
        用于前端表单和报告生成。子类可覆盖。
        """
        return {}

    # ══════════════════════════════════════════════════════════
    # 报告生成支持
    # ══════════════════════════════════════════════════════════

    def get_emission_sources_for_report(self, monitoring: dict) -> list:
        """
        返回报告"碳排放源清单"表格的行数据。

        每种公式根据自己的计算逻辑定义排放源，例如:
        - 光伏项目：只有"替代电网电力"一行
        - 生物质锅炉：有"生物质替代"和"辅助电力消耗"两行
        - 空压站：有"压缩空气产出"一行

        参数:
            monitoring: 监测数据摘要 dict，包含 total_records, total_reduction,
                        daily_avg, latest_breakdown 等字段

        返回:
            list[dict]: 每行包含:
                - seq (int):           序号
                - name (str):          排放源名称
                - activity_data (str): 活动数据（含单位）
                - emission_factor (str): 排放因子（含单位）
                - emission_amount (str): 排放量（含单位）

        示例:
            [{"seq": 1, "name": "光伏发电替代电网电力",
              "activity_data": "1,234.56 MWh",
              "emission_factor": "0.5777 tCO₂e/MWh",
              "emission_amount": "713.24 tCO₂e"}]

        默认实现：生成一行通用排放源。子类可覆盖以提供更精确的数据。
        """
        return [{
            "seq": 1,
            "name": self.formula_label,
            "activity_data": "见监测数据明细",
            "emission_factor": "见计算公式",
            "emission_amount": f"{monitoring.get('total_reduction', 0):,.4f} tCO₂e",
        }]
