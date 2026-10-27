"""
数据源适配器基类 — 所有数据源适配器继承此类。
"""

from abc import ABC, abstractmethod


class BaseAdapter(ABC):
    """数据源适配器基类"""

    adapter_type: str = "base"       # 适配器类型标识
    adapter_label: str = "基础适配器" # 显示名称

    def __init__(self, config: dict):
        """
        初始化适配器。

        参数:
            config: 数据源配置字典（来自项目 data_source_config JSON 字段）
        """
        self.config = config or {}
        self._connected = False

    @abstractmethod
    def connect(self) -> bool:
        """
        建立数据源连接。
        返回 True 表示连接成功，False 表示失败。
        """
        ...

    @abstractmethod
    def fetch_latest(self) -> dict:
        """
        获取最新一条数据。

        返回:
            dict: 原始数据，如 {"cumulative_flow": 5000, "cumulative_power": 200, "timestamp": "..."}
                  如果获取失败，返回 {}
        """
        ...

    @abstractmethod
    def disconnect(self):
        """断开数据源连接"""
        ...

    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected

    def get_status(self) -> dict:
        """获取适配器状态"""
        return {
            "type": self.adapter_type,
            "label": self.adapter_label,
            "connected": self._connected,
        }
