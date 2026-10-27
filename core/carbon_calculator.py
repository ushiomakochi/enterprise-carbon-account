"""
碳计算引擎 — 负责加载公式、调度计算、存储结果。

工作流程:
1. 启动时扫描 hooks/ 目录，发现所有可用公式
2. 对每个项目的**每台机器**，按 project.hook_name 加载对应的公式实例
3. 定时采集数据 → 调用公式 calculate() → 存储结果到 monitoring_data
4. 更新项目累计减排量

设计要点:
- 公式实例按 **machine_id** 缓存，避免重复初始化。
  公式本身（hook_name）是项目级的，但**参数是机器级的** —— 同一个项目里
  1 号机 dew_point=4、2 号机 dew_point=3，所以实例必须一台一个。
- 基准线因子在公式内部缓存（由公式自行管理）——它也是实例属性，
  参数不同的机器各取各的因子，天然不会串味。
- 计算错误不抛出异常，而是记录日志并返回 None

注: 数据库字段 hook_name / hook_params 保留原有名称以保持兼容，
    代码中统一使用"公式"(formula) 一词。hook_params 现在存在 project_machines 上。
"""

import os
import sys
import json
import importlib
import traceback
from datetime import datetime


class CarbonCalculator:
    """
    碳计算引擎 — 类级别状态管理。

    使用方式:
        # 启动时
        CarbonCalculator.discover_formulas("hooks")

        # 为机器的公式初始化
        CarbonCalculator.init_machine_formula(machine_dict, hook_name, vcer_api_url)

        # 执行计算
        result = CarbonCalculator.compute(machine_id, raw_data, cumulative_data)
    """

    _formulas_registry = {}  # {"air_compressor": AirCompressorFormula类, ...}
    _instances = {}          # {machine_id: FormulaInstance}

    # ══════════════════════════════════════════════════════════
    # 公式发现与注册
    # ══════════════════════════════════════════════════════════

    @classmethod
    def discover_formulas(cls, hooks_dir="hooks"):
        """
        扫描 hooks/ 目录，自动发现并注册所有计算公式。

        发现规则:
        - 扫描 hooks/ 下所有 .py 文件（排除 _ 开头和 base_hook.py）
        - 对每个模块，查找 formula_name 属性与文件名一致的类
        - 该类必须继承自 BaseCarbonFormula

        参数:
            hooks_dir: 公式目录路径
        """
        # 确保 hooks 目录在 sys.path 中
        parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)

        if not os.path.isdir(hooks_dir):
            # 尝试相对于项目根目录
            hooks_dir = os.path.join(parent_dir, hooks_dir)
        if not os.path.isdir(hooks_dir):
            print(f"[WARN] 公式目录不存在: {hooks_dir}")
            return

        cls._formulas_registry.clear()

        for filename in sorted(os.listdir(hooks_dir)):
            if not filename.endswith(".py"):
                continue
            if filename.startswith("_") or filename.startswith("."):
                continue
            if filename == "base_hook.py":
                continue

            module_name = filename[:-3]  # 去掉 .py

            try:
                module = importlib.import_module(f"hooks.{module_name}")

                # 查找 formula_name 匹配的类
                found = False
                for attr_name in dir(module):
                    obj = getattr(module, attr_name)
                    if not isinstance(obj, type):
                        continue
                    if not hasattr(obj, "formula_name"):
                        continue
                    if obj.formula_name == module_name:
                        cls._formulas_registry[obj.formula_name] = obj
                        found = True
                        print(f"  [FORMULA] 已注册: {obj.formula_name} ({obj.formula_label} v{obj.formula_version})")
                        break

                if not found:
                    print(f"  [WARN] 模块 {module_name} 中未找到 formula_name='{module_name}' 的公式类")

            except Exception as e:
                print(f"  [ERROR] 加载公式模块 {module_name} 失败: {e}")
                traceback.print_exc()

    @classmethod
    def get_available_formulas(cls) -> list:
        """
        返回所有可用公式的元信息列表。
        供企业选择项目类型时展示。
        """
        result = []
        for name, formula_cls in cls._formulas_registry.items():
            result.append({
                "name": name,
                "label": formula_cls.formula_label,
                "version": formula_cls.formula_version,
                "description": formula_cls.description,
                "required_params": formula_cls.required_formula_params,
                "required_raw_fields": formula_cls.required_raw_fields,
            })
        return result

    # ══════════════════════════════════════════════════════════
    # 公式实例管理
    # ══════════════════════════════════════════════════════════

    @classmethod
    def init_machine_formula(cls, machine: dict, hook_name: str, vcer_api_url: str):
        """
        为一台机器初始化并缓存公式实例（按 machine_id 缓存）。

        参数:
            machine: 机器字典（必须包含 id 和 hook_params）
            hook_name: **项目的**公式标识（公式是项目级的，机器上不单独选）
            vcer_api_url: 促进会 API 地址

        返回:
            BaseCarbonFormula 实例
        """
        formula_cls = cls._formulas_registry.get(hook_name)
        if not formula_cls:
            raise ValueError(
                f"未找到公式: '{hook_name}'。可用公式: {list(cls._formulas_registry.keys())}"
            )

        # 解析公式参数（来自机器）
        formula_params = cls.parse_formula_params(machine.get("hook_params", "{}"),
                                                  label=f"机器 {machine.get('id')}")

        # 创建实例
        instance = formula_cls(formula_params, vcer_api_url)

        # 预获取基准线因子（可选：某些公式可能不需要）
        try:
            instance.get_baseline_factors()
        except Exception as e:
            print(f"  [WARN] 机器 {machine.get('id')} 预获取基准线因子失败: {e}")
            # 不阻塞初始化，计算时会重试

        cls._instances[machine["id"]] = instance
        return instance

    @staticmethod
    def parse_formula_params(raw, label: str = "") -> dict:
        """把 hook_params（JSON 字符串或 dict）解析成 dict，失败一律给空 dict。"""
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return {}
        if not isinstance(raw, dict):
            if raw:
                print(f"  [WARN] {label} 的公式参数解析后不是 dict（{type(raw).__name__}），已重置为空")
            return {}
        return raw

    @classmethod
    def init_project_formula(cls, project: dict, vcer_api_url: str):
        """
        为项目初始化并缓存公式实例 —— 取项目的**主机器**（排序第一台）。

        报告级信息（公式文本、基准线因子、模板里的 {{ project.dew_point }}）
        都是项目口径的，用主机器的参数；采集/计算请直接走 init_machine_formula()。

        参数:
            project: 项目字典（必须包含 id, hook_name）
            vcer_api_url: 促进会 API 地址

        返回:
            BaseCarbonFormula 实例
        """
        from .project_manager import ensure_project_machine
        machine = ensure_project_machine(project["id"])
        return cls.init_machine_formula(machine, project.get("hook_name", ""), vcer_api_url)

    @classmethod
    def get_instance(cls, machine_id: int):
        """获取已缓存的公式实例（按机器）"""
        return cls._instances.get(machine_id)

    @classmethod
    def get_project_instance(cls, project: dict):
        """取项目主机器的公式实例（报告级信息用）。没有则返回 None。"""
        try:
            from .project_manager import get_primary_machine
            m = get_primary_machine(project["id"])
            if not m:
                return None
            return cls._instances.get(m["id"])
        except Exception:
            return None

    @classmethod
    def remove_instance(cls, machine_id: int):
        """移除公式实例缓存（按机器）"""
        cls._instances.pop(machine_id, None)

    @classmethod
    def remove_project_instances(cls, project_id: int):
        """移除项目下所有机器的公式实例缓存（删机器/改参数后用）"""
        try:
            from .project_manager import get_project_machines
            for m in get_project_machines(project_id):
                cls._instances.pop(m["id"], None)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════
    # 计算调度
    # ══════════════════════════════════════════════════════════

    @classmethod
    def compute(cls, machine_id: int, raw_data: dict, cumulative_data: dict) -> dict:
        """
        执行一次碳减排计算。

        参数:
            machine_id: 机器 ID（公式实例按机器缓存）
            raw_data: 当前采集的原始数据
            cumulative_data: 累计/上期数据

        返回:
            dict: {"carbon_reduction": ..., "cumulative_reduction": ..., "breakdown": {...}}

        异常:
            Exception: 公式未初始化或计算失败
        """
        formula = cls._instances.get(machine_id)
        if not formula:
            raise Exception(
                f"机器 {machine_id} 未初始化公式，请先调用 init_machine_formula()"
            )

        # 数据校验
        errors = formula.validate_raw_data(raw_data)
        if errors:
            raise Exception(f"原始数据校验失败: {'; '.join(errors)}")

        # 执行计算
        return formula.calculate(raw_data, cumulative_data)

    @classmethod
    def estimate_annual(cls, machine_id: int, period_data: list) -> float:
        """
        预估年度减排量（按单台机器）。

        参数:
            machine_id: 机器 ID
            period_data: 历史监测数据列表

        返回:
            float: 预估年度减排量 tCO₂e
        """
        formula = cls._instances.get(machine_id)
        if not formula:
            return 0.0
        try:
            return formula.estimate_annual(period_data)
        except Exception as e:
            print(f"  [ERROR] 机器 {machine_id} 预估年度减排量失败: {e}")
            return 0.0

    @classmethod
    def get_formula_info(cls, machine_id: int) -> dict:
        """获取机器的计算公式信息"""
        formula = cls._instances.get(machine_id)
        if not formula:
            return {"formula": "", "parameters": {}}
        return {
            "formula": formula.get_formula_text(),
            "parameters": formula.get_parameter_descriptions(),
        }

    # ── 兼容旧方法名（逐步废弃）──

    @classmethod
    def discover_hooks(cls, hooks_dir="hooks"):
        """[已废弃] 请使用 discover_formulas()"""
        return cls.discover_formulas(hooks_dir)

    @classmethod
    def get_available_hooks(cls) -> list:
        """[已废弃] 请使用 get_available_formulas()"""
        return cls.get_available_formulas()

    @classmethod
    def init_project_hook(cls, project: dict, vcer_api_url: str):
        """[已废弃] 请使用 init_project_formula()"""
        return cls.init_project_formula(project, vcer_api_url)
