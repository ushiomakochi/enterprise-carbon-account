"""
新转换器开发模板
═══════════════════════════════════════════════════════════════

使用方法:
    1. 复制到 enterprises/<企业id>/normalizers/，重命名为平台标识（如 my_corp.py）
    2. 类继承 BaseDataNormalizer，normalizer_name 必须与文件名一致
    3. 声明 aliases（内部字段 ↔ 平台别名，MQTT 与 API 通道共用）
    4. 按需实现各方法（哪些通道用就实现哪些）：
       - convert_mqtt            MQTT 实时通道（抽象，必须实现）
       - convert_api             历史 API 单条记录映射（抽象，必须实现）
       - build_history_request   单机历史查询的 URL/请求体（抽象，必须实现）
       - parse_history_response  单机响应解析（抽象，必须实现）
       - auth_headers            认证头（可选，默认无认证；有登录前置时实现）
       - on_auth_failure         401 重登（可选）
       - list_projects           站点列表（可选，前端「获取站点列表」用）
       - build_batch_request / parse_batch_response  批量查询（可选，机器多时用）
    5. 机器 normalizer_config 中写: {"hook": "你的文件名"}
    6. 重启系统，自动注册生效

注意事项:
    - normalizer_name 必须与文件名一致（不含 .py），这是自动发现的标识
    - 输出字段名以该项目公式的 required_raw_fields 为准
    - convert_mqtt：对方一条消息只有一个指标时，用 _emit_when_all() 缓冲判齐
    - convert_api：把平台返回的一条历史记录映射成 (内部字段, 数值, 规范时间)
"""

from .base_normalizer import BaseDataNormalizer


class TemplateNormalizer(BaseDataNormalizer):
    """模板数据转换器 — 复制后按对方平台实际报文修改"""

    normalizer_name = "template"            # ★ 改为唯一标识（必须与文件名一致）
    normalizer_label = "模板转换器"          # ★ 改为显示名称
    normalizer_version = "1.0"
    description = "在此描述适用的平台 / 设备报文格式"

    # ★ 平台建议采集节奏（API 通道用）：多久拉一次 + 默认粒度。不写用基类默认（1 小时/hour）
    poll_interval_seconds = 24 * 3600       # ★ 按平台数据更新频率设，如每天一次
    default_record_time_type = "normal"     # ★ 默认粒度（平台各自的取值）
    backfill_chunk_days = 1                 # ★ 回溯分块大小（天），单次窗口受限时设 1

    # ★ 内部字段名 → 平台别名（MQTT topic 尾段 / 历史 API 的 alias 共用）
    aliases = {
        "cumulative_flow": "flow-alias",   # ★ 改成平台实际的产气量别名
        "cumulative_power": "power-alias", # ★ 改成平台实际的用电量别名
    }

    def _field_for_alias(self, alias: str):
        """由平台别名反查内部字段名（本平台用别名区分指标时才会用到）。"""
        for field, a in self.aliases.items():
            if a == alias:
                return field
        return None

    # ── 认证（可选）：平台有登录前置时实现 ──
    def auth_headers(self, base_url, username, password):
        """返回认证请求头，如 {"Authorization": "Bearer <token>"}。默认无认证返回 {}。"""
        # 示例：POST {base}/auth/login 拿 token 缓存，返回 Bearer
        return {}

    def on_auth_failure(self, base_url, username, password):
        """401 时清缓存重登，返回新请求头。"""
        return self.auth_headers(base_url, username, password)

    # ── MQTT 实时通道 ──
    def convert_mqtt(self, topic: str, payload: dict) -> dict | None:
        """
        示例 A —— 一条消息含全部指标（单主题多指标）:
            return {"cumulative_flow": float(payload.get("flow", 0)),
                    "cumulative_power": float(payload.get("elec", 0)),
                    "timestamp": payload.get("time", "")}
        示例 B —— 一条消息一个指标（多主题单指标）:
            alias = topic.rstrip("/").split("/")[-1]
            field = self._field_for_alias(alias)
            if not field: return None
            value = self.dig(payload, "value.value")
            return self._emit_when_all({field: value}, list(self.aliases.keys()))
        """
        return None

    # ── 历史 API 通道：单条记录映射 ──
    def convert_api(self, alias: str, record: dict):
        """
        把平台返回的一条历史记录映射成 (内部字段, 数值, 规范时间)。
        示例（{"value": 123, "recordTime": "2026-09-01T00:00:00"}）:
            field = self._field_for_alias(alias)
            if field is None: return None
            value = self.dig(record, "value")
            ts = self._norm_time(self.dig(record, "recordTime"))
            if value is None or ts is None: return None
            return field, float(value), ts
        """
        return None

    # ── 历史 API 通道：单机查询的请求与响应 ──
    def build_history_request(self, base_url, site_id, begin_s, end_s, aliases, record_time_type):
        """
        构造一次历史查询，返回 (url, headers, body)。
        aliases 是「内部字段 → 平台别名」的**全部**映射（一次请求查所有测点）。
        示例（POST {base}/data/{dataId}）:
            url = f"{base_url.rstrip('/')}/data/{site_id}"
            body = {"beginDate": begin_s, "endDate": end_s,
                    "aliases": ",".join(aliases.values())}
            return url, {}, body
        """
        raise NotImplementedError("build_history_request 未实现")

    def parse_history_response(self, alias, result):
        """
        从响应里取出该 alias 的记录列表。
        示例（{"series": {"<alias>": [记录...]}}）:
            series = (result or {}).get("series", {})
            return [r for r in series.get(alias, []) if isinstance(r, dict)]
        """
        raise NotImplementedError("parse_history_response 未实现")

    # ── 站点列表（可选）：前端「获取站点列表」用 ──
    def list_projects(self, base_url, username, password):
        """返回 [{projectName, dataId}, ...]。"""
        return []

    # ── 批量查询（可选）：机器多时回溯用 ──
    def build_batch_request(self, base_url, data_ids, begin_s, end_s, aliases, record_time_type):
        """一次查多个 dataId，返回 (url, headers, body)。不实现则回溯回退逐台单查。"""
        raise NotImplementedError("build_batch_request 未实现")

    def parse_batch_response(self, result):
        """返回 (results, errors)，results 每项含 dataId + series。"""
        if not isinstance(result, dict):
            return [], []
        return result.get("results", []) or [], result.get("errors", []) or []
