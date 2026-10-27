"""
促进会 VCER API 客户端 — 企业碳账户系统通过此客户端与促进会通信。

封装所有 VCER API 调用，包括:
- 碳资产查询
- 项目信息查询
- 基准线参数获取
- 项目申请与阶段提交
- 进度查询
- 企业汇总同步
"""

import json
import os
import re
import time

_requests = None

# ── 密钥清洗 ──
# 密钥本体是 `secrets.token_urlsafe(32)` 生成的，43 位，字符集只有
# [A-Za-z0-9_-]。但粘贴时极易带上包装文字：设置页说明、`Authorization: Bearer `、
# `VCER_API_KEY=`、引号、换行……而 **HTTP 头只能是 latin-1**，混进一个中文就会抛
#     'latin-1' codec can't encode characters in position 7-37
# —— 报错完全看不出是"密钥粘错了"，所以这里统一先还原成裸密钥。
_NON_ASCII_HDR_MSG = (
    "请求头里有非 ASCII 字符（HTTP 头只能是 latin-1），九成是 API 密钥粘贴时"
    "带进了中文/换行。请到「设置 → 企业配置 → 促进会 API 密钥」重新粘贴密钥本体"
    "（字母/数字/-/_ 组成的一长串，约 43 位）。"
)

# 裸密钥：**整串**只由 token 字符组成（不限长度，短密钥也认）
_KEY_BARE_RE = re.compile(r"[A-Za-z0-9_\-]+")
# 从一段文字里"捞"密钥用：至少 20 位，免得把 Authorization 这种英文单词捞出来
_KEY_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{20,}")


def clean_access_key(raw) -> str:
    """把粘贴进来的文本还原成裸密钥；还原不出来就返回空串。

    已经粘错的存量数据也能靠它自愈（不用再去设置页重贴一遍）：
    从整段文字里挑出**最长的 token 形状子串**。

    ⚠️ 判据是"**整串**只由 token 字符组成"，不是"是 ASCII 就行" ——
    `VCER_API_KEY=xxx` 也是纯 ASCII，只判 ASCII 的话会把它整串当密钥发出去。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if _KEY_BARE_RE.fullmatch(s):
        return s
    hits = _KEY_TOKEN_RE.findall(s)
    if not hits:
        return ""
    return max(hits, key=len)

# 进程内轮次结果缓存：key=(query_key, stage) → (expires_at, result)，短 TTL 减少重复远端调用
_ROUNDS_CACHE = {}
_ROUNDS_CACHE_TTL = 10  # 秒

# ── 核查轮次「已通过」的认定 ──
# 促进会不同时期的数据文案不完全统一（已通过 / 通过 / 已完成）。
# **必须只有这一份**：计算口径（core/gap_bridge.py）和报告编号
# （core/reports/context_builder.py）原先各写各的白名单，一旦平台回的是「通过」，
# 算减排量那边会把它丢掉 → 该扣的不扣 → 整段当未核查补记，重复计算。
PASSED_STATUSES = ("已通过", "通过", "已完成")


def is_passed_status(status) -> bool:
    """该核查轮次是否算「已通过」。"""
    return str(status or "").strip() in PASSED_STATUSES


def _r():
    """惰性导入 requests（避免启动时从网络盘加载，加快应用启动）。"""
    global _requests
    if _requests is None:
        import requests
        _requests = requests
    return _requests


# 密钥解析结果缓存。
# 客户端是**按请求**构造的（app.py / code_reservation.py 里到处都是
# `VCERClient(...)`），而企业配置存在 SQLite 里、本机还是网络盘 ——
# 不加缓存就等于每个请求都去读一次库。设置页保存后会显式失效，所以 30 秒的
# TTL 只是兜底（比如直接改了库）。
_KEY_CACHE = {"value": None, "expires": 0.0}
_KEY_CACHE_TTL = 30  # 秒


def invalidate_key_cache():
    """让下次解析重新读企业配置（「设置」页保存密钥后调用）。"""
    _KEY_CACHE["value"] = None
    _KEY_CACHE["expires"] = 0.0


def _key_from_enterprise_config() -> str:
    """从「设置 → 企业配置」里读密钥（enterprise_config.api_access_key）。

    读出来先过一道 clean_access_key()：库里可能存着"粘多了"的值
    （带说明文字/换行/中文），那种值直接当请求头发出去会抛
    latin-1 编码错误，报错信息完全看不出是密钥的问题。
    """
    now = time.time()
    if _KEY_CACHE["expires"] > now:
        return _KEY_CACHE["value"] or ""
    raw = ""
    try:
        from core import project_manager       # 惰性导入，避免导入期循环依赖
        cfg = project_manager.get_enterprise_config() or {}
        raw = cfg.get("api_access_key") or ""
    except Exception:
        raw = ""
    val = clean_access_key(raw)
    if raw and not val:
        print(f"[VCER] 警告：企业配置里的 API 密钥无法识别（{len(raw)} 字符，"
              f"含非 ASCII 或空白），已按「未配置」处理。"
              f"请到「设置 → 企业配置 → 促进会 API 密钥」重新粘贴密钥本体。", flush=True)
    elif raw and raw != val:
        print(f"[VCER] 提示：企业配置里的 API 密钥带了多余内容（{len(raw)} 字符），"
              f"已自动取其中的密钥本体（{len(val)} 字符）。", flush=True)
    _KEY_CACHE["value"] = val
    _KEY_CACHE["expires"] = now + _KEY_CACHE_TTL
    return val


def _default_access_key() -> str:
    """默认密钥，按优先级：

      1. **「设置 → 企业配置 → 促进会 API 密钥」**（enterprise_config.api_access_key）
         —— 跟 vcer_api_url 一样走企业配置优先的约定，也就是界面上填的那个
      2. 环境变量 `VCER_API_KEY`
      3. `config.VCER_API_KEY`

    促进会的碳数据接口（carbon_assets / carbon_projects / enterprise_summary）
    已启用 Bearer 鉴权，不带密钥会 401。企业碳账户这类要读**多家**企业的调用方，
    必须用**系统级密钥**（enterprise_id=SYS）；生成方式见促进会那边
    `python make_carbon_api_key.py`（不带参数即生成 SYS 密钥）。
    """
    key = _key_from_enterprise_config()
    if key:
        return key
    key = clean_access_key(os.getenv("VCER_API_KEY"))
    if key:
        return key
    try:
        import config                      # 惰性导入，避免与调用方的导入顺序打架
        return clean_access_key(getattr(config, "VCER_API_KEY", ""))
    except Exception:
        return ""


class VCERClient:
    """促进会 VCER API 客户端"""

    def __init__(self, api_base_url: str, access_key: str = None):
        """
        初始化客户端。

        参数:
            api_base_url: 促进会 API 基础地址
                          例如: http://api.environdec.cn
            access_key: 可选的 API 访问密钥。
                        **不传就自动取 VCER_API_KEY / config.VCER_API_KEY。**
        """
        self.base_url = api_base_url.rstrip("/")
        # ⚠️ 这里以前只把 access_key 存下来、**从没往请求头里放过** ——
        #    等于所有调用都是匿名的，促进会那边一套 Bearer 鉴权就全 401。
        self._explicit_key = (access_key or "").strip()
        self.access_key = self._explicit_key or _default_access_key()
        self._session = None

    @staticmethod
    def clean_access_key(raw) -> str:
        """把粘贴进来的文本还原成裸密钥（转发到模块级的同名函数）。

        放在类上是为了调用方一句 `VCERClient.clean_access_key(x)` 就够，
        不用额外 import（app.py 的 /api/config 要拿它做保存前校验）。
        """
        return clean_access_key(raw)

    @staticmethod
    def invalidate_key_cache():
        """让下次解析重新读企业配置（「设置」页保存密钥后调用）。

        只是个转发：真正干活的是模块级的 invalidate_key_cache()。
        放在类上是为了调用方一句 `VCERClient.invalidate_key_cache()` 就够
        —— 类里没这个名字的话会 AttributeError（app.py 一开始就是这么写错的）。
        """
        invalidate_key_cache()

    def _ensure_session(self):
        """惰性创建 requests.Session（首次真正调用 API 时才 import requests）。"""
        if self._session is None:
            self._session = _r().Session()
            self._session.headers.update({
                "User-Agent": "CarbonAccountClient/1.0",
                "Accept": "application/json",
            })
            if self.access_key:
                self._session.headers["Authorization"] = f"Bearer {self.access_key}"
        return self._session

    def _http_error(self, e):
        """把 HTTPError 说成人话（401/403 最常见，直接点出该怎么办）。"""
        code = getattr(getattr(e, "response", None), "status_code", 0)
        if code in (401, 403):
            # 项目配置里单独填过密钥时，它会盖过全局 VCER_API_KEY ——
            # 而那个字段在这之前**从没被真正用过**，存的值很可能已失效，
            # 是最容易被忽略的"我明明配了全局密钥还是 401"的原因。
            override_hint = (
                " ⚠ 本次用的是「项目配置里单独填的 API 访问密钥」"
                "（它会盖过全局 VCER_API_KEY）。如果它已失效，"
                "请到该项目的设置里清空它，改用全局配置。"
            ) if self._explicit_key else ""
            if code == 401:
                return ("促进会 API 拒绝了访问（HTTP 401）：未带密钥或密钥无效。"
                        "请到「⚙ 设置 → 企业配置 → 促进会 API 密钥」填写 —— 密钥由促进会用 "
                        "`python make_carbon_api_key.py` 生成（要不带参数的默认执行，"
                        "生成的 SYS 系统密钥才能跨企业读取）。" + override_hint)
            return ("促进会 API 拒绝了访问（HTTP 403）：密钥有效但无权读这个企业。"
                    "只有系统级密钥（enterprise_id=SYS）才能跨企业读取。"
                    + override_hint)
        detail = ""
        try:
            detail = f"，响应: {e.response.text[:300]}"
        except Exception:
            pass
        return f"API 返回错误 (HTTP {code}){detail}"

    def _get(self, path: str, params: dict = None) -> dict:
        """通用 GET 请求"""
        url = f"{self.base_url}{path}"
        try:
            resp = self._ensure_session().get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except _r().exceptions.ConnectionError:
            return {"status": "失败", "msg": f"无法连接到促进会 API ({self.base_url})"}
        except _r().exceptions.Timeout:
            return {"status": "失败", "msg": "请求超时，请稍后重试"}
        except _r().exceptions.HTTPError as e:
            return {"status": "失败", "msg": self._http_error(e)}
        except UnicodeEncodeError:
            # 保险丝：正常路径已由 clean_access_key() 保证密钥是纯 ASCII，
            # 万一将来别的地方往请求头塞了非 latin-1 的东西，也别再抛
            # 那句看不懂的 "'latin-1' codec can't encode characters in position 7-37"。
            return {"status": "失败", "msg": _NON_ASCII_HDR_MSG}
        except Exception as e:
            return {"status": "失败", "msg": str(e)}

    def _post(self, path: str, data: dict = None, files: dict = None) -> dict:
        """通用 POST 请求"""
        url = f"{self.base_url}{path}"
        try:
            if files:
                resp = self._ensure_session().post(url, data=data, files=files, timeout=30)
            else:
                resp = self._ensure_session().post(url, json=data, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except _r().exceptions.ConnectionError:
            return {"status": "失败", "msg": f"无法连接到促进会 API ({self.base_url})"}
        except _r().exceptions.Timeout:
            return {"status": "失败", "msg": "请求超时，请稍后重试"}
        except _r().exceptions.HTTPError as e:
            return {"status": "失败", "msg": self._http_error(e)}
        except UnicodeEncodeError:
            # 保险丝：正常路径已由 clean_access_key() 保证密钥是纯 ASCII，
            # 万一将来别的地方往请求头塞了非 latin-1 的东西，也别再抛
            # 那句看不懂的 "'latin-1' codec can't encode characters in position 7-37"。
            return {"status": "失败", "msg": _NON_ASCII_HDR_MSG}
        except Exception as e:
            return {"status": "失败", "msg": str(e)}

    def post_form(self, path: str, data: dict = None, files: dict = None) -> dict:
        """
        POST multipart/form-data（用于文件上传场景）。

        VCER API 返回格式为 {success: true/false, message: "..."}，
        此方法自动转换为本系统统一的 {status: "成功"/"失败", msg: "..."} 格式。

        参数:
            path: API 路径，如 /cn/api/vcer/create_project
            data: 表单字段字典
            files: {field_name: (filename, file_bytes, content_type), ...}

        返回:
            dict: {status: "成功", ...} 或 {status: "失败", msg: "..."}
        """
        url = f"{self.base_url}{path}"
        try:
            # 构建文件元组 (filename, file_data, content_type)
            file_tuples = {}
            if files:
                for field_name, file_info in files.items():
                    if isinstance(file_info, tuple) and len(file_info) == 2:
                        filename, file_data = file_info
                        # 根据扩展名推断 content_type
                        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
                        mime_map = {
                            'pdf': 'application/pdf',
                            'doc': 'application/msword',
                            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                            'xls': 'application/vnd.ms-excel',
                            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
                            'zip': 'application/zip',
                        }
                        content_type = mime_map.get(ext, 'application/octet-stream')
                        file_tuples[field_name] = (filename, file_data, content_type)
                    elif isinstance(file_info, tuple) and len(file_info) == 3:
                        file_tuples[field_name] = file_info  # 已含 content_type

            resp = self._ensure_session().post(url, data=data or {}, files=file_tuples if file_tuples else None, timeout=60)
            resp.raise_for_status()
            result = resp.json()

            # 统一响应格式：VCER API 用 success/message，本系统用 status/msg
            if "success" in result and "status" not in result:
                if result["success"]:
                    return {"status": "成功", **{k: v for k, v in result.items() if k not in ("success", "message")},
                            "msg": result.get("message", ""), "raw": result}
                else:
                    return {"status": "失败", "msg": result.get("message", "提交失败"), "raw": result}
            return result

        except _r().exceptions.ConnectionError:
            return {"status": "失败", "msg": f"无法连接到促进会 API ({self.base_url})"}
        except _r().exceptions.Timeout:
            return {"status": "失败", "msg": "上传超时，文件可能较大，请稍后重试"}
        except _r().exceptions.HTTPError as e:
            return {"status": "失败", "msg": self._http_error(e)}
        except UnicodeEncodeError:
            # 保险丝：正常路径已由 clean_access_key() 保证密钥是纯 ASCII，
            # 万一将来别的地方往请求头塞了非 latin-1 的东西，也别再抛
            # 那句看不懂的 "'latin-1' codec can't encode characters in position 7-37"。
            return {"status": "失败", "msg": _NON_ASCII_HDR_MSG}
        except Exception as e:
            return {"status": "失败", "msg": str(e)}

    # ══════════════════════════════════════════════════════════
    # 碳资产查询（已有 API）
    # ══════════════════════════════════════════════════════════

    def get_carbon_assets(self, enterprise_id: str, project_code: str = None) -> dict:
        """
        查询企业碳资产。

        GET /cn/api/carbon_assets?enterprise_id=036&project_code=GDES-VCER-036-1

        返回:
            { "status": "成功", "total_carbon_assets": 3023.89,
              "available_assets_gross": 565.90, "available_assets_net": 70.48,
              "traded_amount": 495.42 }
        """
        params = {"enterprise_id": enterprise_id}
        if project_code:
            params["project_code"] = project_code
        return self._get("/cn/api/carbon_assets", params)

    def get_carbon_projects(self, enterprise_id: str) -> dict:
        """
        查询企业 VCER 项目列表。

        GET /cn/api/carbon_projects?enterprise_id=036

        返回:
            { "status": "成功", "enterprise_id": "036", "projects": [...] }
        """
        return self._get("/cn/api/carbon_projects", {"enterprise_id": enterprise_id})

    # ══════════════════════════════════════════════════════════
    # 基准线参数（已有 API）
    # ══════════════════════════════════════════════════════════

    def get_baseline_params(self, dew_point: float, rated_pressure: float,
                            avg_flow_rate: float, has_oil: str = None) -> dict:
        """
        获取空气压缩站基准线参数。

        GET /cn/api/air_compressor_params

        返回:
            { "status": "成功", "output": { "carbon_emission": 5.345, "power_consumption": 0.092 } }
        """
        params = {
            "dew_point": dew_point,
            "rated_pressure": rated_pressure,
            "avg_flow_rate": avg_flow_rate,
        }
        if has_oil:
            params["has_oil"] = has_oil
        return self._get("/cn/api/air_compressor_params", params)

    # ══════════════════════════════════════════════════════════
    # 项目申请与阶段提交（已有 API）
    # ══════════════════════════════════════════════════════════

    def create_project(self, form_data: dict) -> dict:
        """
        创建 VCER 项目申请（阶段1）。

        POST /cn/api/vcer/create_project
        """
        return self._post("/cn/api/vcer/create_project", form_data)

    def submit_stage(self, query_key: str, query_password: str,
                     stage: int, form_data: dict, files: dict = None) -> dict:
        """
        提交阶段材料（阶段2-5，或阶段1驳回后重提）。

        POST /cn/api/vcer/submit_stage
        """
        data = {
            "query_key": query_key,
            "query_password": query_password,
            "stage": stage,
            **form_data,
        }
        return self._post("/cn/api/vcer/submit_stage", data, files)

    def get_project_status(self, query_key: str, query_password: str = None) -> dict:
        """
        查询项目完整状态。

        GET /cn/api/vcer/project_by_key?key=XXX&password=XXX
        """
        params = {"key": query_key}
        if query_password:
            params["password"] = query_password
        return self._get("/cn/api/vcer/project_by_key", params)

    def get_stage_rounds(self, query_key: str, stage: int) -> dict:
        """
        查询阶段多轮历史（阶段3/4/5），带进程内短 TTL 缓存。

        GET /cn/api/vcer/stage_rounds?key=XXX&stage=3
        """
        key = (query_key, stage)
        now = time.time()
        hit = _ROUNDS_CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
        result = self._get("/cn/api/vcer/stage_rounds", {"key": query_key, "stage": stage})
        if result.get("success") or result.get("status") == "成功":
            _ROUNDS_CACHE[key] = (now + _ROUNDS_CACHE_TTL, result)
        return result

    @staticmethod
    def invalidate_rounds_cache(query_key: str = None, stage: int = None):
        """失效轮次缓存。query_key 为 None 时清空全部（用于提交成功后的强一致）。"""
        if query_key is None:
            _ROUNDS_CACHE.clear()
            return
        for key in list(_ROUNDS_CACHE.keys()):
            if key[0] == query_key and (stage is None or key[1] == stage):
                del _ROUNDS_CACHE[key]

    def change_password(self, query_key: str, old_password: str, new_password: str) -> dict:
        """
        修改查询密码（知道原密码时）。

        POST /cn/api/vcer/change_password
        """
        return self._post("/cn/api/vcer/change_password", {
            "query_key": query_key,
            "old_password": old_password,
            "new_password": new_password,
        })

    def request_password_reset(self, query_key: str) -> dict:
        """
        申请管理员重置密码（忘记密码时）。

        POST /cn/api/vcer/request_password_change
        """
        return self._post("/cn/api/vcer/request_password_change", {
            "query_key": query_key,
        })

    def set_new_password(self, query_key: str, new_password: str) -> dict:
        """
        管理员批准后设置新密码。

        POST /cn/api/vcer/set_new_password
        """
        return self._post("/cn/api/vcer/set_new_password", {
            "query_key": query_key,
            "new_password": new_password,
        })

    def check_progress(self, query_key: str, company_name: str = None) -> dict:
        """
        进度查询。

        GET /cn/api/vcer/check_progress
        """
        params = {"id": query_key}
        if company_name:
            params["company"] = company_name
        return self._get("/cn/api/vcer/check_progress", params)

    # ══════════════════════════════════════════════════════════
    # 新接口（Phase 1 新增的 API）
    # ══════════════════════════════════════════════════════════

    def get_enterprise_summary(self, enterprise_id: str) -> dict:
        """
        企业碳账户汇总视图。

        GET /cn/api/account/enterprise_summary?enterprise_id=036

        返回:
            { "status": "成功", "enterprise_name": "...",
              "carbon_assets": {...}, "projects": [...] }
        """
        return self._get("/cn/api/account/enterprise_summary", {"enterprise_id": enterprise_id})

    def sync_project_data(self, query_key: str, query_password: str,
                          project_code: str, cumulative_reduction: float,
                          monitoring_summary: dict = None) -> dict:
        """
        将本地计算的减排数据同步到促进会。

        POST /cn/api/account/project_sync
        """
        import datetime
        return self._post("/cn/api/account/project_sync", {
            "query_key": query_key,
            "query_password": query_password,
            "project_code": project_code,
            "cumulative_reduction": cumulative_reduction,
            "monitoring_summary": monitoring_summary or {},
            "last_sync_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    def get_hook_params(self, hook_name: str, **kwargs) -> dict:
        """
        获取公式钩子所需的基准线参数。

        GET /cn/api/account/hook_params?hook_name=air_compressor&dew_point=0&...

        目前支持:
            hook_name=air_compressor: 调用 air_compressor_params 接口
        """
        params = {"hook_name": hook_name}
        params.update(kwargs)
        return self._get("/cn/api/account/hook_params", params)
