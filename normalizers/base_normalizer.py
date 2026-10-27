"""
数据前置归一化基类 — 每个企业 / 平台的数据格式转换器继承此类。

背景
────
MQTT 只规定「怎么传」（发布/订阅），不规定「传什么」（报文格式）。
各家平台报文格式都不一样，所以系统内部只认一种统一格式 —— 以项目公式的
`required_raw_fields` 为准（如空压站是 cumulative_flow / cumulative_power）。

外部数据进来先经过这里转换成内部格式，再交给 DataCollector / 公式 hook，
下游完全无感。一个企业的对接 = 一个文件，实现两个**抽象方法**：
- `convert_mqtt(topic, payload)`：MQTT 实时通道，一条消息 → 一条内部记录
- `convert_api(alias, record)`：历史 API 通道，一条记录 → (内部字段, 数值, 规范时间)

基类**不假设任何平台的报文格式** —— 字段名怎么取、别名怎么反查、时间怎么解析，
都是各企业文件自己的事（见 xinzuan.py 示例）。

自动发现（与 hooks/ 公式同一套规则）
────────────────────────────────────
系统扫描 normalizers/ 目录，自动注册所有转换器：
- 排除 _ 开头、base_normalizer.py 等文件
- 类必须继承 BaseDataNormalizer
- normalizer_name 必须与文件名一致（如 my_corp.py → "my_corp"）
"""

import time
from datetime import datetime


class BaseDataNormalizer:
    """数据格式转换器基类"""

    # ── 元信息（子类必须定义）──
    normalizer_name: str = ""        # 唯一标识，如 "my_corp"（必须与文件名一致）
    normalizer_label: str = ""       # 显示名称，如 "某某企业（某某平台）"
    normalizer_version: str = "1.0"  # 版本号
    description: str = ""            # 适用平台 / 设备描述

    # ── 平台建议的采集节奏（API 通道用；子类按需覆盖）──
    # API 通道是系统主动轮询，多久拉一次由**平台数据更新频率**决定，写在这里
    # 而不是全局采集 tick（全局 60 秒对 API 平台太密）。采集器对 api 机器按
    # poll_interval_seconds 节流，default_record_time_type 是机器没配粒度时的默认值。
    poll_interval_seconds: int = 3600          # 建议拉取间隔（秒），默认 1 小时
    default_record_time_type: str = "hour"     # 默认时间粒度：hour / day / month
    backfill_chunk_days: int = 0               # 回溯分块大小（天），0 = 用适配器默认分块
                                               # 平台单次窗口有限制时设为 1（按天分块）

    def __init__(self, params: dict = None):
        """
        初始化转换器。

        参数:
            params: 机器数据源配置里 "normalizer": {"hook": "<企业标识>", "params": {...}}
                    的 params 部分，用于同一平台不同站点/字段名的微调，可为空。
        """
        self.params = params or {}
        self._buffer = {}  # 多主题单指标平台的缓冲：字段名 -> 最新数值

    # ── MQTT 实时通道：一条消息 → 一条内部记录（或 None 表示指标未集齐）──

    def convert_mqtt(self, topic: str, payload: dict) -> dict | None:
        """
        ★ MQTT 通道转换 — 每个转换器必须实现。

        参数:
            topic:   MQTT 主题（如 <站点ID>/read/<指标别名>）
            payload: 已解析的 JSON（dict）

        返回:
            dict: 转换后的内部统一格式记录，输出给 DataCollector 触发计算
            None: 本条消息不产出完整记录（如多主题单指标平台只到了一半指标），
                  系统继续等下一条
        """
        raise NotImplementedError(
            f"转换器 {self.normalizer_name} 未实现 convert_mqtt()"
        )

    # ── 历史 API 通道：一条记录 → (内部字段, 数值, 规范时间) ──

    def convert_api(self, alias: str, record: dict):
        """
        ★ API 通道转换 — 每个转换器必须实现。

        参数:
            alias:  该条记录所属的指标别名（请求时用的那个）
            record: 平台返回的一条记录（结构由平台自己决定）

        返回:
            tuple(内部字段名, 数值, 规范时间 "YYYY-MM-DD HH:MM:SS")，无法映射返回 None
        """
        raise NotImplementedError(
            f"转换器 {self.normalizer_name} 未实现 convert_api()"
        )

    # ── 历史 API 通道：平台怎么发起一次历史查询（平台专属，每个转换器必须实现）──

    def build_history_request(self, base_url: str, site_id: str, begin_s: str,
                              end_s: str, aliases: dict, record_time_type: str):
        """
        ★ API 通道：构造一次历史查询请求。每个转换器必须实现。

        各平台历史接口的地址拼法、请求体结构都不一样 —— 这些是**平台专属**的，
        所以不放适配器里，由各企业文件自己写。

        参数:
            base_url         公司级平台基地址（「设置 → 企业配置」里的 history_api_base）
            site_id          站点 ID（机器配置的 site_id）
            begin_s / end_s  本次查询时间窗（已按 time_format 格式化好的字符串）
            aliases          内部字段名 → 平台别名 的**全部映射**（一次请求查所有测点）
            record_time_type 时间粒度（平台各自含义，如 hour/day/month 或 normal）

        返回:
            tuple(url, headers, body)
                url     完整请求地址
                headers 额外请求头 dict（会叠加在认证头之上，可覆盖认证）
                body    请求体 dict（已填好 begin/end/aliases，由适配器 json 序列化）
        """
        raise NotImplementedError(
            f"转换器 {self.normalizer_name} 未实现 build_history_request()"
        )

    def parse_history_response(self, alias: str, result):
        """
        ★ API 通道：从一次查询的响应 JSON 里取出该 alias 的记录列表。每个转换器必须实现。

        各平台响应结构不一样（有的按 alias 键分组、有的平铺数组），所以这是平台专属。

        参数:
            alias:  请求时用的指标别名
            result: 响应解析后的 JSON（dict 或其它）

        返回:
            list[dict]: 该 alias 的记录列表（每条结构由平台决定，再交给 convert_api 解析）
        """
        raise NotImplementedError(
            f"转换器 {self.normalizer_name} 未实现 parse_history_response()"
        )

    # ── 认证 / 前置请求（平台专属，子类按需覆盖）──

    def auth_headers(self, base_url: str, username: str, password: str) -> dict:
        """返回认证请求头（如 {"Authorization": "Bearer <token>"}）。

        平台若需要「先登录拿 token 再请求」，在这里实现（含 token 缓存与续期）。
        默认空实现 = 无认证。
        """
        return {}

    def on_auth_failure(self, base_url: str, username: str, password: str) -> dict:
        """认证失效（如 401）时被调用：清除缓存 token、重新认证，返回新请求头。

        默认空实现 = 无认证，不做任何事。
        """
        return {}

    def list_projects(self, base_url: str, username: str, password: str) -> list:
        """返回平台可见的项目列表 [{projectName, dataId}, ...]。

        用于前端「获取站点列表」辅助填 site_id。默认空实现 = 不支持。
        """
        return []

    def build_batch_request(self, base_url: str, data_ids: list, begin_s: str,
                            end_s: str, aliases: dict, record_time_type: str):
        """构造一次**批量**历史查询（一次查多个 dataId），返回 (url, headers, body)。

        平台支持批量接口时实现（如 POST /data/batch）；不支持则用默认实现（抛错，
        上层回退到逐台单查）。
        """
        raise NotImplementedError(
            f"转换器 {self.normalizer_name} 未实现 build_batch_request()"
        )

    def parse_batch_response(self, result):
        """解析批量查询响应，返回 (results, errors)。

        results: list[dict]，每项含 dataId 与 series（结构与单查一致的记录分组）
        errors:  list[dict]，每项含 dataId 与 message
        """
        return [], []

    # ── 便捷工具（子类按需使用，非必用）──

    def _emit_when_all(self, partial: dict, required: list) -> dict | None:
        """
        「多主题单指标」平台通用：缓冲 partial 中的指标，required 全部到齐时输出一条记录。
        适用：一条消息只带一个指标、要等几个指标凑齐才算一条完整数据的平台。
        别的平台（一条消息全字段）用不到。

        参数:
            partial:  本条消息解析出的 {内部字段名: 数值}
            required: 需要集齐的字段名列表，如 ["cumulative_flow", "cumulative_power"]

        返回:
            dict: 完整记录（含 timestamp），齐了才返回
            None: 还没齐，继续等下一条消息
        """
        for k, v in partial.items():
            try:
                self._buffer[k] = float(v)
            except (TypeError, ValueError):
                pass
        if not all(k in self._buffer for k in required):
            return None
        record = {k: self._buffer[k] for k in required}
        record["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        return record

    @staticmethod
    def _norm_time(record_time) -> str | None:
        """'YYYY-MM-DDTHH:MM:SS'（或带毫秒）→ 'YYYY-MM-DD HH:MM:SS'；失败 None。"""
        if not record_time:
            return None
        s = str(record_time).replace("T", " ").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                return datetime.strptime(s[:26], fmt).strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        return None

    @staticmethod
    def dig(obj, path: str):
        """按点号路径从嵌套 dict 里取值；取不到返回 None。"""
        if not path:
            return obj
        cur = obj
        for key in path.split("."):
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                return None
        return cur
