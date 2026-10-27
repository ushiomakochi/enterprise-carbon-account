"""
MQTT 数据源适配器 — 适用于空压站等 IoT 设备通过 MQTT 上报数据。

配置示例 (data_source_config):
{
    "broker_host": "192.168.1.100",
    "broker_port": 1883,
    "topic": "enterprise/036/air_station/stn1/data",
    "username": "mqtt_user",       // 可选
    "password": "mqtt_pass",       // 可选
    "qos": 1,
    "keepalive": 60
}

消息格式 (JSON):
{
    "cumulative_flow": 5000.5,
    "cumulative_power": 200.3,
    "timestamp": "2026-06-10T14:30:00"
}

外部平台格式不统一时，在 data_source_config 里加 "normalizer" 块做
前置归一化（一个企业一个转换文件，放在 normalizers/ 目录）:

{
    "broker_host": "...",
    "topic": "<站点ID>/read/+",
    "normalizer": {"hook": "<企业标识>"}   # 即 normalizers/<企业标识>.py
}
"""

import json
import logging
import threading
import time
from datetime import datetime

from .base_adapter import BaseAdapter
from .normalizer import DataNormalizer

log = logging.getLogger("collector.mqtt")


class MQTTAdapter(BaseAdapter):
    adapter_type = "mqtt"
    adapter_label = "MQTT 数据源"

    def __init__(self, config: dict, on_data=None):
        """
        初始化 MQTT 适配器。

        参数:
            config: 数据源配置
            on_data: 可选回调，收到新消息时调用 on_data(raw_data, timestamp)
                     用于实时触发采集计算，而非等待轮询
        """
        super().__init__(config)
        self._client = None
        self._latest_data = {}     # 缓存最新一条消息
        self._latest_timestamp = None
        self._lock = threading.Lock()
        self._message_received = threading.Event()
        self._last_error = ""      # 记录最后一次错误详情
        self._on_data_callback = on_data  # 实时数据回调
        # 前置归一化层：配置了 "normalizer" 就把外部格式映射成内部统一格式；
        # 没配置则完全直通，行为与旧版一致
        self._normalizer = DataNormalizer(self.config.get("normalizer"))

    def connect(self) -> bool:
        """建立 MQTT 连接并订阅主题"""
        self._last_error = ""

        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self._last_error = "未安装 paho-mqtt，请执行: pip install paho-mqtt"
            print(f"[ERROR] {self._last_error}")
            return False

        broker_host = self.config.get("broker_host")
        if not broker_host:
            self._last_error = "MQTT 配置缺少 broker_host（Broker 地址）"
            print(f"[ERROR] {self._last_error}")
            return False

        broker_port = int(self.config.get("broker_port", 1883))
        topic = self.config.get("topic", "#")
        username = self.config.get("username")
        password = self.config.get("password")
        qos = int(self.config.get("qos", 1))
        keepalive = int(self.config.get("keepalive", 60))

        print(f"[MQTT] 尝试连接 {broker_host}:{broker_port}, 主题: {topic}")

        try:
            self._client = mqtt.Client()
            self._client.on_connect = self._on_connect
            self._client.on_message = self._on_message

            if username:
                self._client.username_pw_set(username, password)

            # 保存 topic 以便 on_connect 中订阅
            self._topic = topic
            self._qos = qos

            self._client.connect(broker_host, broker_port, keepalive)
            self._client.loop_start()
            self._connected = True
            print(f"[MQTT] 已连接到 {broker_host}:{broker_port}, 订阅: {topic}")
            return True

        except Exception as e:
            self._last_error = f"连接 {broker_host}:{broker_port} 失败: {e}"
            print(f"[ERROR] {self._last_error}")
            self._connected = False
            return False

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT 连接回调"""
        if rc == 0:
            client.subscribe(self._topic, self._qos)
            print(f"[MQTT] 已订阅: {self._topic}")
        else:
            print(f"[MQTT] 连接失败，返回码: {rc}")

    def _on_message(self, client, userdata, msg):
        """MQTT 消息回调 — 收到消息后立即通知 DataCollector"""
        try:
            payload = msg.payload.decode("utf-8")
            data = json.loads(payload)
            # 前置归一化：外部格式 → 内部统一格式；指标未集齐时返回 None，跳过本条
            record = self._normalizer.process(msg.topic, data)
            if record is None:
                log.debug("[MQTT] 消息经前置转换后未集齐指标，等待下一条: %s", msg.topic)
                return
            data = record
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._lock:
                self._latest_data = data
                self._latest_timestamp = ts
                self._message_received.set()
            # ★ 实时推送：收到消息立即触发采集计算
            if self._on_data_callback:
                # 每条消息都触发，属于最高频的一类 —— 必须留 DEBUG。
                # 改成 INFO 的话会立刻变成新的日志洪水（100 项目 / 5 秒 ≈ 173 万条/天）
                log.debug("[MQTT] 收到消息，实时触发采集: flow=%s, power=%s",
                          data.get('cumulative_flow', '?'), data.get('cumulative_power', '?'))
                self._on_data_callback(data, ts)
        except Exception as e:
            log.warning("[MQTT] 消息解析失败: %s, payload: %s", e, msg.payload[:200])

    def fetch_latest(self) -> dict:
        """
        获取最新一条 MQTT 消息数据。
        如果 30 秒内未收到新消息，返回缓存的最后一条。
        """
        with self._lock:
            data = self._latest_data.copy() if self._latest_data else {}
            ts = self._latest_timestamp
        if not data:
            return {}
        data["_source_timestamp"] = ts
        return data

    def disconnect(self):
        """断开 MQTT 连接"""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._connected = False
        print("[MQTT] 已断开连接")
