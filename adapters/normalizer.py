"""
数据前置归一化层（Data Normalizer / 前置适配层）

为什么需要这一层
────────────────
MQTT 只规定「怎么传」（发布/订阅），不规定「传什么」（报文格式）。
每家平台 / 每台设备的报文格式都不一样。如果让适配器直接认识每一种外部格式，
适配器会膨胀成一锅粥，每接一个新平台都要改采集主链路。

所以这里挂一层「前置归一化」：不管对方什么格式，出来都是内部统一格式 ——
字段名以项目公式 hook 的 required_raw_fields 为准（如空压站是
cumulative_flow / cumulative_power + timestamp）。下游（DataCollector / 公式）
完全无感，就像公式 hook 挂在项目上一样，这个前置层 hook 在数据入口处。

用法（唯一写法）
───────────────
一个企业一个 normalizer 文件，放在 normalizers/ 目录，自动发现注册。
机器数据源配置里引用：

    "normalizer": {"hook": "<企业标识>"}   # 即 normalizers/<企业标识>.py

不写 "normalizer" 块 = 原样直通（数据已是系统格式）。

文件内容（见 normalizers/TEMPLATE.py）：
    aliases  = {内部字段: 平台别名}       # MQTT 与 API 通道共用
    convert_mqtt(topic, payload)         # MQTT 实时通道
    convert_api(alias, record)           # 历史 API 通道（基类默认实现通常够用）
"""

import importlib
import os
import sys

# ──────────────────────────────────────────────────────────────
# 转换器注册表 + 自动发现（规则与 core/carbon_calculator 扫描 hooks/ 相同）
# ──────────────────────────────────────────────────────────────

_normalizer_registry = {}  # {"<企业标识>": XxxNormalizer类, ...}
_discovered = False


def _discover():
    """扫描 normalizers/ 目录，自动注册所有转换器（只执行一次）。"""
    global _discovered
    if _discovered:
        return
    _discovered = True

    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    import normalizers
    from normalizers.base_normalizer import BaseDataNormalizer
    for norm_dir in normalizers.__path__:
        if not os.path.isdir(norm_dir):
            continue
        for filename in sorted(os.listdir(norm_dir)):
            if not filename.endswith(".py") or filename.startswith(("_", ".")):
                continue
            if filename in ("base_normalizer.py", "TEMPLATE.py"):
                continue
            module_name = filename[:-3]
            try:
                module = importlib.import_module(f"normalizers.{module_name}")
                for obj in vars(module).values():
                    if (isinstance(obj, type) and issubclass(obj, BaseDataNormalizer)
                            and obj is not BaseDataNormalizer
                            and getattr(obj, "normalizer_name", "") == module_name):
                        _normalizer_registry[obj.normalizer_name] = obj
                        break
            except Exception as error:
                print(f"[ERROR] Loading normalizer {module_name}: {error}")


def get_available_normalizers() -> list:
    """返回所有已注册转换器的元信息（供前端「选文件」下拉）。"""
    _discover()
    return [{"name": n, "label": c.normalizer_label, "version": c.normalizer_version,
             "description": c.description} for n, c in _normalizer_registry.items()]


class DataNormalizer:
    """数据前置归一化器：hook 有则转换，无则直通。"""

    def __init__(self, config: dict = None):
        self.hook = None           # 企业自定义转换器实例（无 hook = 直通）
        self._last_record = None   # 最近一次完整记录（供 fetch_latest 兜底）
        if config:
            self.configure(config)

    def configure(self, cfg: dict):
        cfg = cfg or {}
        hook_name = cfg.get("hook")
        if hook_name:
            _discover()
            hook_cls = _normalizer_registry.get(hook_name)
            if hook_cls:
                self.hook = hook_cls(cfg.get("params") or {})
            else:
                print(f"[WARN] 未找到转换器 hook: '{hook_name}'，"
                      f"可用: {list(_normalizer_registry.keys())}")

    @property
    def active(self) -> bool:
        """是否配置了转换（没配置 = 直通）。"""
        return self.hook is not None

    def process(self, topic: str, payload) -> dict | None:
        """
        MQTT 通道：喂入一条原始消息。

        返回:
            dict: 归一化后的完整记录（喂给 DataCollector 触发计算）
            None: 本条消息不产出完整记录（指标尚未集齐，正常等下一条）
        """
        if isinstance(payload, str):
            import json
            try:
                payload = json.loads(payload)
            except Exception:
                return None
        if not isinstance(payload, dict):
            return None

        # 没配置转换：原样直通
        if not self.hook:
            return payload

        record = self.hook.convert_mqtt(topic or "", payload)
        if record:
            self._last_record = record
        return record

    def latest(self) -> dict:
        """返回最近一次输出的完整记录（供 fetch_latest 在静默期兜底）。"""
        return dict(self._last_record) if self._last_record else {}
