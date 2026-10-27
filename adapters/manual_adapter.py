"""
手动录入 / CSV 导入数据源适配器 — 适用于无自动化数据源的企业。

通过前端表单手动录入数据，或 CSV 批量导入。

CSV 格式要求:
    第一行为表头: timestamp,cumulative_flow,cumulative_power
    后续每行一条记录: 2026-06-10 14:30:00,5000.5,200.3
"""

import csv
import io
from datetime import datetime

from .base_adapter import BaseAdapter


class ManualAdapter(BaseAdapter):
    adapter_type = "manual"
    adapter_label = "手动录入 / CSV 导入"

    def __init__(self, config: dict):
        super().__init__(config)
        self._latest_data = {}

    def connect(self) -> bool:
        """手动模式无需连接"""
        self._connected = True
        return True

    def fetch_latest(self) -> dict:
        """返回最近手动录入的数据"""
        return self._latest_data.copy() if self._latest_data else {}

    def input_data(self, data: dict):
        """录入一条数据"""
        if "timestamp" not in data:
            data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._latest_data = data
        self._latest_data["_source_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def import_csv(self, csv_content: str) -> list:
        """
        批量导入 CSV 数据。

        参数:
            csv_content: CSV 文件内容字符串

        返回:
            list: 解析后的数据列表
        """
        reader = csv.DictReader(io.StringIO(csv_content))
        records = []
        for row in reader:
            record = {}
            for key, value in row.items():
                key = key.strip()
                value = value.strip() if value else ""
                # 尝试转换为数字
                try:
                    if "." in value:
                        record[key] = float(value)
                    else:
                        record[key] = int(value)
                except (ValueError, TypeError):
                    record[key] = value
            if "timestamp" not in record:
                record["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            records.append(record)

        if records:
            self._latest_data = records[-1]
        return records

    def disconnect(self):
        """手动模式无需断开"""
        self._connected = False
