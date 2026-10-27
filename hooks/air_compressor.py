"""
空压站节能碳减排计算公式

适用项目类型: 数字能源空气压缩站节能项目
参考: 空压站节能碳减排方法学需求文档 Stage1（首个接入企业定制，现为通用方法学 hook）

═══════════════════════════════════════════════════════════════

核心公式:
    减排量 = (Q_b × Z_b × 10⁻⁵) - (AD_电 / 1000 × EF_grid)

参数说明:
    Q_b      — Δt 时间内输出的压缩空气量 (m³)
    Z_b      — 将输出压缩空气量转换为 CO₂ 当量的系数 (10⁻⁵ tCO₂/m³)
              从促进会 API /cn/api/air_compressor_params 获取
    AD_电    — Δt 时间内数字能源空压站使用的实际电量 (kWh)
    EF_grid  — 电网排放因子 (tCO₂e/MWh)
              默认 0.5777，来源: 生态环境部《2024年电力碳足迹因子》

═══════════════════════════════════════════════════════════════

公式参数 (formula_params):
    dew_point       — 气站露点温度 (℃)
    rated_pressure  — 额定排气压力 (MPa)
    avg_flow_rate   — 供气平均流量 (m³/min)
    has_oil         — 是否有油 ("有油" 或 "无油")，露点 ≥ -70℃ 时必填

原始数据字段 (raw_data):
    cumulative_flow   — 累计产气量 (m³)，来自 PLC/SCADA 累计值
    cumulative_power  — 累计耗电量 (kWh)，来自电表累计值
"""

_requests = None


def _r():
    """惰性导入 requests（避免启动加载公式时从网络盘加载 requests，加快启动）。"""
    global _requests
    if _requests is None:
        import requests
        _requests = requests
    return _requests


from .base_hook import BaseCarbonFormula

class AirCompressorFormula(BaseCarbonFormula):
    formula_name = "air_compressor"
    formula_label = "空压站节能碳减排"
    formula_version = "1.0"
    description = ("适用于数字能源空气压缩站节能项目的碳减排量计算。"
                   "通过对比传统空压站基准线排放与实际用电排放的差值，"
                   "计算项目实现的碳减排量。")

    required_raw_fields = ["cumulative_flow", "cumulative_power"]
    required_formula_params = ["dew_point", "rated_pressure", "avg_flow_rate"]

    # 电网排放因子（2024年，来源：生态环境部）
    # 单位: tCO₂e/MWh
    EF_GRID = 0.5777

    def __init__(self, formula_params, vcer_api_url):
        super().__init__(formula_params, vcer_api_url)
        self._cached_z_b = None  # 缓存基准线碳因子，避免频繁调 API
        self._cached_w_b = None  # 缓存基准线用电单耗

    def get_formula_text(self) -> str:
        return (
            "减排量 = (Q_b × Z_b × 10⁻⁵) - (AD_电 / 1000 × EF_grid)\n\n"
            "其中:\n"
            "  Q_b     = Δt 内输出压缩空气量 (m³)\n"
            "  Z_b     = 单位用气碳排放因子 (10⁻⁵ tCO₂/m³)\n"
            "  AD_电   = Δt 内实际用电量 (kWh)\n"
            "  EF_grid = 电网排放因子 (tCO₂/MWh, 2024年取 0.5777)"
        )

    def get_parameter_descriptions(self) -> dict:
        return {
            "dew_point": "气站露点温度 (℃)",
            "rated_pressure": "额定排气压力 (MPa)",
            "avg_flow_rate": "供气平均流量 (m³/min)",
            "has_oil": "是否有油（有油/无油，露点 ≥ -70℃ 时必填）",
            "cumulative_flow": "累计产气量 (m³) — 来自 PLC/SCADA 累计值",
            "cumulative_power": "累计耗电量 (kWh) — 来自电表累计值",
            "z_b": f"基准线碳因子: {self._cached_z_b or '（待获取）'} 10⁻⁵ tCO₂/m³",
            "ef_grid": f"电网排放因子: {self.EF_GRID} tCO₂/MWh (2024年)",
        }

    def fetch_baseline_factors(self, raw_data=None):
        """
        从促进会 API 获取基准线因子 Z_b 和 W_b。

        API: GET /cn/api/air_compressor_params
        参数来源于项目的 formula_params。
        """
        dew = self.formula_params.get("dew_point")
        pressure = self.formula_params.get("rated_pressure")
        flow = self.formula_params.get("avg_flow_rate")
        oil = self.formula_params.get("has_oil")

        # ── 参数校验：缺少必填参数时给出明确提示 ──
        missing = []
        if dew is None:
            missing.append("露点温度 (dew_point)")
        if pressure is None:
            missing.append("额定排气压力 (rated_pressure)")
        if flow is None:
            missing.append("供气平均流量 (avg_flow_rate)")
        # 露点 ≥ -70℃ 时 has_oil 为必填项（不论取值）
        if dew is not None and dew >= -70 and not oil:
            missing.append("是否有油 (has_oil)，露点 ≥ -70℃ 时为必填项")
        if missing:
            raise Exception(
                f"项目公式参数不完整，缺少: {', '.join(missing)}。"
                f"请在项目设置中配置公式参数。"
            )

        # 露点 ≥ -70℃ 时需要 has_oil 参数
        params = {
            "dew_point": dew,
            "rated_pressure": pressure,
            "avg_flow_rate": flow,
        }
        if dew is not None and dew >= -70:
            params["has_oil"] = oil

        try:
            resp = _r().get(
                f"{self.vcer_api_url}/cn/api/air_compressor_params",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") == "成功":
                output = data.get("output", {})
                if not isinstance(output, dict):
                    raise Exception(f"API 返回的 output 字段类型异常: {type(output).__name__}，内容: {str(output)[:200]}")
                self._cached_z_b = output.get("carbon_emission")       # 10⁻⁵ tCO₂/m³
                self._cached_w_b = output.get("power_consumption")     # kWh/m³
                return {"z_b": self._cached_z_b, "w_b": self._cached_w_b}
            else:
                raise Exception(f"API 返回错误: {data.get('msg', '未知错误')}")

        except _r().exceptions.HTTPError as e:
            detail = ""
            try:
                detail = f"，响应内容: {e.response.text[:300]}"
            except Exception:
                pass
            raise Exception(f"调用促进会 API 失败 (HTTP {e.response.status_code}){detail}")

        except _r().exceptions.ConnectionError:
            raise Exception(
                f"无法连接到促进会 API ({self.vcer_api_url})，"
                "请检查网络连接和 VCER_API_URL 配置"
            )
        except _r().exceptions.Timeout:
            raise Exception("调用促进会 API 超时，请稍后重试")
        except Exception as e:
            raise Exception(f"获取基准线参数失败: {str(e)}")

    def calculate(self, raw_data, cumulative_data):
        """
        执行一次碳减排计算（增量法）。

        用累计值的差值来计算 Δt 时间内的减排量:
            ΔQ_b = current_flow - prev_flow
            ΔAD_电 = current_power - prev_power

        然后: reduction = (ΔQ_b × Z_b × 10⁻⁵) - (ΔAD_电 / 1000 × 0.5777)
        """
        # ── 1. 提取原始数据 ──
        current_flow = float(raw_data.get("cumulative_flow", 0))
        current_power = float(raw_data.get("cumulative_power", 0))

        # ── 2. 计算增量 ──
        prev_flow = float(cumulative_data.get("prev_flow", 0))
        prev_power = float(cumulative_data.get("prev_power", 0))

        delta_flow = current_flow - prev_flow      # Q_b (m³)
        delta_power = current_power - prev_power    # AD_电 (kWh)

        # 防御: 如果累计值回退（如设备重置），增量取 0
        if delta_flow < 0:
            delta_flow = 0
        if delta_power < 0:
            delta_power = 0

        # ── 3. 获取基准线因子（缓存）──
        if self._cached_z_b is None:
            self.fetch_baseline_factors()
        z_b = self._cached_z_b
        if z_b is None:
            raise Exception("基准线碳因子 Z_b 未获取，请检查公式参数和 API 连接")

        # ── 4. 计算排放量 ──
        # 基准线排放 = Q_b × Z_b × 10⁻⁵ (tCO₂)
        baseline_emissions = delta_flow * z_b * 0.00001

        # 项目实际排放 = AD_电 / 1000 × EF_grid (tCO₂)
        # AD_电 是 kWh，EF_grid 是 tCO₂/MWh，所以需要除以 1000 转换
        project_emissions = (delta_power / 1000.0) * self.EF_GRID

        # ── 5. 减排量 = 基准线 - 实际 ──
        reduction = baseline_emissions - project_emissions
        if reduction < 0:
            reduction = 0  # 无减排，取 0

        # ── 6. 累计 ──
        prev_cumulative = float(cumulative_data.get("cumulative_reduction", 0))
        cumulative = prev_cumulative + reduction

        return {
            "carbon_reduction": round(reduction, 6),
            "cumulative_reduction": round(cumulative, 6),
            "breakdown": {
                "delta_flow_m3": round(delta_flow, 2),
                "delta_power_kwh": round(delta_power, 2),
                "z_b_factor": round(z_b, 6),
                "z_b_unit": "10⁻⁵ tCO₂/m³",
                "baseline_emissions_tco2": round(baseline_emissions, 8),
                "project_emissions_tco2": round(project_emissions, 8),
                "ef_grid": self.EF_GRID,
                "ef_grid_unit": "tCO₂/MWh",
                "formula": (
                    f"({round(delta_flow, 2)} × {z_b} × 10⁻⁵) - "
                    f"({round(delta_power, 2)} / 1000 × {self.EF_GRID})"
                ),
            },
        }

    def estimate_annual(self, period_data):
        """
        根据已有监测数据预估年度减排量。

        规则:
        - 有 ≥365 天数据 → 直接取最近365天总和
        - 有 <365 天数据 → 日均值 × 365
        - 无数据 → 0
        """
        if not period_data:
            return 0.0

        total = sum(d.get("carbon_reduction", 0) or 0 for d in period_data)
        days = len(period_data)
        if days == 0:
            return 0.0

        # 如果超过一年，取最近365天
        if days >= 365:
            recent = period_data[-365:]
            return round(sum(d.get("carbon_reduction", 0) or 0 for d in recent), 2)
        else:
            return round(total / days * 365, 2)

    def yearly_projection(self, project: dict, start_date: str, years: int = 10) -> list:
        """
        计算空压站项目的计入期逐年预估（按自然年对齐）。

        口径:
          - 年气量        = avg_flow_rate(m³/min) × 60 × 该年运行小时
          - 基准线排放    = 年气量 × Z_b × 10⁻⁵
          - 项目排放      = 年气量 × 项目电单耗 / 1000 × EF_grid
          - 减排量        = max(基准线排放 - 项目排放, 0)

        可选参数（取自 formula_params）:
          annual_operating_hours  年运行小时数（默认 8760，即全年无休）
          project_specific_power  项目电单耗 kWh/m³（缺省由历史 ΣΔ电/ΣΔ气 推算）
        """
        from datetime import datetime, date, timedelta

        params = self.formula_params or {}

        def _f(v, default=0.0):
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        flow_rate = _f(params.get("avg_flow_rate", 0))
        annual_hours = _f(params.get("annual_operating_hours", 8760), 8760.0)

        # 基准线碳因子 Z_b
        factors = self.get_baseline_factors() or {}
        z_b = _f(factors.get("z_b", 0))

        if flow_rate <= 0 or z_b <= 0 or not start_date:
            return []

        # 项目电单耗：优先参数，否则由历史 ΣΔ电/ΣΔ气 推算
        specific_power = None
        sp_raw = params.get("project_specific_power")
        if sp_raw not in (None, ""):
            specific_power = _f(sp_raw, None)
            if specific_power == 0:
                specific_power = None
        if specific_power is None:
            specific_power = self._measured_specific_power(project)

        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
        except Exception:
            return []

        # 计入期末尾 = 起始日 + years 年 − 1 天（备案口径：2022-05-13 起 10 年 ⇒ 2032-05-12）
        def _add_years(d, n):
            try:
                return d.replace(year=d.year + n)
            except ValueError:      # 2/29 加到非闰年
                return d.replace(year=d.year + n, day=28)

        period_end = _add_years(start, years) - timedelta(days=1)

        # 按自然年切分：首段部分年 + 中间整年 + 末段部分年。
        # 以前是「起始年 + 往后数 N 个自然年」，末段会整个丢掉
        # （2022-05-13 起 10 年只算到 2031-12-31，漏掉 2032 年的 4 个多月），
        # 与备案案例的 11 行对不上。
        rows = []
        cur = start
        idx = 0
        while cur <= period_end:
            y = cur.year
            span_start = cur
            span_end = min(date(y, 12, 31), period_end)
            days_in_span = (span_end - span_start).days + 1
            hours_year = annual_hours * (days_in_span / 365.0)
            annual_volume = flow_rate * 60.0 * hours_year  # m³

            baseline = annual_volume * z_b * 0.00001  # tCO₂
            need_sp = specific_power is None
            if specific_power:
                project_emi = annual_volume * specific_power / 1000.0 * self.EF_GRID
            else:
                project_emi = 0.0
            reduction = max(baseline - project_emi, 0.0)

            idx += 1
            rows.append({
                "year": idx,
                "start": span_start.strftime("%Y-%m-%d"),
                "end": span_end.strftime("%Y-%m-%d"),
                "baseline_emission": round(baseline, 4),
                "project_emission": round(project_emi, 4),
                "reduction": round(reduction, 4),
                "need_specific_power": bool(need_sp),
                # 计入期年数（≠ 行数：首末是部分年，10 年会计出 11 行）
                "period_years": years,
            })
            cur = span_end + timedelta(days=1)
        return rows

    def _measured_specific_power(self, project: dict):
        """从历史监测数据推算项目实际电单耗（ΣΔ电 / ΣΔ气，kWh/m³）。无数据返回 None。"""
        pid = project.get("id")
        if not pid:
            return None
        try:
            import json as _json
            from core.project_manager import get_db
            conn = get_db()
            try:
                rows = conn.execute(
                    "SELECT breakdown FROM monitoring_data WHERE project_id = ?",
                    (pid,)
                ).fetchall()
            finally:
                conn.close()
            total_power = 0.0
            total_flow = 0.0
            for r in rows:
                bd = r["breakdown"]
                if isinstance(bd, str):
                    try:
                        bd = _json.loads(bd)
                    except Exception:
                        continue
                if not isinstance(bd, dict):
                    continue
                total_power += float(bd.get("delta_power_kwh", 0) or 0)
                total_flow += float(bd.get("delta_flow_m3", 0) or 0)
            if total_flow <= 0:
                return None
            return total_power / total_flow
        except Exception:
            return None

    # ══════════════════════════════════════════════════════════
    # 报告排放源清单
    # ══════════════════════════════════════════════════════════

    def get_emission_sources_for_report(self, monitoring: dict) -> list:
        """空压站有两行排放源：基准线（传统空压站）和项目实际用电"""
        breakdown = monitoring.get("latest_breakdown", {})
        if not isinstance(breakdown, dict):
            breakdown = {}

        baseline_emissions = breakdown.get("baseline_emissions_tco2", 0)
        project_emissions = breakdown.get("project_emissions_tco2", 0)
        delta_flow = breakdown.get("delta_flow_m3", 0)
        delta_power = breakdown.get("delta_power_kwh", 0)
        z_b = breakdown.get("z_b_factor", self._cached_z_b or 0)

        return [
            {
                "seq": 1,
                "name": "压缩空气产出（基准线排放）",
                "activity_data": f"{delta_flow:,.2f} m³ 压缩空气",
                "emission_factor": f"{z_b:.6f} × 10⁻⁵ tCO₂/m³" if z_b else "—",
                "emission_amount": f"{baseline_emissions:,.4f} tCO₂e",
            },
            {
                "seq": 2,
                "name": "空压站实际用电（项目排放，扣除）",
                "activity_data": f"{delta_power:,.2f} kWh",
                "emission_factor": f"{self.EF_GRID} tCO₂e/MWh",
                "emission_amount": f"{-project_emissions:,.4f} tCO₂e",
            },
        ]
