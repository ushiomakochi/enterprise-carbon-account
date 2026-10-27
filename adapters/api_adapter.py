"""
API 定时拉取适配器 — 统一的数据接口通道（定时拉取 + 批量回溯）。

定位
────
这是「API」类数据源的唯一实现：每个采集 tick 按时间窗拉取一批历史记录，
逐条走 collect_and_store 记账链（首条基线 / 断档保护 / 公式计算全复用）。
批量回溯（项目管理页「回溯历史」）也走本适配器的 fetch_range()。

本适配器只做**通用传输骨架**：分块、循环、发 HTTP 请求、按时间合并、去重。
所有**平台专属**的东西（URL 怎么拼、请求体长什么样、响应怎么解析、单条记录
字段怎么映射）都下沉到前置转换文件（normalizers/ 下的企业文件）：
- `build_history_request(...)`  平台怎么构造一次历史查询
- `parse_history_response(...)` 平台响应里怎么取记录
- `convert_api(...)`            一条记录怎么映射成 (内部字段, 数值, 时间)
- `aliases`                    要查哪些平台别名

凭据模型（公司级 + 机器级分离）
──────────────────────────────
- 公司级（「设置 → 企业配置」）：平台基地址 history_api_base + 账号 history_api_username
  + 密码 history_api_password —— 一家公司一套，访问旗下所有机器。
- 机器级：data_source_config 保存站点 ID（site_id）与采集粒度；normalizer_config 选择 xinzuan。

数据源配置示例 (api 机器的 data_source_config):
{
    "site_id": "1129",
    "record_time_type": "month",
    "lookback_minutes": 120
}

字段说明:
    site_id              站点 ID（dataId），交给 build_history_request 拼 URL
    record_time_type     平台时间粒度（hour/day/month）
    lookback_minutes     每次拉取最近多长时间的窗口，默认 60
    max_records_per_run  单次最多入账多少条，默认 500
    chunk_days           回溯分块大小（天），默认按粒度：小时 31 / 天 366 / 月不分
    fill                 1 = 缺失字段用上一条已知读数补齐（累计口径允许）
    timeout              HTTP 超时秒数（默认 30）；方法/路径/认证由 xinzuan.py 定义
"""

import json
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

from .base_adapter import BaseAdapter
from .normalizer import DataNormalizer


def post_json(url: str, headers: dict, body: dict, timeout: int = 30,
              method: str = "POST", params: dict = None,
              return_details: bool = False):
    """GET 参数放 query，POST 数据放 JSON body。"""
    method = method.upper()
    if method not in ("GET", "POST"):
        raise ValueError("API 请求方法只支持 GET 或 POST")
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(
        url, data=(json.dumps(body, ensure_ascii=False).encode("utf-8")
                   if method == "POST" else None),
        method=method, headers={
            "Content-Type": "application/json",
            **(headers or {}),
        })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if return_details:
            raw = resp.read(65537)
            return {"status_code": resp.status,
                    "response_body": raw[:65536].decode("utf-8", errors="replace"),
                    "truncated": len(raw) > 65536,
                    "content_type": resp.headers.get("Content-Type", "")}
        return json.loads(resp.read().decode("utf-8"))


def merge_history_points(per_field_points: dict, fill: bool = False) -> list:
    """Align cumulative readings; preserve exact-timestamp apartValue for audit."""
    fields = list(per_field_points)
    series = {}
    for field in fields:
        series[field] = {point[0]: point for point in per_field_points[field]}
    times = sorted(set().union(*(set(v) for v in series.values()))) if fields else []
    rows, last = [], {field: None for field in fields}
    for timestamp in times:
        row, complete = {}, True
        for field in fields:
            point = series[field].get(timestamp)
            if point is not None:
                last[field] = point[1]
            value = point[1] if point is not None else (last[field] if fill else None)
            if value is None:
                complete = False
                break
            row[field] = value
            if point is not None and len(point) > 2 and point[2] is not None:
                row["apart_" + field.removeprefix("cumulative_")] = point[2]
        if complete:
            row["timestamp"] = timestamp
            rows.append(row)
    return rows


class ApiAdapter(BaseAdapter):
    adapter_type = "api"
    adapter_label = "API 定时拉取（支持回溯）"

    # 默认尽量少发请求：一次拉一大块。平台限制单次跨度时用 chunk_days 调小。
    # 鑫钻实测单次时间窗超 30 天报 400，这里统一按 28 天（留 2 天余量）。
    _CHUNK_DAYS = {"hour": 28, "day": 28, "month": 28, "normal": 28}

    def __init__(self, config: dict):
        super().__init__(config)
        self._last_error = ""
        self._normalizer = DataNormalizer(self.config.get("normalizer") or
                                          ({"hook": self.config["normalizer_hook"]}
                                           if self.config.get("normalizer_hook") else None))
        # 要查哪些平台别名，来自前置转换文件的 aliases（connect 校验 hook 存在）
        hook = self._normalizer.hook
        self._aliases = getattr(hook, "aliases", None) or {}
        if hook is not None:
            hook.aliases = self._aliases
        # 平台建议的拉取节奏：多久拉一次、默认粒度。
        # 机器配置 poll_interval_seconds 优先（数据源配置里可覆盖），否则用前置转换文件声明的默认值。
        self.poll_interval_seconds = int(self.config.get("poll_interval_seconds")
                                         or getattr(hook, "poll_interval_seconds", 3600))
        self._last_fetch_dt = None  # 上次成功拉取的结束时间（增量窗口起点）

    def _enterprise_creds(self):
        """公司级平台基地址 + 账号 + 密码（惰性读，避免循环 import）。"""
        from core.project_manager import get_enterprise_config
        cfg = get_enterprise_config() or {}
        return (cfg.get("history_api_base") or "",
                cfg.get("history_api_username") or "",
                cfg.get("history_api_password") or "")

    def _authorized_post(self, hook, base_url, username, password, url,
                         extra_headers, body, timeout, method, params=None,
                         return_details=False):
        """带认证发 POST；401 时调 on_auth_failure 重登并重试一次。"""
        headers = {**hook.auth_headers(base_url, username, password),
                   **(extra_headers or {})}
        try:
            return post_json(url, headers, body, timeout, method, params,
                             return_details=return_details)
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise
            # 认证失效（JWT 过期/无效）→ 重登后重试一次
            headers = {**hook.on_auth_failure(base_url, username, password),
                       **(extra_headers or {})}
            return post_json(url, headers, body, timeout, method, params,
                             return_details=return_details)

    def _history_request(self, hook, base_url, username, password, site_id,
                         begin_s, end_s, record_time_type):
        """鑫钻接口的 URL、Headers 和 Data 只由转换文件定义。"""
        url, headers, body = hook.build_history_request(
            base_url, site_id, begin_s, end_s, self._aliases, record_time_type)
        return url, headers, {}, body, getattr(hook, "history_method", "POST")

    def connect(self) -> bool:
        """校验配置齐全（无长连接）"""
        self._last_error = ""
        hook = self._normalizer.hook
        if hook is None:
            self._last_error = "API 数据源必须选择前置转换文件"
            print(f"[API] {self._last_error}")
            return False
        if not self.config.get("site_id"):
            self._last_error = "配置缺少 site_id（站点 ID）"
            print(f"[API] {self._last_error}")
            return False
        if not self._aliases:
            self._last_error = "前置转换文件未声明 aliases（内部字段 → 平台指标别名）"
            print(f"[API] {self._last_error}")
            return False
        base_url, _u, _p = self._enterprise_creds()
        if not base_url:
            self._last_error = "缺少平台地址：请在「设置 → 企业配置」填历史 API 基地址"
            print(f"[API] {self._last_error}")
            return False
        self._connected = True
        return True

    def test_request(self, begin: datetime, end: datetime) -> dict:
        """按当前配置发送一个业务查询，返回原始 HTTP 响应；不入账、不改采集游标。"""
        if begin >= end:
            raise ValueError("测试结束时间必须晚于开始时间")
        if not self.connect():
            raise ValueError(self._last_error)
        hook = self._normalizer.hook
        base_url, username, password = self._enterprise_creds()
        site_id = self.config.get("site_id", "")
        time_format = self.config.get("time_format", "%Y-%m-%d %H:%M:%S")
        rtt = self.config.get("record_time_type") or getattr(
            hook, "default_record_time_type", "hour")
        begin_s, end_s = begin.strftime(time_format), end.strftime(time_format)
        url, extra_headers, params, body, method = self._history_request(
            hook, base_url, username, password, site_id, begin_s, end_s,
            rtt)
        info = {"method": method, "begin": begin_s, "end": end_s}
        try:
            response = self._authorized_post(
                hook, base_url, username, password, url, extra_headers, body,
                int(self.config.get("timeout", 30)), method, params,
                return_details=True)
            return {**info, **response}
        except urllib.error.HTTPError as exc:
            raw = exc.read(65537)
            return {**info, "status_code": exc.code,
                    "response_body": raw[:65536].decode("utf-8", errors="replace"),
                    "truncated": len(raw) > 65536,
                    "content_type": exc.headers.get("Content-Type", "") if exc.headers else ""}

    def fetch_latest(self) -> dict:
        """BaseAdapter 契约；返回窗口内最新一条。"""
        rows = self.fetch_history()
        return rows[-1] if rows else {}

    def fetch_history(self) -> list:
        """拉取自上次成功拉取以来到现在的数据（增量窗口：不漏、不重）。

        首次拉取用 lookback_minutes 兜底；之后窗口起点 = 上次成功拉取时间，
        配合采集器的 poll_interval 节流（如 15 天拉一次，窗口也覆盖 15 天跨度）。
        查询失败时抛异常，让采集器下个 tick 快速重试，别被节流冻结。
        """
        end = datetime.now()
        if self._last_fetch_dt is None:
            begin = end - timedelta(minutes=int(self.config.get("lookback_minutes", 60)))
        else:
            begin = self._last_fetch_dt
        rows = self.fetch_range(begin, end)
        if not rows and self._last_error:
            raise RuntimeError(self._last_error)
        self._last_fetch_dt = end
        return rows

    def fetch_range(self, begin: datetime, end: datetime) -> list:
        """
        拉取 [begin, end] 区间全部历史记录，按粒度分块，返回内部格式记录（升序）。
        批量回溯走这里。传输骨架通用，平台差异交给前置转换文件。
        """
        self._last_error = ""
        hook = self._normalizer.hook
        base_url, username, password = self._enterprise_creds()
        timeout = int(self.config.get("timeout", 30))
        fill = bool(int(self.config.get("fill", 0)))
        max_records = int(self.config.get("max_records_per_run", 500))
        time_format = self.config.get("time_format", "%Y-%m-%d %H:%M:%S")
        site_id = self.config.get("site_id", "")
        # 粒度：机器配置优先，否则用前置转换文件声明的默认粒度
        rtt = self.config.get("record_time_type") or getattr(hook, "default_record_time_type", "hour")
        # 分块：机器配置 > 前置转换文件 backfill_chunk_days > 按粒度的默认值
        chunk_days = int(self.config.get("chunk_days")
                         or getattr(hook, "backfill_chunk_days", 0)
                         or self._CHUNK_DAYS.get(rtt, 31))
        chunk = timedelta(days=chunk_days)

        by_ts = {}
        cur = begin
        while cur <= end:
            chunk_end = min(cur + chunk, end)
            begin_s, end_s = cur.strftime(time_format), chunk_end.strftime(time_format)
            # ① 平台怎么构造这次查询（URL / 请求体）→ 前置转换文件决定；一次请求查全部别名
            try:
                url, extra_headers, params, body, request_method = self._history_request(
                    hook, base_url, username, password, site_id, begin_s, end_s, rtt)
                raw = self._authorized_post(
                    hook, base_url, username, password, url, extra_headers,
                    body, timeout, request_method, params)
            except Exception as e:
                self._last_error = f"查询失败: {e}"
                print(f"[API] 块 {begin_s} ~ {end_s} {self._last_error}")
                cur = chunk_end + timedelta(seconds=1)
                continue
            per_field = {}
            for field, alias in self._aliases.items():
                records = hook.parse_history_response(
                    alias, raw)
                pts = []
                for rec in records:
                    # ② 一条记录怎么映射成 (内部字段, 数值, 时间) → 前置转换文件决定
                    convert = getattr(hook, "convert_api_with_apart", hook.convert_api)
                    mapped = convert(alias, rec)
                    if mapped is None:
                        continue
                    _f, v, ts = mapped[:3]
                    try:
                        pts.append((ts, float(v), mapped[3] if len(mapped) > 3 else None))
                    except (TypeError, ValueError):
                        continue
                per_field[field] = pts
            # ③ 按时间对齐合并（通用）
            for row in merge_history_points(per_field, fill=fill):
                by_ts[row["timestamp"]] = row
            cur = chunk_end + timedelta(seconds=1)

        rows = [by_ts[t] for t in sorted(by_ts)]
        if len(rows) > max_records:
            rows = rows[-max_records:]
        if rows:
            print(f"[API] 拉取 {begin.strftime(time_format)} ~ {end.strftime(time_format)}，"
                  f"{len(rows)} 条记录（{rows[0]['timestamp']} ~ {rows[-1]['timestamp']}）")
        return rows

    def fetch_batch(self, data_ids: list, begin: datetime, end: datetime):
        """一次批量拉取多个 dataId（平台支持 batch 时用），返回 (results, errors)。

        用于批量回溯：同公司的多台机器合并成一次请求，减少请求次数。
        平台不支持 batch（build_batch_request 未实现）会抛 NotImplementedError，调用方回退单机。
        """
        hook = self._normalizer.hook
        base_url, username, password = self._enterprise_creds()
        timeout = int(self.config.get("timeout", 30))
        method = getattr(hook, "batch_method", "POST")
        time_format = self.config.get("time_format", "%Y-%m-%d %H:%M:%S")
        rtt = self.config.get("record_time_type") or getattr(hook, "default_record_time_type", "hour")
        begin_s, end_s = begin.strftime(time_format), end.strftime(time_format)
        url, extra_headers, body = hook.build_batch_request(
            base_url, data_ids, begin_s, end_s, self._aliases, rtt)
        raw = self._authorized_post(hook, base_url, username, password,
                                    url, extra_headers, body, timeout, method)
        return hook.parse_batch_response(raw)

    def disconnect(self):
        self._connected = False
