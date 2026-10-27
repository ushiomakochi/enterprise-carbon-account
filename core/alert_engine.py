"""
告警引擎 — 根据告警规则检查监测数据，触发告警。

告警规则:
- metric: 监测指标 (carbon_reduction / flow_rate / power)
- operator: 比较方式 (gt=大于阈值 / lt=小于阈值)
- threshold: 阈值
- cooldown_minutes: 冷却时间（分钟内不重复触发）
"""

import json
import logging
import time
from datetime import datetime, timedelta

from .project_manager import get_db

log = logging.getLogger("alert")


# 冷却状态缓存 {rule_id: last_triggered_timestamp}
_cooldowns = {}


class AlertEngine:
    """告警引擎"""

    @classmethod
    def check(cls, project_id: int, result: dict):
        """
        检查项目的最新计算结果是否触发告警规则。

        参数:
            project_id: 项目 ID
            result: CarbonCalculator.compute() 的返回结果
        """
        conn = get_db()
        try:
            rules = conn.execute(
                """SELECT * FROM alert_rules
                   WHERE project_id = ? AND is_enabled = 1""",
                (project_id,)
            ).fetchall()

            for rule in rules:
                rule_id = rule["id"]
                metric = rule["metric"]
                operator = rule["operator"]
                threshold = float(rule["threshold"])
                cooldown = int(rule["cooldown_minutes"])

                # 获取当前指标值
                current_value = cls._get_metric_value(result, metric)
                if current_value is None:
                    continue

                # 判断是否触发
                triggered = False
                if operator == "gt" and current_value > threshold:
                    triggered = True
                elif operator == "lt" and current_value < threshold:
                    triggered = True

                if not triggered:
                    continue

                # 检查冷却时间
                if cls._is_in_cooldown(rule_id, cooldown):
                    continue

                # 记录告警
                alert_msg = (
                    f"[告警] 项目{project_id} — {metric} = {current_value}, "
                    f"{'大于' if operator == 'gt' else '小于'}阈值 {threshold}"
                )
                log.warning(alert_msg)

                conn.execute(
                    """INSERT INTO alert_log
                       (rule_id, project_id, alert_message, metric_value, threshold_value)
                       VALUES (?, ?, ?, ?, ?)""",
                    (rule_id, project_id, alert_msg, current_value, threshold)
                )
                conn.commit()

                # 更新冷却时间
                _cooldowns[rule_id] = datetime.now()

        finally:
            conn.close()

    @classmethod
    def _get_metric_value(cls, result: dict, metric: str):
        """从计算结果中提取指标值"""
        if metric == "carbon_reduction":
            return result.get("carbon_reduction")
        if metric == "cumulative_reduction":
            return result.get("cumulative_reduction")

        # 尝试从 breakdown 中获取
        breakdown = result.get("breakdown", {})
        return breakdown.get(metric)

    @classmethod
    def _is_in_cooldown(cls, rule_id: int, cooldown_minutes: int) -> bool:
        """检查是否在冷却时间内"""
        last_trigger = _cooldowns.get(rule_id)
        if last_trigger is None:
            return False
        elapsed = (datetime.now() - last_trigger).total_seconds() / 60
        return elapsed < cooldown_minutes
