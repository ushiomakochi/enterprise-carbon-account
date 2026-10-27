"""
内置模拟数据源适配器 — 无需外部依赖，在进程内生成模拟传感器数据。

适用于开发和测试场景，行为类似 MQTT 推送（被动接收），但完全自包含：
- 后台线程定时生成递增的模拟数据
- fetch_latest() 返回最新的生成值
- 无需 MQTT Broker、无需 REST API 端点

配置示例 (data_source_config):
{
    "interval": 5,              # 数据生成间隔（秒），默认 10
    "flow_start": 5000.0,       # 累计产气量初始值，默认 5000
    "power_start": 200.0,       # 累计耗电量初始值，默认 200
    "flow_delta_min": 10.0,     # 每次产气增量下限，默认 10
    "flow_delta_max": 50.0,     # 每次产气增量上限，默认 50
    "power_delta_min": 0.5,     # 每次耗电增量下限，默认 0.5
    "power_delta_max": 2.0      # 每次耗电增量上限，默认 2.0
}

注：所有配置字段均为可选，留空使用默认值。
"""

import json
import random
import threading
import time
from datetime import datetime

from .base_adapter import BaseAdapter


class InternalAdapter(BaseAdapter):
    adapter_type = "internal"
    adapter_label = "内置模拟传感器（无需外部服务）"

    def __init__(self, config: dict):
        super().__init__(config)
        self._latest_data = {}
        self._lock = threading.Lock()
        self._thread = None
        self._running = False

        # 读取配置，使用默认值
        self._interval = float(config.get("interval", 10))
        self._flow = float(config.get("flow_start", 5000.0))
        self._power = float(config.get("power_start", 200.0))
        self._flow_min = float(config.get("flow_delta_min", 10.0))
        self._flow_max = float(config.get("flow_delta_max", 50.0))
        self._power_min = float(config.get("power_delta_min", 0.5))
        self._power_max = float(config.get("power_delta_max", 2.0))

    def connect(self) -> bool:
        """启动后台模拟线程"""
        if self._running:
            return True

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._connected = True
        print(f"[内置模拟] 已启动，数据生成间隔: {self._interval}s, "
              f"初始值: flow={self._flow}, power={self._power}")
        return True

    def _run(self):
        """后台线程：定时生成模拟数据"""
        while self._running:
            flow_delta = random.uniform(self._flow_min, self._flow_max)
            power_delta = random.uniform(self._power_min, self._power_max)
            self._flow += flow_delta
            self._power += power_delta

            data = {
                "cumulative_flow": round(self._flow, 2),
                "cumulative_power": round(self._power, 2),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with self._lock:
                self._latest_data = data

            time.sleep(self._interval)

    def fetch_latest(self) -> dict:
        """返回最新生成的模拟数据"""
        with self._lock:
            return self._latest_data.copy() if self._latest_data else {}

    def disconnect(self):
        """停止模拟线程"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        self._connected = False
        print("[内置模拟] 已停止")
