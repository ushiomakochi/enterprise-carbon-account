"""
通用碳账户管理系统 — 主应用入口
企业部署此 Flask 应用即可运行独立的碳账户管理系统。

启动方式:
    python app.py
    python app.py --port 8080
    python serve.py
    waitress-serve --call --port=5000 serve:create_app
"""

import sys
import os
import json
import logging
import argparse
from datetime import datetime

# ── 启动加速：字节码缓存重定向到本地盘 ──
# 项目若部署在 SMB 网络映射盘（如 E:\ 映射到局域网另一台机器），Python 每次启动
# 都要从网络盘读源码并校验/重编译 .pyc（flask/requests/docx 等依赖首编译可达
# 数十秒甚至几分钟）。把 pycache 指到本地盘后，第二次起就秒级；对本地盘部署
# （如生产 Ubuntu /opt/apps）无副作用。
_LOCAL_PYCACHE = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    ".carbon_account_pycache",
)
try:
    sys.pycache_prefix = _LOCAL_PYCACHE
except Exception:
    pass

from flask import Flask, request, jsonify, render_template, send_from_directory, send_file

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from core import project_manager
from core.carbon_calculator import CarbonCalculator
from core.vcer_client import VCERClient
from core.report_generator import ReportGenerator
from core.data_collector import DataCollector

app = Flask(__name__)
app.secret_key = config.SECRET_KEY


@app.before_request
def _reject_void_project_writes():
    """Single-project API writes are blocked even if a client bypasses the UI."""
    import re
    match = re.match(r"^/api/projects/(\d+)(?:/|$)", request.path)
    if not match:
        return None
    project_id = int(match.group(1))
    if request.path.endswith("/lifecycle/restore") or request.path.endswith("/lifecycle/archive/retry"):
        return None
    if request.path.endswith("/lifecycle/void"):
        return None
    if request.method == "GET" and not request.path.endswith("/vcer/status"):
        return None
    from core.project_lifecycle import is_active
    if not is_active(project_id):
        return jsonify({"status": "失败", "msg": "项目已作废，历史资料仅供查看"}), 409
    return None


# ═══════════════════════════════════════════════════════════════
# 应用初始化
# ═══════════════════════════════════════════════════════════════

def init_app():
    """应用启动初始化"""
    import time
    t_start = time.time()
    print("=" * 60)
    print("  通用碳账户管理系统 v1.0")
    print("=" * 60)

    # 0. 日志：先挂上落库 handler，后面各步骤的日志才会进 system_logs。
    #    放在最前面，建表由第 1.5 步的迁移负责；在此之前产生的日志只进控制台。
    log = None
    try:
        from core.app_log import setup_logging
        setup_logging()
        log = logging.getLogger("startup")
    except Exception as e:
        print(f"[WARN] 日志系统初始化失败（仅影响日志页签）: {e}")

    # 1. 初始化数据库
    t = time.time()
    print("[1/5] 初始化数据库 ...", flush=True)
    project_manager.init_database()
    print(f"      完成（{time.time() - t:.1f}s）", flush=True)

    # 1.5. 迁移：确保 enterprise_config 有 presets 列
    t = time.time()
    print("[2/5] 数据库结构迁移 ...", flush=True)
    try:
        from core.presets import migrate_db, discover_preset_packs
        migrate_db()
        packs = discover_preset_packs()
        if packs and log:
            log.info("已加载 %s 个企业预设外载包: %s", len(packs),
                     "、".join(p["pack_name"] for p in packs))
    except Exception:
        pass

    # 1.6. 补齐报告模板依赖的通用项目预设（审定机构等）
    try:
        from core.project_presets import ensure_required_project_presets
        ensure_required_project_presets()
    except Exception:
        pass
    print(f"      完成（{time.time() - t:.1f}s）", flush=True)

    # 2. 发现并注册计算公式
    t = time.time()
    print("[3/5] 加载计算公式 ...", flush=True)
    CarbonCalculator.discover_formulas(config.HOOKS_DIR)
    formulas = CarbonCalculator.get_available_formulas()
    print(f"[OK] 已加载 {len(formulas)} 个计算公式（{time.time() - t:.1f}s）:", flush=True)
    for f in formulas:
        print(f"     - {f['name']}: {f['label']} v{f['version']}")

    # 3. 初始化 VCER API 客户端
    t = time.time()
    print("[4/5] 初始化 VCER API 客户端 ...", flush=True)
    vcer = VCERClient(config.VCER_API_URL)
    print(f"[OK] VCER API 端点: {config.VCER_API_URL}（{time.time() - t:.1f}s）", flush=True)

    # 4. 启动数据采集器（如果有活动项目）
    t = time.time()
    print("[5/5] 启动数据采集器 ...", flush=True)
    DataCollector.start()
    from core.continuity_probe import start_scheduler
    start_scheduler()
    print(f"      完成（{time.time() - t:.1f}s）", flush=True)

    # 6. 后台预热 docx / docxtpl
    # 项目在网络盘上时，首次 import docx 要几十秒（从网络盘读源码 + 编译）。
    # 而它们是惰性导入的，第一个碰到模板的请求（通常是首屏的模板字段识别）得一个人扛完。
    # 放到后台线程里预热，启动流程不用等，用户点进去时通常已经好了。
    def _warm_report_imports():
        try:
            import docx  # noqa: F401
            import docxtpl  # noqa: F401
            (log.info if log else print)("[预热] docx / docxtpl 已加载完成")
        except Exception as e:
            print(f"[预热] docx / docxtpl 预加载失败（不影响功能）: {e}", flush=True)

    import threading
    threading.Thread(target=_warm_report_imports, daemon=True, name="warm-docx").start()

    print(f"[OK] 服务地址: http://{config.HOST}:{config.PORT}（启动共 {time.time() - t_start:.1f}s）")
    print("=" * 60, flush=True)
    if log:
        log.info("系统启动完成（%.1fs）：%s 个公式，服务地址 http://%s:%s",
                 time.time() - t_start, len(formulas), config.HOST, config.PORT)


# ═══════════════════════════════════════════════════════════════
# 前端页面路由
# ═══════════════════════════════════════════════════════════════

@app.route('/')
def index():
    """碳账户管理主页（SPA）"""
    return send_from_directory(
        os.path.join(config.BASE_DIR, 'static'), 'index.html'
    )


@app.route('/static/<path:filename>')
def serve_static(filename):
    """静态文件服务"""
    return send_from_directory(
        os.path.join(config.BASE_DIR, 'static'), filename
    )


# ═══════════════════════════════════════════════════════════════
# 企业配置 API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/config', methods=['GET'])
def api_get_config():
    """获取企业配置"""
    cfg = project_manager.get_enterprise_config()
    if cfg:
        cfg = dict(cfg)
        # 密钥是敏感信息，不回显本体，只回一个打码串给设置页显示"已配置"。
        # ⚠️ 打码的是**清洗后真正会用到的那个值**（不是库里存的原始串）——
        #    库里可能存着"粘多了"的文本，回显原串的话末 4 位跟实际发出去的对不上，
        #    反而误导。
        _raw = cfg.pop("api_access_key", None) or ""
        _k = VCERClient.clean_access_key(_raw)
        cfg["api_access_key_set"] = bool(_k)
        cfg["api_access_key_masked"] = (("•" * 8) + _k[-4:]) if _k else ""
        # 存的值需要清洗才能用（或者根本用不了）→ 提醒前端提示用户重贴
        cfg["api_access_key_dirty"] = bool(_raw) and (_raw.strip() != _k)
        # 历史 API 平台密码同样打码，不回显本体
        _hpwd = cfg.pop("history_api_password", None) or ""
        cfg["history_api_password_set"] = bool(_hpwd)
        cfg["history_api_password_masked"] = ("•" * 8) if _hpwd else ""
        # 合并企业预设外载包的企业级配置（只读覆盖，随包文件走）
        try:
            from core.presets import get_pack_enterprise_config
            pack_cfg = get_pack_enterprise_config()
            if pack_cfg:
                cfg.update(pack_cfg)
                cfg["_pack_config"] = sorted(pack_cfg.keys())
        except Exception:
            pass
    return jsonify(cfg or {})


@app.route('/api/config', methods=['POST'])
def api_update_config():
    """更新企业配置"""
    data = request.get_json(force=True, silent=True) or {}

    # 密钥是个密码框（回显的是打码串），所以约定：
    #   · 留空        → **不修改**（不能因为没填就把已存的密钥清掉）
    #   · "__clear__" → 真的清空
    #   · 其它值      → 清洗后覆盖
    if "api_access_key" in data:
        v = (data.get("api_access_key") or "").strip()
        if v == "__clear__":
            data["api_access_key"] = ""
        elif not v:
            data.pop("api_access_key")
        else:
            # 粘贴时很容易连说明文字/换行一起带进来。那种值存下去，
            # 请求头会抛 `'latin-1' codec can't encode characters ...`，
            # 报错完全看不出是密钥的问题 —— 所以这里先还原成裸密钥，
            # 还原不出来就当场拒掉，别存进去。
            cleaned = VCERClient.clean_access_key(v)
            if not cleaned:
                return jsonify({
                    "status": "失败",
                    "msg": "这串不像密钥。密钥是字母/数字/-/_ 组成的一长串"
                           "（约 43 位），请只复制 make_carbon_api_key.py "
                           "打印出来的密钥本体，不要带下面的说明文字。",
                })
            data["api_access_key"] = cleaned

    # 历史 API 平台密码：留空 = 不修改；"__clear__" = 清空；其它 = 覆盖
    if "history_api_password" in data:
        v = (data.get("history_api_password") or "").strip()
        if v == "__clear__":
            data["history_api_password"] = ""
        elif not v:
            data.pop("history_api_password")

    resp = project_manager.update_enterprise_config(data)

    # 保存成功就让客户端丢掉缓存的密钥，下一次 API 调用立刻用新的
    if resp.get("status") == "成功" and "api_access_key" in data:
        VCERClient.invalidate_key_cache()
    return jsonify(resp)


# ═══════════════════════════════════════════════════════════════
# 项目管理 API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/projects', methods=['GET'])
def api_list_projects():
    """列出所有项目"""
    projects = project_manager.list_projects()
    return jsonify({"status": "成功", "projects": projects})


@app.route('/api/projects/<int:project_id>', methods=['GET'])
def api_get_project(project_id):
    """获取单个项目详情"""
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})
    return jsonify({"status": "成功", "project": proj})


@app.route('/api/projects', methods=['POST'])
def api_create_project():
    """创建新项目"""
    data = request.get_json(force=True, silent=True) or {}
    return jsonify(project_manager.create_project(data))


@app.route('/api/projects/<int:project_id>', methods=['PUT'])
def api_update_project(project_id):
    """更新项目"""
    data = request.get_json(force=True, silent=True) or {}
    return jsonify(project_manager.update_project(project_id, data))


@app.route('/api/projects/<int:project_id>', methods=['DELETE'])
def api_delete_project(project_id):
    """删除项目"""
    return jsonify(project_manager.delete_project(project_id))


@app.route('/api/projects/<int:project_id>/lifecycle/void', methods=['POST'])
def api_void_project(project_id):
    from core.project_lifecycle import void_project
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(void_project(project_id, data.get("reason"), data.get("actor") or "本机经办人"))
    except ValueError as exc:
        return jsonify({"status": "失败", "msg": str(exc)}), 400


@app.route('/api/projects/<int:project_id>/lifecycle/restore', methods=['POST'])
def api_restore_project(project_id):
    from core.project_lifecycle import restore_project
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(restore_project(project_id, data.get("reason"), data.get("actor") or "本机经办人"))
    except ValueError as exc:
        return jsonify({"status": "失败", "msg": str(exc)}), 400


@app.route('/api/projects/<int:project_id>/lifecycle/archive/retry', methods=['POST'])
def api_retry_project_archive(project_id):
    from core.project_lifecycle import retry_archive, mark_archive_failure
    try:
        return jsonify(retry_archive(project_id))
    except ValueError as exc:
        return jsonify({"status": "失败", "msg": str(exc)}), 400
    except Exception as exc:
        return jsonify(mark_archive_failure(project_id, str(exc)))


@app.route('/api/projects/<int:project_id>/lifecycle/archive', methods=['GET'])
def api_project_archive(project_id):
    from core.project_lifecycle import archive_detail
    return jsonify({"status": "成功", **archive_detail(project_id)})


@app.route('/api/projects/<int:project_id>/lifecycle/archive/file/<name>', methods=['GET'])
def api_project_archive_file(project_id, name):
    from core.project_lifecycle import _archive_root
    from pathlib import Path
    if not name or "/" in name or "\\" in name:
        return jsonify({"status": "失败", "msg": "文件名无效"}), 400
    root = _archive_root(project_id)
    path = root / "attachments" / name
    if not path.is_file():
        path = root / "reports" / name
    if not path.is_file():
        return jsonify({"status": "失败", "msg": "归档文件不存在"}), 404
    return send_file(path, as_attachment=True, download_name=name)


@app.route('/api/projects/<int:project_id>/verification/sync', methods=['POST'])
def api_sync_verification(project_id):
    from core.reduction_ledger import sync_verification
    return jsonify(sync_verification(project_id))


# ═══════════════════════════════════════════════════════════════
# 机器（多设备）API
# ═══════════════════════════════════════════════════════════════
#
# 一个项目可以有多台机器，每台自带**数据源配置**和**公式参数**。
# 公式（hook_name）仍然是项目级的 —— PDD 的方法学一般只有一个。
# 报告读参数时以「主机器」（排序第一台）为代表，各机器逐台的值
# 由表 B.2/B.3/B.4 按列呈现。

@app.route('/api/projects/<int:project_id>/machines', methods=['GET'])
def api_list_machines(project_id):
    """列出项目的机器（含每台的监测数据条数，删除前提示用）"""
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})
    return jsonify({"status": "成功",
                    "machines": project_manager.list_machines(project_id)})


@app.route('/api/projects/<int:project_id>/machines', methods=['POST'])
def api_create_machine(project_id):
    """给项目加一台机器。未给的字段沿用项目级 legacy 配置作默认值。"""
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})
    data = request.get_json(force=True, silent=True) or {}
    return jsonify(project_manager.create_machine(project_id, data))


@app.route('/api/projects/<int:project_id>/machines/<int:machine_id>', methods=['PUT'])
def api_update_machine(project_id, machine_id):
    """更新一台机器（名称/排序/数据源/公式参数）"""
    mach = project_manager.get_machine(machine_id)
    if not mach or mach["project_id"] != project_id:
        return jsonify({"status": "失败", "msg": "机器不存在或不属于该项目"})
    data = request.get_json(force=True, silent=True) or {}
    result = project_manager.update_machine(machine_id, data)
    # 参数/数据源变了，机器上缓存的公式实例得作废重建
    if result.get("status") == "成功" and (
            "hook_params" in data or "data_source_config" in data):
        CarbonCalculator.remove_instance(machine_id)
    # 数据源配置变了，采集器里缓存的适配器也要作废 —— 否则旧 MQTT 连接
    # 还按老配置订阅，前端改了 broker/topic/前置转换不重启不生效
    if result.get("status") == "成功" and ("data_source_config" in data or "normalizer_config" in data):
        DataCollector.drop_adapter(machine_id)
    return jsonify(result)


@app.route('/api/projects/<int:project_id>/machines/<int:machine_id>/test-request', methods=['POST'])
def api_test_machine_request(project_id, machine_id):
    """用当前表单配置发一次 API 请求，返回原始响应，不保存机器或监测数据。"""
    mach = project_manager.get_machine(machine_id)
    if not mach or mach["project_id"] != project_id:
        return jsonify({"status": "失败", "msg": "机器不存在或不属于该项目"}), 404
    data = request.get_json(silent=True) or {}
    source = data.get("data_source_config")
    normalizer = data.get("normalizer_config")
    if not isinstance(source, dict) or not isinstance(normalizer, dict):
        return jsonify({"status": "失败", "msg": "数据源和前置转换配置必须是 JSON 对象"}), 400
    try:
        begin = datetime.fromisoformat(str(data.get("begin") or ""))
        end = datetime.fromisoformat(str(data.get("end") or ""))
    except ValueError:
        return jsonify({"status": "失败", "msg": "请填写有效的测试开始和结束时间"}), 400
    try:
        from adapters.api_adapter import ApiAdapter
        config = dict(source)
        config["normalizer"] = normalizer
        result = ApiAdapter(config).test_request(begin, end)
        return jsonify({"status": "成功", **result})
    except Exception as exc:
        return jsonify({"status": "失败", "msg": str(exc)})


@app.route('/api/projects/<int:project_id>/machines/<int:machine_id>/void', methods=['POST'])
def api_void_machine(project_id, machine_id):
    from core.project_lifecycle import change_machine_status
    data = request.get_json(silent=True) or {}
    try:
        result = change_machine_status(project_id, machine_id, "void",
                                       data.get("reason"), data.get("actor") or "本机经办人")
        DataCollector.drop_adapter(machine_id)
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"status": "失败", "msg": str(exc)}), 400


@app.route('/api/projects/<int:project_id>/machines/<int:machine_id>/restore', methods=['POST'])
def api_restore_machine(project_id, machine_id):
    from core.project_lifecycle import change_machine_status
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(change_machine_status(project_id, machine_id, "restore",
                    data.get("reason"), data.get("actor") or "本机经办人"))
    except ValueError as exc:
        return jsonify({"status": "失败", "msg": str(exc)}), 400


@app.route('/api/projects/<int:project_id>/machines/<int:machine_id>/lifecycle', methods=['GET'])
def api_machine_lifecycle(project_id, machine_id):
    from core.project_lifecycle import machine_events
    machine = project_manager.get_machine(machine_id)
    if not machine or machine["project_id"] != project_id:
        return jsonify({"status": "失败", "msg": "机器不存在"}), 404
    return jsonify({"status": "成功", "events": machine_events(machine_id)})


@app.route('/api/projects/<int:project_id>/machines/<int:machine_id>', methods=['DELETE'])
def api_delete_machine(project_id, machine_id):
    """删除一台机器（连同它名下的监测数据）。

    前端应先拿 machines 里的 data_count 提示「会一并删除 N 条监测数据」。
    项目最后一台机器不允许删。
    """
    mach = project_manager.get_machine(machine_id)
    if not mach or mach["project_id"] != project_id:
        return jsonify({"status": "失败", "msg": "机器不存在或不属于该项目"})
    result = project_manager.delete_machine(machine_id)
    if result.get("status") == "成功":
        CarbonCalculator.remove_instance(machine_id)
    return jsonify(result)


def _resolve_machine_id(project_id, machine_id=None):
    """把「可选的 machine_id」解析成一个确定存在的机器 id。

    没传就落到项目主机器 —— 老接口（不带 machine_id）因此保持可用。
    """
    if machine_id:
        m = project_manager.get_machine(int(machine_id))
        if not m or m["project_id"] != project_id:
            raise ValueError(f"机器 {machine_id} 不存在或不属于项目 #{project_id}")
        return m["id"]
    m = project_manager.ensure_project_machine(project_id)
    if not m:
        raise ValueError(f"项目 #{project_id} 没有可用机器")
    return m["id"]


# ═══════════════════════════════════════════════════════════════
# 计算公式 API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/formulas', methods=['GET'])
def api_list_formulas():
    """列出所有可用的计算公式"""
    formulas = CarbonCalculator.get_available_formulas()
    return jsonify({"status": "成功", "formulas": formulas})


@app.route('/api/formulas/<formula_name>/params', methods=['GET'])
def api_formula_params(formula_name):
    """获取公式需要的参数定义"""
    formula_cls = CarbonCalculator._formulas_registry.get(formula_name)
    if not formula_cls:
        return jsonify({"status": "失败", "msg": f"未找到公式: {formula_name}"})
    return jsonify({
        "status": "成功",
        "formula_name": formula_name,
        "required_params": formula_cls.required_formula_params,
        "required_raw_fields": formula_cls.required_raw_fields,
    })


@app.route('/api/normalizers', methods=['GET'])
def api_list_normalizers():
    """列出所有已注册的数据前置转换器（normalizers/ 目录自动发现）。

    供前端机器编辑表单展示「前置数据处理」下拉选项。
    """
    from adapters.normalizer import get_available_normalizers
    return jsonify({"status": "成功", "normalizers": get_available_normalizers()})


def _site_mapping_warnings(platform_projects, hook_name):
    """Read-only hints; platform absence never changes project lifecycle."""
    available = {str(site.get("dataId")) for site in platform_projects
                 if isinstance(site, dict) and site.get("dataId") is not None}
    warnings = []
    for project in project_manager.list_projects():
        if project.get("lifecycle_status") != "active":
            continue
        for machine in project_manager.list_machines(project["id"]):
            if machine.get("lifecycle_status") != "active":
                continue
            if machine.get("data_source_type") != "api" and machine.get("backfill_method") != "api":
                continue
            try:
                norm = json.loads(machine.get("normalizer_config") or "{}")
                source = json.loads(machine.get("data_source_config") or "{}")
                history = json.loads(machine.get("backfill_config") or "{}")
            except (TypeError, ValueError):
                continue
            if norm.get("hook") != hook_name:
                continue
            site_id = str(source.get("site_id") or history.get("site_id") or "").strip()
            reason = ("未配置站点 ID" if not site_id else
                      ("站点 ID " + site_id + " 不在当前平台列表中" if site_id not in available else ""))
            if reason:
                warnings.append({"project_id": project["id"],
                                 "project_name": project["project_name"],
                                 "machine_id": machine["id"], "machine_name": machine["name"],
                                 "reason": reason})
    return warnings


@app.route('/api/normalizer/list-projects', methods=['POST'])
def api_normalizer_list_projects():
    """调用指定前置转换文件的 list_projects，返回平台可见的项目列表（dataId 选择用）。

    body: {"hook": "xinzuan", "refresh": false}
    项目列表（含省市分类）会缓存到本地 data/site_projects_<hook>.json，
    之后读缓存不再调平台接口；refresh=true 强制重新拉取。
    """
    from adapters.normalizer import _normalizer_registry, _discover
    from core.project_manager import get_enterprise_config

    data = request.get_json(force=True, silent=True) or {}
    hook_name = (data.get("hook") or "").strip()
    refresh = bool(data.get("refresh"))
    if not hook_name:
        return jsonify({"status": "失败", "msg": "缺少 hook 参数"})
    _discover()
    hook_cls = _normalizer_registry.get(hook_name)
    if not hook_cls:
        return jsonify({"status": "失败", "msg": f"未找到转换器: {hook_name}"})

    cache_file = config.data_path(f"site_projects_{hook_name}.json")

    # 读缓存（未要求刷新且缓存有项目时直接返回，不调平台接口）
    if not refresh and os.path.exists(cache_file):
        try:
            with open(cache_file, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("projects"):
                return jsonify({"status": "成功", "projects": cached["projects"],
                                "mapping_warnings": _site_mapping_warnings(cached["projects"], hook_name),
                                "cached": True, "fetched_at": cached.get("fetched_at")})
        except Exception:
            pass

    cfg = get_enterprise_config() or {}
    base_url = cfg.get("history_api_base") or ""
    username = cfg.get("history_api_username") or ""
    password = cfg.get("history_api_password") or ""
    if not base_url:
        return jsonify({"status": "失败", "msg": "未配置历史数据 API 地址（设置 → 企业配置）"})

    try:
        hook = hook_cls()
        projects = hook.list_projects(base_url, username, password)
    except Exception as e:
        return jsonify({"status": "失败", "msg": f"获取项目列表失败: {e}"})

    # 写缓存
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"hook": hook_name,
                       "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "projects": projects}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return jsonify({"status": "成功", "projects": projects, "cached": False,
                    "mapping_warnings": _site_mapping_warnings(projects, hook_name)})


# ═══════════════════════════════════════════════════════════════
# 历史数据批量回溯 API
# ═══════════════════════════════════════════════════════════════

def _start_backfill(project_id=None):
    from core import backfill
    data = request.get_json(force=True, silent=True) or {}
    baseline_date = (data.get("baseline_date") or "").strip() or None
    granularity = data.get("granularity") or None
    if granularity not in (None, "normal", "hour", "day", "month"):
        return jsonify({"status": "失败", "msg": f"粒度只能是 normal/hour/day/month: {granularity}"})
    job_id = backfill.start_backfill(project_id=project_id,
                                     baseline_date=baseline_date,
                                     granularity=granularity)
    return jsonify({"status": "成功", "job_id": job_id, "msg": "回溯任务已启动"})


@app.route('/api/projects/<int:project_id>/backfill', methods=['POST'])
def api_backfill_project(project_id):
    """回溯单个项目的 API 机器"""
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})
    return _start_backfill(project_id=project_id)


@app.route('/api/backfill', methods=['POST'])
def api_backfill_all():
    """批量回溯所有项目的 API 机器"""
    return _start_backfill(project_id=None)


@app.route('/api/backfill/status/<job_id>', methods=['GET'])
def api_backfill_status(job_id):
    from core import backfill
    job = backfill.get_job(job_id)
    if not job:
        return jsonify({"status": "失败", "msg": "任务不存在或已过期"})
    return jsonify({"status": "成功", "job": job})


@app.route('/api/projects/<int:project_id>/backfill/jobs', methods=['GET'])
def api_backfill_jobs(project_id):
    """列出某个项目的回溯任务历史（含运行中），供「回溯历史」弹窗查看。"""
    from core import backfill
    return jsonify({"status": "成功", "jobs": backfill.list_jobs(project_id=project_id)})


@app.route('/api/backfill/jobs', methods=['GET'])
def api_backfill_jobs_all():
    """列出全部回溯任务历史（含运行中）。"""
    from core import backfill
    return jsonify({"status": "成功", "jobs": backfill.list_jobs(project_id=None)})


# ═══════════════════════════════════════════════════════════════
# 碳计算 API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/projects/<int:project_id>/compute', methods=['POST'])
def api_compute_reduction(project_id):
    """
    手动触发一次碳减排计算。
    请求体: { "raw_data": {...}, "cumulative_data": {...} }
    """
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    data = request.get_json(force=True, silent=True) or {}
    raw_data = data.get("raw_data", {})
    cumulative_data = data.get("cumulative_data", {})

    try:
        # 不传 machine_id 就走项目主机器（老调用方无感）
        machine_id = _resolve_machine_id(project_id, data.get("machine_id"))
        mach = project_manager.get_machine(machine_id)

        # 确保该机器的公式已初始化（公式名取自项目，参数取自机器）
        if machine_id not in CarbonCalculator._instances:
            CarbonCalculator.init_machine_formula(mach, proj.get("hook_name", ""),
                                                  config.VCER_API_URL)

        result = CarbonCalculator.compute(machine_id, raw_data, cumulative_data)
        return jsonify({"status": "成功", "result": result, "machine_id": machine_id})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/projects/<int:project_id>/compute/estimate', methods=['GET'])
def api_estimate_annual(project_id):
    """预估年度减排量"""
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    try:
        # 不传 machine_id 就走项目主机器（老调用方无感）
        machine_id = _resolve_machine_id(project_id, request.args.get("machine_id"))
        mach = project_manager.get_machine(machine_id)
        if machine_id not in CarbonCalculator._instances:
            CarbonCalculator.init_machine_formula(mach, proj.get("hook_name", ""),
                                                  config.VCER_API_URL)

        # 获取该机器最近30天的监测数据（年化按单台算，不做多机器混合）
        from core.project_manager import get_db
        conn = get_db()
        rows = conn.execute(
            """SELECT carbon_reduction, timestamp FROM monitoring_data
               WHERE machine_id = ? AND timestamp >= date('now', '-30 days')
               ORDER BY timestamp""",
            (machine_id,)
        ).fetchall()
        conn.close()

        period_data = [{"carbon_reduction": r["carbon_reduction"]} for r in rows if r["carbon_reduction"]]
        estimate = CarbonCalculator.estimate_annual(machine_id, period_data)
        return jsonify({"status": "成功", "estimated_annual_tco2e": estimate,
                        "machine_id": machine_id})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/projects/<int:project_id>/projection', methods=['GET'])
def api_get_projection(project_id):
    """获取项目十年逐年预估（基准线排放 / 项目排放 / 减排量）。"""
    from core import projection
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})
    rows = projection.get_projection(proj)
    return jsonify({
        "status": "成功",
        "crediting_start": proj.get("baseline_date") or "",
        "years": len(rows) or 10,
        "rows": rows,
        "is_stored": bool(proj.get("projection_10yr")),
    })


@app.route('/api/projects/<int:project_id>/projection', methods=['POST'])
def api_save_projection(project_id):
    """保存手动改的十年预估，或触发重算并保存。请求体 {rows:[...]} 或 {recompute:true}。"""
    from core import projection
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    data = request.get_json(force=True, silent=True) or {}
    if data.get("recompute"):
        rows = projection.compute_projection(proj)
        if not rows:
            return jsonify({"status": "失败",
                            "msg": "无法计算：请确认项目已配置公式参数（avg_flow_rate 等）且基准线因子已获取"})
        result = projection.save_projection(project_id, rows)
        if result.get("status") != "成功":
            return jsonify(result)
        return jsonify({"status": "成功", "rows": rows, "is_stored": True})

    rows = data.get("rows")
    if not isinstance(rows, list):
        return jsonify({"status": "失败", "msg": "缺少 rows 数组"})
    result = projection.save_projection(project_id, rows)
    if result.get("status") != "成功":
        return jsonify(result)
    return jsonify({"status": "成功", "rows": rows, "is_stored": True})


@app.route('/api/projects/<int:project_id>/collect', methods=['POST'])
def api_manual_collect(project_id):
    """
    手动触发一次数据采集+计算（用于测试 REST API / MQTT 数据源）。

    请求体可选 {"machine_id": N}：给了就只采那一台；不给则采该项目
    **所有非手动**的机器。返回每台的结果。
    """
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    data = request.get_json(force=True, silent=True) or {}
    try:
        if data.get("machine_id"):
            machines = [project_manager.get_machine(int(data["machine_id"]))]
            if not machines[0] or machines[0]["project_id"] != project_id:
                return jsonify({"status": "失败", "msg": "机器不存在或不属于该项目"})
        else:
            machines = [m for m in project_manager.list_machines(project_id)
                        if (m.get("data_source_type") or "manual") != "manual"]
            if not machines:
                return jsonify({"status": "失败",
                                "msg": "该项目没有配置自动采集的机器（全部为手动录入）"})

        results = []
        for m in machines:
            r = DataCollector.collect_one(m["id"])
            results.append({
                "machine_id": m["id"], "machine_name": m.get("name") or "",
                "status": r.get("status"), "msg": r.get("msg"),
                "result": r.get("result"),
            })

        ok = sum(1 for r in results if r["status"] == "成功")
        resp = {
            "status": "成功" if ok else "失败",
            "msg": (results[0].get("msg") if len(results) == 1
                    else f"采集完成：成功 {ok} 台 / 共 {len(results)} 台"),
            "collected": ok,
            "failed": len(results) - ok,
            "results": results,
        }
        # 只采了一台时补上老字段，兼容既有调用方
        if len(results) == 1:
            resp["result"] = results[0].get("result")
        return jsonify(resp)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


# ═══════════════════════════════════════════════════════════════
# 断档兜底 API（检测 → 人工确认 → 执行）
#
# 自动兜底默认已关（config.COLLECT_GAP_AUTO_BRIDGE=0）：采集时只记新起点 + 告警，
# 由人在这里看过计算结果再确认。算法全在 core/gap_bridge.py，与采集器/命令行同一份。
# ═══════════════════════════════════════════════════════════════

def _gap_formula_and_fields(project, machine):
    """取该机器的公式实例 + 公式要求的原始字段名（与采集路径同一套）。"""
    from core.data_collector import _collector_formula, _raw_fields
    import config as _cfg
    return _collector_formula(machine, project, _cfg.VCER_API_URL), _raw_fields(project)


def _find_gap_by_id(conn, gap_row_id):
    """按 monitoring_data.id 定位一条断档。

    返回 (project, machine, gap, err) —— **四个**，err 非空表示没找到，
    调用方一律先判 err。别把 err 塞进第三位：那样成功时第三位是 dict、
    失败时是字符串，`if not gap` 判不出来（错误信息是非空字符串，恒为真）。
    """
    from core.gap_bridge import find_gaps
    from core.project_manager import get_project, get_machine
    row = conn.execute(
        "SELECT project_id, machine_id FROM monitoring_data WHERE id = ?", (gap_row_id,)
    ).fetchone()
    if not row:
        return None, None, None, f"找不到 id={gap_row_id} 的监测数据行"
    project = get_project(row["project_id"])
    machine = get_machine(row["machine_id"])
    if not project or not machine:
        return None, None, None, "该监测数据行对应的项目或机器已不存在"
    for g in find_gaps(conn, row["machine_id"]):
        if g["gap_row"]["id"] == gap_row_id:
            return project, machine, g, None
    return None, None, None, "这一行不是断档行（没有断档标记）"


@app.route('/api/gap_bridge/pending', methods=['GET'])
def api_gap_bridge_pending():
    """所有项目里「待兜底」的断档（不含已兜底过的），供项目卡片显示角标。"""
    from core.gap_bridge import find_gaps
    from core.project_manager import get_db
    conn = get_db()
    try:
        machines = conn.execute(
            "SELECT m.id, m.name, m.project_id, p.project_name, p.project_code "
            "FROM project_machines m JOIN carbon_projects p ON p.id = m.project_id "
            "ORDER BY p.id, m.sort_order, m.id"
        ).fetchall()
        items = []
        for m in machines:
            for g in find_gaps(conn, m["id"]):
                if g["gap_row"]["id"] is None:
                    continue
                items.append({
                    "gap_row_id": g["gap_row"]["id"],
                    "project_id": m["project_id"],
                    "project_name": m["project_name"],
                    "project_code": m["project_code"],
                    "machine_id": m["id"],
                    "machine_name": m["name"],
                    "gap_timestamp": g["gap_row"]["timestamp"],
                    "prev_timestamp": g["prev_row"]["timestamp"] if g["prev_row"] else None,
                    "span_hours": round(g["span_hours"], 1) if g["span_hours"] is not None else None,
                    "bridged": bool(g["bridged"]),
                })
        # 按项目聚合成角标用的计数
        counts = {}
        for it in items:
            if not it["bridged"]:
                counts[it["project_id"]] = counts.get(it["project_id"], 0) + 1
        return jsonify({"status": "成功", "data": items, "counts": counts})
    finally:
        conn.close()


@app.route('/api/gap_bridge/preview', methods=['GET'])
def api_gap_bridge_preview():
    """干跑一次兜底：算出会改成什么，但**不写库**。参数 gap_row_id。"""
    from core.gap_bridge import preview_bridge
    from core.project_manager import get_db

    gap_row_id = request.args.get("gap_row_id", type=int)
    verified_manual = request.args.get("verified", type=float)
    if not gap_row_id:
        return jsonify({"status": "失败", "msg": "缺少 gap_row_id"})
    conn = get_db()
    try:
        project, machine, gap, err = _find_gap_by_id(conn, gap_row_id)
        if err:
            return jsonify({"status": "失败", "msg": err})
        formula, raw_fields = _gap_formula_and_fields(project, machine)
        return jsonify({"status": "成功", "data": preview_bridge(
            project, machine, gap, formula, raw_fields, verified_manual=verified_manual)})
    except Exception as e:
        return jsonify({"status": "失败", "msg": f"{type(e).__name__}: {e}"})
    finally:
        conn.close()


@app.route('/api/gap_bridge/apply', methods=['POST'])
def api_gap_bridge_apply():
    """确认执行一次兜底。body: {gap_row_id, verified?}

    会**重新算一遍**再写（不复用预览结果，避免"预览看到 A、执行写进去 B"），
    写库前自动备份整库。
    """
    from core.gap_bridge import apply_bridge
    from core.project_manager import get_db

    body = request.get_json(silent=True) or {}
    gap_row_id = body.get("gap_row_id")
    verified_manual = body.get("verified")
    if verified_manual in ("", None):
        verified_manual = None
    else:
        try:
            verified_manual = float(verified_manual)
        except (TypeError, ValueError):
            return jsonify({"status": "失败", "msg": "手工指定的已核查量不是数字"})
    if not gap_row_id:
        return jsonify({"status": "失败", "msg": "缺少 gap_row_id"})

    conn = get_db()
    try:
        project, machine, gap, err = _find_gap_by_id(conn, int(gap_row_id))
        if err:
            return jsonify({"status": "失败", "msg": err})
        formula, raw_fields = _gap_formula_and_fields(project, machine)
        res = apply_bridge(conn, project, machine, gap, formula, raw_fields,
                           verified_manual=verified_manual)
        if not res.get("ok"):
            return jsonify({"status": "失败", "msg": res.get("error") or "这段空档不需要补记",
                            "data": res})
        return jsonify({"status": "成功", "data": res,
                        "msg": f"已补记 {res['written']['carbon_reduction']:.6f} tCO₂e"})
    except Exception as e:
        return jsonify({"status": "失败", "msg": f"{type(e).__name__}: {e}"})
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# 监测数据 API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/projects/<int:project_id>/monitoring', methods=['GET'])
def api_get_monitoring(project_id):
    """
    获取项目监测数据。支持参数:
      hours: 最近多少小时（默认24）
      limit: 最多返回条数（默认100）
    """
    hours = request.args.get("hours", 24, type=int)
    limit = request.args.get("limit", 100, type=int)
    # 可选按机器过滤；不传 = 项目全部机器
    machine_id = request.args.get("machine_id", type=int)

    from core.project_manager import get_db
    conn = get_db()
    try:
        where = "project_id = ? AND timestamp >= datetime('now', ? || ' hours')"
        params = [project_id, f"-{hours}"]
        if machine_id:
            where += " AND machine_id = ?"
            params.append(machine_id)
        params.append(limit)
        rows = conn.execute(
            f"""SELECT * FROM monitoring_data
                WHERE {where}
                ORDER BY timestamp DESC LIMIT ?""",
            params
        ).fetchall()
        return jsonify({
            "status": "成功",
            "data": [dict(r) for r in reversed(rows)],
            "count": len(rows),
        })
    finally:
        conn.close()


@app.route('/api/projects/<int:project_id>/monitoring/all', methods=['GET'])
def api_get_monitoring_all(project_id):
    """分页返回项目的全部监测数据（含机器名），供「数据查看」模块用。"""
    page = request.args.get("page", 1, type=int)
    page_size = request.args.get("page_size", 50, type=int)
    machine_id = request.args.get("machine_id", type=int)
    anomalies_only = request.args.get("anomalies_only") == "1"
    segment_id = request.args.get("segment_id",type=int)
    page = max(1, page)
    page_size = min(500, max(1, page_size))
    from core.project_manager import get_db
    conn = get_db()
    try:
        where = "d.project_id = ?"
        params = [project_id]
        if machine_id:
            where += " AND d.machine_id = ?"
            params.append(machine_id)
        if segment_id:
            where += " AND json_valid(d.breakdown) AND json_extract(d.breakdown,'$.segment_id')=?"
            params.append(segment_id)
        if anomalies_only:
            where += (" AND json_valid(d.breakdown)"
                      " AND (json_extract(d.breakdown,'$.meter_review')=1"
                      " OR json_extract(d.breakdown,'$.boundary_review')=1"
                      " OR json_extract(d.breakdown,'$.coverage_gap')=1)")
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM monitoring_data d WHERE {where}", params
        ).fetchone()["c"]
        rows = conn.execute(
            f"""SELECT d.*, m.name AS machine_name,
                       (SELECT e.status FROM monitoring_review_events e
                        WHERE e.monitoring_id=d.id ORDER BY e.id DESC LIMIT 1)
                        AS review_status,
                       (SELECT CASE WHEN json_valid(pr.breakdown) AND
                         (json_extract(pr.breakdown,'$.meter_review')=1 OR
                          json_extract(pr.breakdown,'$.boundary_review')=1)
                         THEN 0 ELSE 1 END
                        FROM monitoring_data pr
                        WHERE pr.machine_id=d.machine_id AND
                          (pr.timestamp<d.timestamp OR
                           (pr.timestamp=d.timestamp AND pr.id<d.id))
                        ORDER BY pr.timestamp DESC,pr.id DESC LIMIT 1)
                        AS review_event_start
                FROM monitoring_data d
                LEFT JOIN project_machines m ON m.id = d.machine_id
                WHERE {where}
                ORDER BY d.timestamp DESC, d.id DESC
                LIMIT ? OFFSET ?""",
            params + [page_size, (page - 1) * page_size]
        ).fetchall()
        return jsonify({
            "status": "成功",
            "data": [dict(r) for r in rows],
            "total": total, "page": page, "page_size": page_size,
        })
    finally:
        conn.close()


def _continuity_local_only(function):
    """No new unauthenticated LAN surface. Ignore forwarded IP headers."""
    from functools import wraps
    from ipaddress import ip_address
    @wraps(function)
    def guarded(*args,**kwargs):
        try: allowed=ip_address(request.remote_addr or "").is_loopback
        except ValueError: allowed=False
        if not allowed:
            return jsonify({"status":"失败","msg":"连续计量核对与报告操作仅允许本机访问，请在服务所在电脑打开 http://127.0.0.1:5000"}),403
        return function(*args,**kwargs)
    return guarded


@app.route('/api/projects/<int:project_id>/monitoring/segments', methods=['GET'])
@_continuity_local_only
def api_monitoring_segments(project_id):
    from core.meter_continuity import segments_for_machine
    from core.project_manager import get_db
    conn=get_db()
    try:
        mid=request.args.get("machine_id",type=int)
        machines=conn.execute("SELECT id,name FROM project_machines WHERE project_id=? "+("AND id=? " if mid else "")+"ORDER BY sort_order,id",(project_id,mid) if mid else (project_id,)).fetchall()
        result=[]
        for machine in machines:
            for segment in segments_for_machine(conn,machine["id"]): result.append({**segment,"machine_name":machine["name"]})
        return jsonify({"status":"成功","segments":result})
    finally: conn.close()


@app.route('/api/projects/<int:project_id>/monitoring/continuity/preflight', methods=['POST'])
@_continuity_local_only
def api_continuity_preflight(project_id):
    from core.meter_continuity import preflight
    from core.project_manager import get_db
    body=request.get_json(silent=True) or {};conn=get_db()
    try: return jsonify({"status":"成功",**preflight(conn,project_id,body.get("start_date"),body.get("end_date"))})
    except ValueError as exc: return jsonify({"status":"失败","msg":str(exc)}),400
    finally: conn.close()


@app.route('/api/projects/<int:project_id>/monitoring/quality/recheck', methods=['POST'])
@_continuity_local_only
def api_quality_recheck(project_id):
    from core.continuity_probe import start_probe
    body=request.get_json(silent=True) or {}
    try:
        if _monitoring_backfill_active(project_id): raise ValueError("请等待当前历史回溯结束")
        job_id=start_probe(project_id,int(body.get("machine_id")),int(body.get("start_row_id")),force=True)
        return jsonify({"status":"成功","job_id":job_id})
    except (TypeError,ValueError) as exc: return jsonify({"status":"失败","msg":str(exc)}),400


@app.route('/api/projects/<int:project_id>/monitoring/quality/probes/<job_id>', methods=['GET','POST'])
@_continuity_local_only
def api_quality_probe(project_id,job_id):
    from core.project_manager import get_db
    from core.continuity_probe import apply_probe,refresh_probe
    conn=get_db()
    try:
        row=conn.execute("SELECT * FROM monitoring_gap_probes WHERE id=? AND project_id=?",(job_id,project_id)).fetchone()
        if not row: return jsonify({"status":"失败","msg":"回查任务不存在"}),404
        if request.method=="POST":
            if _monitoring_backfill_active(project_id): raise ValueError("请等待当前历史回溯结束")
            body=request.get_json(silent=True) or {}
            if body.get("action")=="refresh":
                return jsonify({"status":"成功","job_status":"ready","result":refresh_probe(job_id)})
            return jsonify({"status":"成功",**apply_probe(job_id,str(body.get("reviewer") or ""),str(body.get("note") or ""))})
        result=json.loads(row["result_json"])
        result.pop("rows",None);result.pop("signature",None)
        return jsonify({"status":"成功","job_status":row["status"],"result":result})
    except ValueError as exc: return jsonify({"status":"失败","msg":str(exc)}),409
    finally: conn.close()


@app.route('/api/projects/<int:project_id>/monitoring/quality', methods=['GET'])
@_continuity_local_only
def api_monitoring_quality(project_id):
    """One case per physical or sampling event, including uncovered periods."""
    from core.project_manager import get_db
    from core.monitoring_quality import cases_for_machine
    conn = get_db()
    try:
        machine_id = request.args.get("machine_id", type=int)
        machines = conn.execute(
            "SELECT m.id,m.name,p.baseline_date FROM project_machines m "
            "JOIN carbon_projects p ON p.id=m.project_id WHERE m.project_id=? "
            + ("AND m.id=? " if machine_id else "") + "ORDER BY m.sort_order,m.id",
            (project_id,machine_id) if machine_id else (project_id,)).fetchall()
        cases = []
        for machine in machines:
            events, _ = cases_for_machine(conn, machine["id"], machine["baseline_date"])
            for event in events:
                event["machine_id"] = machine["id"]
                event["machine_name"] = machine["name"]
                # A list view only needs candidate baseline IDs and counts.
                event["baseline_candidates"] = event.get("drop_ids", [])
                event.pop("affected_ids", None)
                cases.append(event)
        return jsonify({"status": "成功", "cases": cases, "count": len(cases)})
    finally:
        conn.close()


@app.route('/api/projects/<int:project_id>/monitoring/quality/preview', methods=['POST'])
@_continuity_local_only
def api_monitoring_quality_preview(project_id):
    from core.project_manager import get_db
    from core.monitoring_quality import preview_decision
    body = request.get_json(silent=True) or {}
    try:
        machine_id = int(body.get("machine_id"))
        start_row_id = int(body.get("start_row_id"))
        baseline = body.get("baseline_row_id")
        baseline = int(baseline) if baseline not in (None, "") else None
        kind = str(body.get("kind") or "")
        resolution = str(body.get("resolution") or "pending")
    except (TypeError, ValueError):
        return jsonify({"status": "失败", "msg": "事件参数不完整"}), 400
    conn = get_db()
    try:
        result = preview_decision(conn, project_id, machine_id, kind,
                                  start_row_id, resolution, baseline)
        return jsonify({"status": "成功", "preview": result})
    except ValueError as exc:
        return jsonify({"status": "失败", "msg": str(exc)}), 400
    finally:
        conn.close()


@app.route('/api/projects/<int:project_id>/monitoring/quality/apply', methods=['POST'])
@_continuity_local_only
def api_monitoring_quality_apply(project_id):
    from core.project_manager import get_db
    from core.monitoring_quality import apply_decision
    body = request.get_json(silent=True) or {}
    try:
        machine_id = int(body.get("machine_id"))
        start_row_id = int(body.get("start_row_id"))
        baseline = body.get("baseline_row_id")
        baseline = int(baseline) if baseline not in (None, "") else None
        kind = str(body.get("kind") or "")
        resolution = str(body.get("resolution") or "pending")
    except (TypeError, ValueError):
        return jsonify({"status": "失败", "msg": "事件参数不完整"}), 400
    if _monitoring_backfill_active(project_id):
        return jsonify({"status": "失败", "msg": "回溯任务仍在运行，请结束后重新预览"}), 409
    conn = get_db()
    try:
        result = apply_decision(
            conn, project_id, machine_id, kind, start_row_id, resolution,
            baseline, str(body.get("signature") or ""),
            str(body.get("reviewer") or ""), str(body.get("note") or ""),
            str(body.get("evidence") or ""))
        return jsonify({"status": "成功", **result})
    except ValueError as exc:
        return jsonify({"status": "失败", "msg": str(exc)}), 409
    except Exception as exc:
        return jsonify({"status": "失败", "msg": str(exc)}), 500
    finally:
        conn.close()


@app.route('/api/projects/<int:project_id>/monitoring/<int:data_id>/review', methods=['GET', 'POST'])
def api_monitoring_review(project_id, data_id):
    """Show adjacent readings and preserve a reviewer's decision without changing accounting."""
    from core.project_manager import get_db
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT d.*,m.name AS machine_name FROM monitoring_data d
               LEFT JOIN project_machines m ON m.id=d.machine_id
               WHERE d.id=? AND d.project_id=?""", (data_id, project_id)).fetchone()
        if not row:
            return jsonify({"status": "失败", "msg": "监测记录不存在"}), 404
        if request.method == 'POST':
            body = request.get_json(silent=True) or {}
            status = body.get("review_status")
            if status not in ("checked_raw", "needs_fix", "pending"):
                return jsonify({"status": "失败", "msg": "核对结论无效"}), 400
            note = str(body.get("note") or "").strip()
            reviewer = str(body.get("reviewer") or "").strip()
            if not note or not reviewer:
                return jsonify({"status": "失败", "msg": "请填写核对人和核对依据"}), 400
            if len(note) > 2000 or len(reviewer) > 80:
                return jsonify({"status": "失败", "msg": "核对内容过长"}), 400
            conn.execute(
                """INSERT INTO monitoring_review_events
                   (monitoring_id,project_id,status,note,reviewer)
                   VALUES (?,?,?,?,?)""",
                (data_id, project_id, status, note, reviewer))
            conn.commit()
        previous = conn.execute(
            """SELECT id,timestamp,raw_data,carbon_reduction,breakdown
               FROM monitoring_data WHERE machine_id=?
               AND (timestamp<? OR (timestamp=? AND id<?))
               ORDER BY timestamp DESC,id DESC LIMIT 1""",
            (row["machine_id"],row["timestamp"],row["timestamp"],row["id"])).fetchone()
        next_row = conn.execute(
            """SELECT id,timestamp,raw_data,carbon_reduction,breakdown
               FROM monitoring_data WHERE machine_id=?
               AND (timestamp>? OR (timestamp=? AND id>?))
               ORDER BY timestamp,id LIMIT 1""",
            (row["machine_id"],row["timestamp"],row["timestamp"],row["id"])).fetchone()
        event_origin = event_previous = None
        try:
            current_breakdown = json.loads(row["breakdown"] or "{}")
        except (TypeError, ValueError):
            current_breakdown = {}
        if current_breakdown.get("meter_review") or current_breakdown.get("boundary_review"):
            earlier = conn.execute(
                """SELECT id,timestamp,raw_data,breakdown FROM monitoring_data
                   WHERE machine_id=? AND
                     (timestamp<? OR (timestamp=? AND id<=?))
                   ORDER BY timestamp DESC,id DESC""",
                (row["machine_id"],row["timestamp"],row["timestamp"],row["id"]))
            for candidate in earlier:
                try:
                    candidate_breakdown = json.loads(candidate["breakdown"] or "{}")
                except (TypeError, ValueError):
                    candidate_breakdown = {}
                if candidate_breakdown.get("meter_review") or candidate_breakdown.get("boundary_review"):
                    event_origin = dict(candidate)
                else:
                    event_previous = dict(candidate)
                    break
        events = conn.execute(
            """SELECT status,note,reviewer,created_at FROM monitoring_review_events
               WHERE monitoring_id=? ORDER BY id DESC LIMIT 20""", (data_id,)).fetchall()
        return jsonify({"status":"成功","row":dict(row),
                        "previous":dict(previous) if previous else None,
                        "next":dict(next_row) if next_row else None,
                        "event_origin":event_origin,
                        "event_previous":event_previous,
                        "reviews":[dict(e) for e in events]})
    finally:
        conn.close()


def _monitoring_backfill_active(project_id):
    from core import backfill
    return any(job.get("status") == "running" and
               job.get("project_id") in (None, project_id)
               for job in backfill._jobs.values())


@app.route('/api/projects/<int:project_id>/monitoring/bulk-delete', methods=['POST'])
def api_bulk_delete_monitoring(project_id):
    """Archive and delete explicitly selected flagged rows in one transaction."""
    from uuid import uuid4
    from core.project_manager import get_db
    from core.reduction_ledger import reconcile_machine, refresh_totals
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > 100 or any(
            not isinstance(x, int) or isinstance(x, bool) or x <= 0 for x in ids):
        return jsonify({"status": "失败", "msg": "请选择 1—100 条待核对记录"}), 400
    if len(set(ids)) != len(ids):
        return jsonify({"status": "失败", "msg": "选择中有重复记录"}), 400
    if _monitoring_backfill_active(project_id):
        return jsonify({"status": "失败", "msg": "该项目正在回溯历史，请结束后再删除，避免重新入库"}), 409
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" for _ in ids)
        project = conn.execute("SELECT lifecycle_status FROM carbon_projects WHERE id=?",
                               (project_id,)).fetchone()
        if not project or project["lifecycle_status"] != "active":
            conn.rollback()
            return jsonify({"status": "失败", "msg": "项目不存在或已作废"}), 409
        rows = conn.execute(
            f"SELECT * FROM monitoring_data WHERE project_id=? AND id IN ({placeholders})",
            [project_id, *ids]).fetchall()
        if len(rows) != len(ids):
            conn.rollback()
            return jsonify({"status": "失败", "msg": "部分记录已变化，请刷新后重新选择"}), 409
        for row in rows:
            bd = json.loads(row["breakdown"] or "{}")
            if not (bd.get("meter_review") or bd.get("boundary_review")):
                conn.rollback()
                return jsonify({"status": "失败", "msg": "仅允许批量删除待核对记录"}), 400
        batch_id = uuid4().hex
        for row in rows:
            conn.execute(
                "INSERT INTO monitoring_deletion_archive "
                "(batch_id,original_id,project_id,machine_id,row_json) VALUES (?,?,?,?,?)",
                (batch_id,row["id"],project_id,row["machine_id"],
                 json.dumps(dict(row), ensure_ascii=False)))
        conn.executemany("DELETE FROM monitoring_data WHERE id=? AND project_id=?",
                         [(x,project_id) for x in ids])
        for machine_id in sorted({row["machine_id"] for row in rows if row["machine_id"]}):
            reconcile_machine(machine_id, apply=True, conn=conn)
        refresh_totals(conn, project_id)
        conn.commit()
        return jsonify({"status": "成功", "deleted": len(rows), "batch_id": batch_id,
                        "msg": "已归档并删除，可在本窗口恢复本批次"})
    except Exception as exc:
        conn.rollback()
        return jsonify({"status": "失败", "msg": str(exc)}), 500
    finally:
        conn.close()


@app.route('/api/projects/<int:project_id>/monitoring/deleted-batches', methods=['GET'])
def api_monitoring_deleted_batches(project_id):
    from core.project_manager import get_db
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT batch_id,MIN(deleted_at) AS deleted_at,COUNT(*) AS row_count "
            "FROM monitoring_deletion_archive WHERE project_id=? AND restored_at IS NULL "
            "GROUP BY batch_id ORDER BY deleted_at DESC LIMIT 10", (project_id,)).fetchall()
        return jsonify({"status": "成功", "batches": [dict(r) for r in rows]})
    finally:
        conn.close()


@app.route('/api/projects/<int:project_id>/monitoring/deleted-batches/<batch_id>/restore',
           methods=['POST'])
def api_restore_monitoring_batch(project_id, batch_id):
    from core.project_manager import get_db
    from core.reduction_ledger import reconcile_machine, refresh_totals
    if _monitoring_backfill_active(project_id):
        return jsonify({"status": "失败", "msg": "该项目正在回溯历史，请结束后再恢复"}), 409
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT original_id,machine_id,row_json FROM monitoring_deletion_archive "
            "WHERE project_id=? AND batch_id=? AND restored_at IS NULL",
            (project_id,batch_id)).fetchall()
        if not rows:
            conn.rollback()
            return jsonify({"status": "失败", "msg": "没有可恢复的记录"}), 404
        project = conn.execute("SELECT lifecycle_status FROM carbon_projects WHERE id=?",
                               (project_id,)).fetchone()
        if not project or project["lifecycle_status"] != "active":
            conn.rollback()
            return jsonify({"status": "失败", "msg": "项目不存在或已作废"}), 409
        restored = []
        for item in rows:
            row = json.loads(item["row_json"])
            if conn.execute(
                    "SELECT 1 FROM monitoring_data WHERE id=? OR "
                    "(machine_id=? AND timestamp=?) LIMIT 1",
                    (row["id"],row["machine_id"],row["timestamp"])).fetchone():
                conn.rollback()
                return jsonify({"status": "失败", "msg": "原时间已有监测记录，请先核对，未执行恢复"}), 409
            columns = ("id","project_id","machine_id","timestamp","raw_data",
                       "carbon_reduction","cumulative_reduction","breakdown","source","created_at")
            conn.execute(
                "INSERT INTO monitoring_data (" + ",".join(columns) + ") VALUES (" +
                ",".join("?" for _ in columns) + ")",
                [row.get(k) for k in columns])
            restored.append(row)
        conn.execute(
            "UPDATE monitoring_deletion_archive SET restored_at=datetime('now','localtime') "
            "WHERE project_id=? AND batch_id=? AND restored_at IS NULL",
            (project_id,batch_id))
        for machine_id in sorted({row["machine_id"] for row in restored if row["machine_id"]}):
            reconcile_machine(machine_id, apply=True, conn=conn)
        refresh_totals(conn, project_id)
        conn.commit()
        return jsonify({"status": "成功", "restored": len(restored)})
    except Exception as exc:
        conn.rollback()
        return jsonify({"status": "失败", "msg": str(exc)}), 500
    finally:
        conn.close()


@app.route('/api/projects/<int:project_id>/monitoring/<int:data_id>', methods=['DELETE'])
def api_delete_monitoring(project_id, data_id):
    """删除项目下一条监测数据，并重算项目累计。"""
    from core.project_manager import get_db
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id,machine_id FROM monitoring_data WHERE id = ? AND project_id = ?",
            (data_id, project_id)).fetchone()
        if not row:
            return jsonify({"status": "失败", "msg": "记录不存在"})
        conn.execute("DELETE FROM monitoring_data WHERE id = ? AND project_id = ?",
                     (data_id, project_id))
        from core.reduction_ledger import reconcile_machine, refresh_totals
        machine_id = row["machine_id"]
        if machine_id:
            conn.commit()
            reconcile_machine(machine_id, apply=True)
        refresh_totals(conn, project_id)
        conn.commit()
        return jsonify({"status": "成功", "msg": "已删除"})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})
    finally:
        conn.close()


@app.route('/api/projects/<int:project_id>/monitoring/latest', methods=['GET'])
def api_get_latest_monitoring(project_id):
    """项目最新记录、各机器最新快照及项目累计；机器快照用于固定设备显示。"""
    from core.project_manager import get_db, project_cumulative
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM monitoring_data WHERE project_id=? "
            "ORDER BY timestamp DESC,id DESC LIMIT 1", (project_id,)).fetchone()
        snapshots = conn.execute(
            """SELECT * FROM monitoring_data WHERE id IN (
                SELECT (SELECT d.id FROM monitoring_data d WHERE d.machine_id=m.id
                        AND d.project_id=? ORDER BY d.timestamp DESC,d.id DESC LIMIT 1)
                FROM project_machines m WHERE m.project_id=?
                UNION ALL
                SELECT (SELECT d.id FROM monitoring_data d WHERE d.project_id=?
                        AND d.machine_id IS NULL ORDER BY d.timestamp DESC,d.id DESC LIMIT 1)
            ) ORDER BY machine_id,id""", (project_id,project_id,project_id)).fetchall()
        return jsonify({"status": "成功", "data": dict(row) if row else None,
                        "machine_latest": [dict(item) for item in snapshots],
                        "project_cumulative": project_cumulative(conn, project_id)})
    finally:
        conn.close()


@app.route('/api/projects/<int:project_id>/monitoring/manual', methods=['POST'])
def api_manual_monitoring(project_id):
    """
    手动录入监测数据。
    请求体: { "raw_data": {...}, "timestamp": "2026-06-10 14:30:00" }
    """
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    data = request.get_json(force=True, silent=True) or {}
    raw_data = data.get("raw_data", {})
    # 容错：如果 raw_data 是 JSON 字符串（前端双重序列化），尝试解析
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except json.JSONDecodeError:
            return jsonify({"status": "失败", "msg": "raw_data 不是有效的 JSON 字符串"})
    if not isinstance(raw_data, dict):
        return jsonify({"status": "失败", "msg": f"raw_data 类型错误，期望对象，实际: {type(raw_data).__name__}"})
    if not raw_data:
        return jsonify({"status": "失败", "msg": "raw_data 不能为空"})
    ts = data.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    try:
        # 录入到哪台机器：不传就落到项目主机器（老调用方无感）
        machine_id = _resolve_machine_id(project_id, data.get("machine_id"))
        result = DataCollector.collect_and_store(machine_id, raw_data,
                                                 source="manual", timestamp=ts)
        return jsonify({"status": "成功", "result": result, "machine_id": machine_id})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


# ═══════════════════════════════════════════════════════════════
# VCER 同步 API
# ═══════════════════════════════════════════════════════════════

def _sync_project_from_vcer(project_id):
    """把单个项目的碳资产与阶段状态从促进会同步到本地。

    完全依据该项目自己存的查询密钥 + 密码去查（`/cn/api/vcer/project_by_key`）。

    **编号以促进会为准**：平台返回的编号和本地不一致时会更新本地并说明
    （所以后台改了编号，这里同步一次就能跟上）。但**平台没返回编号时绝不清空本地**
    —— 那会冲掉「已预分配但平台还没审核通过」的编号（预分配编号只存本地）。

    查一次接口同时拿到编号和阶段状态，不重复请求。

    返回 {"status", "msg", "details", "problems"}；单项目路由和批量路由共用这一份逻辑。
    """
    proj = project_manager.get_project(project_id)
    if not proj:
        return {"status": "失败", "msg": "项目不存在", "details": []}

    try:
        vcer = VCERClient(config.VCER_API_URL)

        # 获取 enterprise_id：优先从数据库配置读取，其次从 config，最后从 project_code 提取
        enterprise_cfg = project_manager.get_enterprise_config()
        enterprise_id = (enterprise_cfg or {}).get("enterprise_id", "") or config.ENTERPRISE_ID
        sync_details = []
        # 同步过程中攒的提示原先只弹个 alert 就丢了，这里同时记进日志，
        # 事后想查「上次同步为什么没成功」有据可依。
        sync_log = logging.getLogger("sync")

        # ── 第一步：查一次促进会，拿到权威的编号 + 阶段状态 ──
        project_code = (proj.get("project_code") or "").strip()
        status_data = None
        if proj.get("vcer_query_key"):
            status_data = vcer.get_project_status(
                proj["vcer_query_key"], proj.get("vcer_query_pwd"))
            # 返回格式: {success: true, project: {...}}（vcer_client._get 直接给原始响应）
            if status_data.get("success") or status_data.get("status") == "成功":
                proj_info = status_data.get("project", {}) or {}
                remote_code = str(proj_info.get("project_code") or "").strip()
                if remote_code and remote_code != project_code:
                    # 促进会是权威源：后台改过编号，这里跟上
                    project_manager.update_project(project_id, {"project_code": remote_code})
                    sync_details.append(
                        f"项目编号已更新: {project_code or '（本地为空）'} → {remote_code}")
                    project_code = remote_code
                elif not remote_code:
                    # 平台没给编号就保持本地不动 —— 预分配编号只存本地，平台还不知道
                    sync_details.append(
                        "促进会未返回项目编号（本地保持 " + (project_code or "空")
                        + "；预分配编号要等平台审核通过才会出现）")
            else:
                raw_keys = list(status_data.keys()) if isinstance(status_data, dict) else str(type(status_data))
                err_msg = status_data.get('message', status_data.get('msg', '未知错误'))
                sync_details.append(f"查询项目信息失败: {err_msg}（响应字段: {raw_keys}）")
        else:
            sync_details.append("未同步项目状态（缺少查询密钥 vcer_query_key）")

        # ── 补全 enterprise_id：从 project_code 提取（如 GDES-VCER-101-1 → 101）──
        if not enterprise_id and project_code:
            import re
            m = re.match(r'GDES-VCER-(\d+)-\d+', project_code)
            if m:
                enterprise_id = m.group(1)
                sync_details.append(f"从项目编号提取企业序号: {enterprise_id}")

        # ── 第二步：同步碳资产（需要 enterprise_id + project_code）──
        if project_code and enterprise_id:
            assets = vcer.get_carbon_assets(enterprise_id, project_code)
            if assets.get("status") == "成功":
                project_manager.sync_carbon_assets(project_id, assets)
                sync_details.append("碳资产已同步")
            else:
                raw_keys = list(assets.keys()) if isinstance(assets, dict) else str(type(assets))
                sync_details.append(f"碳资产同步失败: {assets.get('msg', '未知错误')}（响应字段: {raw_keys}）")
        elif not project_code:
            sync_details.append("未同步碳资产（缺少 project_code，需等待促进会分配或填写查询密钥）")
        else:
            sync_details.append(f"未同步碳资产（缺少 enterprise_id，请在「设置」中填写企业序号，或确认项目编号格式正确）")

        # ── 第三步：同步项目阶段状态（复用第一步那次查询，不重复请求）──
        if status_data and (status_data.get("success") or status_data.get("status") == "成功"):
            proj_data = status_data.get("project", {}) or {}
            passed_map = {
                s: 1 if proj_data.get(f"stage{s}_status") == "已通过" else 0
                for s in range(1, 6)
            }
            project_manager.update_stages_passed(project_id, passed_map)
            stage_updated = sum(passed_map.values())
            sync_details.append(f"项目状态已同步（{stage_updated} 个阶段通过）")
        elif status_data:
            # 第一步已经记过「查询失败」了，这里不重复记
            pass

        # Track rounds independently of the status and asset requests.
        try:
            from core.reduction_ledger import sync_verification
            verified = sync_verification(project_id)
            if verified.get("status") != "成功":
                sync_details.append("核查区间同步失败，待核证值保留上次结果")
        except Exception as exc:
            sync_details.append(f"核查区间同步失败: {exc}")

        project_manager.update_sync_time(project_id)
        # 阶段1/2 已通过时，预分配编号自动转正
        try:
            from core.code_reservation import confirm_if_passed
            if confirm_if_passed(project_id):
                sync_details.append("预分配编号已转正")
        except Exception:
            pass
        # 有失败/跳过项就记 warning，全是正常进度就记 info。
        # problems 同时回传给前端，用于区分「完全成功」和「同步了但有项没成」——
        # 否则调用方只看到 status=成功，会以为碳资产也刷新了。
        bad_kw = ("失败", "未同步", "未找到", "缺少")
        problems = [d for d in sync_details if any(k in d for k in bad_kw)]
        try:
            sync_log.log(logging.WARNING if problems else logging.INFO,
                         "项目 #%s 同步完成：%s", project_id,
                         "；".join(sync_details) or "无附加信息",
                         extra={"project_id": project_id})
        except Exception:
            pass
        return {"status": "成功", "msg": "同步完成", "details": sync_details,
                "problems": problems}
    except Exception as e:
        return {"status": "失败", "msg": str(e), "details": []}


@app.route('/api/projects/<int:project_id>/sync', methods=['POST'])
def api_sync_to_vcer(project_id):
    """同步单个项目：碳资产 + 阶段状态（依据该项目的查询密钥）。"""
    return jsonify(_sync_project_from_vcer(project_id))


@app.route('/api/projects/sync-all', methods=['POST'])
def api_sync_all_to_vcer():
    """把所有项目的碳资产与阶段状态从促进会刷新一遍。

    逐个项目走 `_sync_project_from_vcer`（各自用自己的查询密钥+密码）。
    没有查询密钥的项目跳过并说明原因——不能因为一个项目卡住就中断整批。

    返回:
        {"status": "成功", "total": N, "ok_count": M,
         "results": [{"id", "name", "ok", "msg", "details": [...]}, ...]}
    """
    try:
        projects = project_manager.list_projects()
    except Exception as e:
        return jsonify({"status": "失败", "msg": f"读取项目列表失败: {e}"})

    results = []
    for p in projects:
        if p.get("lifecycle_status") != "active":
            continue
        pid = p.get("id")
        name = p.get("project_name", "")
        if not (p.get("vcer_query_key") or "").strip():
            results.append({"id": pid, "name": name, "ok": False,
                            "msg": "未填写查询密钥，跳过（无法向促进会查询）", "details": []})
            continue
        r = _sync_project_from_vcer(pid)
        problems = r.get("problems") or []
        results.append({
            "id": pid, "name": name,
            # ok=完全成功；partial=同步了但有项没成（如碳资产拉不到）；都不满足=整体失败
            "ok": r.get("status") == "成功" and not problems,
            "partial": bool(problems) and r.get("status") == "成功",
            "msg": r.get("msg", ""),
            "problems": problems,
            "details": r.get("details") or [],
        })

    ok_count = sum(1 for x in results if x["ok"])
    partial_count = sum(1 for x in results if x.get("partial"))
    return jsonify({"status": "成功", "total": len(results),
                    "ok_count": ok_count, "partial_count": partial_count,
                    "results": results})


# ═══════════════════════════════════════════════════════════════
# VCER 自助申请 API（代理到促进会平台）
# ═══════════════════════════════════════════════════════════════

def _freeze_ten_year_on_submit(project_id, total):
    """阶段一提交成功后，固化「十年预估减排总量」。

    固化值 = 用户在提交表单填的「十年预估减排总量」（即 PDD 报告中的值），
    此后 ten_year_total 读 pending_validation_10yr（第 1 级优先级）保持固定，
    不再随监测数据变化。同时固化 projection_10yr（十年逐年预估表）。
    """
    try:
        project_manager.update_project(project_id, {"pending_validation_10yr": round(total, 2)})
    except Exception:
        pass
    try:
        from core.projection import compute_projection, save_projection
        proj = project_manager.get_project(project_id)
        if proj and not proj.get("projection_10yr"):
            rows = compute_projection(proj) or []
            if rows:
                save_projection(project_id, rows)
    except Exception:
        pass


@app.route('/api/projects/<int:project_id>/vcer/submit', methods=['POST'])
def api_vcer_submit(project_id):
    """
    代理提交 VCER 各阶段申请材料到促进会平台。

    请求格式: multipart/form-data
    必需字段:
        stage: 1-5（阶段编号）
        query_password: 查询密码（阶段1必填，阶段2-5用于鉴权）

    阶段1 额外字段: enterprise_name, project_name
    阶段1 文件: application_form (.pdf), design_doc (.docx/.doc)
    阶段2 文件: contract (.pdf)
    阶段3 字段: total_reduction, 文件: verification_report (.pdf)
    阶段4 字段: buyer_name, seller_name, traded_amount, 文件: contract, certificate
    阶段5 字段: buyer_name, offset_amount, 文件: offset_declaration, certificate
    """
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    try:
        stage = int(request.form.get('stage', 0))
    except (ValueError, TypeError):
        return jsonify({"status": "失败", "msg": "缺少有效参数: stage"})

    if stage not in (1, 2, 3, 4, 5):
        return jsonify({"status": "失败", "msg": f"无效的阶段: {stage}"})

    if stage == 3:
        # Scope acknowledgements/relaxed quality gate are local-only until the app has authentication.
        from ipaddress import ip_address
        try: local=ip_address(request.remote_addr or "").is_loopback
        except ValueError: local=False
        if not local: return jsonify({"status":"失败","msg":"核查提交请在服务所在电脑通过 127.0.0.1 操作"}),403
        from core.meter_continuity import preflight,acknowledge
        from core.project_manager import get_db
        start_date=request.form.get("verification_start");end_date=request.form.get("verification_end")
        if not start_date or not end_date: return jsonify({"status":"失败","msg":"请填写核查起止日期（本地检查用）"}),400
        quality_conn=get_db()
        try:
            scope=preflight(quality_conn,project_id,start_date,end_date)
            if scope["requires_confirmation"] and request.form.get("continuity_ack")!=scope["signature"]:
                return jsonify({"status":"需要确认","code":"continuity_confirmation_required","msg":"核查期包含未确认缺口或计量阶段边界，请查看提示后确认提交","preflight":scope}),409
            acknowledge(quality_conn,project_id,"vcer_stage3",scope,request.form.get("continuity_ack"));quality_conn.commit()
        except ValueError as exc: return jsonify({"status":"失败","msg":str(exc)}),400
        finally: quality_conn.close()

    vcer = VCERClient(config.VCER_API_URL)

    # 构建表单字段和文件
    form_data = {}
    file_tuples = {}

    # 从项目记录和配置获取基本信息
    enterprise_cfg = project_manager.get_enterprise_config() or {}
    ent_name = enterprise_cfg.get("enterprise_name", "") or proj.get("project_name", "")
    ent_id = enterprise_cfg.get("enterprise_id", "") or config.ENTERPRISE_ID

    if stage == 1:
        # 阶段1：创建项目申请
        form_data['enterprise_name'] = request.form.get('enterprise_name', ent_name).strip()
        form_data['project_name'] = request.form.get('project_name', proj.get('project_name', '')).strip()
        form_data['project_type'] = request.form.get('project_type', proj.get('project_type', proj.get('hook_name', ''))).strip()
        form_data['project_summary'] = request.form.get('project_summary', proj.get('project_summary', '')).strip()
        form_data['contact_person'] = request.form.get('contact_person', '').strip()
        form_data['contact_phone'] = request.form.get('contact_phone', '').strip()
        form_data['query_password'] = request.form.get('query_password', '').strip()

        if not form_data['enterprise_name'] or not form_data['project_name']:
            return jsonify({"status": "失败", "msg": "企业名称和项目名称不能为空"})
        if not form_data['query_password']:
            return jsonify({"status": "失败", "msg": "请设置查询密码（用于后续阶段查询和提交）"})

        # 文件：申请书 + 设计文件
        for file_key in ['application_form', 'design_doc']:
            f = request.files.get(file_key)
            if f and f.filename:
                file_tuples[file_key] = (f.filename, f.read(), f.content_type or 'application/octet-stream')

        if not file_tuples:
            return jsonify({"status": "失败", "msg": "请上传申请书(.pdf)和项目设计文件(.docx)"})

        result = vcer.post_form('/cn/api/vcer/create_project', form_data, file_tuples)

        # 提交成功后，固化「十年预估减排总量」（用户在表单填的值 = PDD 报告中的值）
        if result.get('status') == '成功':
            ten_year_total = request.form.get('ten_year_total', '').strip()
            if ten_year_total:
                try:
                    _freeze_ten_year_on_submit(project_id, float(ten_year_total))
                except (ValueError, TypeError):
                    pass

        # 成功后自动保存 query_key 和密码
        if result.get('status') == '成功' and result.get('raw', {}).get('query_key'):
            qk = result['raw']['query_key']
            update_data = {'vcer_query_key': qk}
            if form_data.get('query_password'):
                update_data['vcer_query_pwd'] = form_data['query_password']
            project_manager.update_project(project_id, update_data)
            result['query_key_saved'] = qk

    else:
        # 阶段2-5：提交材料
        query_key = (request.form.get('query_key') or proj.get('vcer_query_key') or '').strip()
        query_password = (request.form.get('query_password') or proj.get('vcer_query_pwd') or '').strip()

        if not query_key:
            return jsonify({"status": "失败", "msg": "缺少查询密钥，请先在阶段1创建申请，或在项目中填写查询密钥"})
        if not query_password:
            return jsonify({"status": "失败", "msg": "请输入查询密码"})

        form_data['query_key'] = query_key
        form_data['query_password'] = query_password
        form_data['stage'] = stage

        if stage == 2:
            f = request.files.get('contract')
            if not f or not f.filename:
                return jsonify({"status": "失败", "msg": "请上传合同(.pdf)"})
            file_tuples['contract'] = (f.filename, f.read(), f.content_type or 'application/pdf')

        elif stage == 3:
            f = request.files.get('verification_report')
            if not f or not f.filename:
                return jsonify({"status": "失败", "msg": "请上传项目碳减排量核算报告(.pdf)"})
            file_tuples['verification_report'] = (f.filename, f.read(), f.content_type or 'application/pdf')
            total_reduction = request.form.get('total_reduction', '').strip()
            if total_reduction:
                form_data['total_reduction'] = total_reduction

        elif stage == 4:
            for file_key in ['contract', 'certificate']:
                f = request.files.get(file_key)
                if f and f.filename:
                    file_tuples[file_key] = (f.filename, f.read(), f.content_type or 'application/pdf')
            if 'contract' not in file_tuples:
                return jsonify({"status": "失败", "msg": "请上传合同(.pdf)"})
            if 'certificate' not in file_tuples:
                return jsonify({"status": "失败", "msg": "请上传碳减排量核证证书"})

            for field in ['buyer_name', 'seller_name', 'traded_amount']:
                val = request.form.get(field, '').strip()
                if not val:
                    return jsonify({"status": "失败", "msg": f"请填写{field}"})
                form_data[field] = val
            try:
                trade_amount = float(form_data['traded_amount'])
                if not 0 < trade_amount < float('inf'):
                    raise ValueError()
            except ValueError:
                return jsonify({"status": "失败", "msg": "交易量必须是大于 0 的有限数字"})
            # 项目实时持有余额由促进会提交接口校验，本地同步值只供界面提示。

        elif stage == 5:
            for file_key in ['offset_declaration', 'certificate']:
                f = request.files.get(file_key)
                if f and f.filename:
                    file_tuples[file_key] = (f.filename, f.read(), f.content_type or 'application/pdf')
            if 'offset_declaration' not in file_tuples:
                return jsonify({"status": "失败", "msg": "请上传抵消声明文件"})
            if 'certificate' not in file_tuples:
                return jsonify({"status": "失败", "msg": "请上传碳减排量核证证书"})

            buyer_name = request.form.get('buyer_name', '').strip()
            offset_amount = request.form.get('offset_amount', '').strip()
            if not buyer_name:
                return jsonify({"status": "失败", "msg": "请填写买方/抵消方名称"})
            try:
                amount = float(offset_amount)
                if not 0 < amount < float('inf'):
                    raise ValueError()
            except ValueError:
                return jsonify({"status": "失败", "msg": "抵消量必须是大于 0 的有限数字"})
            form_data['buyer_name'] = buyer_name
            form_data['offset_amount'] = offset_amount
            # 抵消方在该项目的持有余额由促进会提交接口校验。

        result = vcer.post_form('/cn/api/vcer/submit_stage', form_data, file_tuples)

    # 提交成功后同步状态
    if result.get('status') == '成功':
        vcer.invalidate_rounds_cache()  # 失效轮次缓存，避免刚提交的新轮次被缓存遮蔽
        try:
            api_sync_to_vcer(project_id)
        except Exception:
            pass

    return jsonify(result)


@app.route('/api/projects/<int:project_id>/vcer/status', methods=['GET'])
def api_vcer_status(project_id):
    """
    查询项目在 VCER 平台的最新状态并同步到本地。
    """
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    query_key = proj.get('vcer_query_key')
    if not query_key:
        return jsonify({"status": "失败", "msg": "项目未关联 VCER 查询密钥，请先完成阶段1申请"})

    try:
        vcer = VCERClient(config.VCER_API_URL)
        resp = vcer.get_project_status(query_key, proj.get('vcer_query_pwd'))

        if resp.get('success') or resp.get('status') == '成功':
            proj_data = resp.get('project', {})
            # 同步阶段通过状态（布尔；具体状态仍现场返回给前端）
            passed_map = {
                s: 1 if proj_data.get(f'stage{s}_status') == '已通过' else 0
                for s in range(1, 6)
            }
            project_manager.update_stages_passed(project_id, passed_map)
            stage_updated = sum(passed_map.values())
            # 同步 project_code
            if proj_data.get('project_code') and not proj.get('project_code'):
                project_manager.update_project(project_id, {'project_code': proj_data['project_code']})
            # 同步 query_key（如果本地没有）
            if proj_data.get('query_key') and not proj.get('vcer_query_key'):
                project_manager.update_project(project_id, {'vcer_query_key': proj_data['query_key']})

            # 构建阶段摘要
            stage_names = {1: '项目申请', 2: '项目审定', 3: '项目核查', 4: '项目交易', 5: '碳减排量抵消'}
            stages = []
            for s in range(1, 6):
                status = proj_data.get(f'stage{s}_status', '未提交')
                reason = proj_data.get(f'stage{s}_reason')
                # 如果 reason 是 JSON 字符串，解析为列表用于展示
                if isinstance(reason, str) and reason.strip().startswith('['):
                    try:
                        reason = json.loads(reason)
                    except json.JSONDecodeError:
                        pass
                stages.append({
                    'stage': s,
                    'name': stage_names.get(s, f'阶段{s}'),
                    'status': status,
                    'reason': proj_data.get(f'stage{s}_reason'),
                })

            # 文件下载用的 admin base URL
            admin_base = config.VCER_API_URL.rstrip('/')
            if admin_base.endswith('/cn'):
                admin_base = admin_base[:-3]

            return jsonify({
                "status": "成功",
                "project_code": proj_data.get('project_code'),
                "query_key": proj_data.get('query_key') or query_key,
                "enterprise_name": proj_data.get('enterprise_name'),
                "stages": stages,
                "stage_updated": stage_updated,
                "admin_base_url": admin_base,
            })
        else:
            err_msg = resp.get('message', resp.get('msg', '查询失败'))
            return jsonify({"status": "失败", "msg": err_msg})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/projects/<int:project_id>/vcer/rounds', methods=['GET'])
def api_vcer_rounds(project_id):
    """
    查询项目某阶段的多轮提交历史。
    参数: stage (1-5)
    """
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    query_key = proj.get('vcer_query_key')
    if not query_key:
        return jsonify({"status": "失败", "msg": "项目未关联 VCER 查询密钥"})

    try:
        stage = request.args.get('stage', type=int)
        if stage not in (1, 2, 3, 4, 5):
            return jsonify({"status": "失败", "msg": f"无效的阶段: {stage}"})
    except (ValueError, TypeError):
        return jsonify({"status": "失败", "msg": "缺少参数: stage"})

    vcer = VCERClient(config.VCER_API_URL)
    try:
        resp = vcer._get('/cn/api/vcer/stage_rounds', {'key': query_key, 'stage': stage})

        if resp.get('success') or resp.get('status') == '成功':
            rounds = resp.get('rounds', [])
            return jsonify({"status": "成功", "stage": stage, "rounds": rounds})
        else:
            err_msg = resp.get('message', resp.get('msg', '查询失败'))
            return jsonify({"status": "失败", "msg": err_msg})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


# ═══════════════════════════════════════════════════════════════
# VCER 文件下载代理
# ═══════════════════════════════════════════════════════════════

@app.route('/api/vcer/file', methods=['GET'])
def api_vcer_file_download():
    """
    代理下载 VCER 平台文件（转发到 VCER 服务器的 /db/api/file_view）。
    参数: path — 文件在 VCER 服务器上的完整路径
    """
    file_path = request.args.get('path', '').strip()
    if not file_path:
        return jsonify({"status": "失败", "msg": "缺少文件路径"}), 400

    import urllib.parse
    # VCER 服务器的 file_view 端点是 /db/api/file_view?path=...&download=1
    base = config.VCER_API_URL.rstrip('/')
    if base.endswith('/cn'):
        base = base[:-3]

    proxy_url = f"{base}/db/api/file_view?path={urllib.parse.quote(file_path, safe='')}&download=1"
    import requests as req
    try:
        r = req.get(proxy_url, timeout=30)
        r.raise_for_status()
        from flask import Response
        # 从响应头获取文件名（VCER 服务器通过 Content-Disposition 回传）
        filename = file_path.rsplit('\\', 1)[-1] if '\\' in file_path else file_path.rsplit('/', 1)[-1]
        resp = Response(r.content, content_type=r.headers.get('Content-Type', 'application/octet-stream'))
        resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp
    except Exception as e:
        return jsonify({"status": "失败", "msg": f"文件下载失败: {str(e)}"}), 404


# ═══════════════════════════════════════════════════════════════
# VCER 密码管理 API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/projects/<int:project_id>/vcer/change_password', methods=['POST'])
def api_vcer_change_password(project_id):
    """
    修改 VCER 查询密码（知道原密码）。
    请求体: {old_password, new_password}
    """
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    query_key = proj.get('vcer_query_key')
    if not query_key:
        return jsonify({"status": "失败", "msg": "项目未关联 VCER 查询密钥，请先完成阶段1申请"})

    data = request.get_json(silent=True) or {}
    old_pwd = (data.get('old_password') or '').strip()
    new_pwd = (data.get('new_password') or '').strip()

    if not old_pwd:
        return jsonify({"status": "失败", "msg": "请输入原密码"})
    if not new_pwd or len(new_pwd) < 4:
        return jsonify({"status": "失败", "msg": "新密码至少4位"})

    vcer = VCERClient(config.VCER_API_URL)
    try:
        result = vcer.change_password(query_key, old_pwd, new_pwd)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})

    if result.get('success') or result.get('status') == '成功':
        # 同步更新本地密码
        try:
            project_manager.update_project(project_id, {'vcer_query_pwd': new_pwd})
        except Exception:
            pass
        return jsonify({"status": "成功", "msg": "密码修改成功"})
    else:
        err_msg = result.get('message', result.get('msg', '修改失败'))
        return jsonify({"status": "失败", "msg": err_msg})


@app.route('/api/projects/<int:project_id>/vcer/request_password_reset', methods=['POST'])
def api_vcer_request_password_reset(project_id):
    """
    申请管理员重置密码（忘记密码）。
    """
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    query_key = proj.get('vcer_query_key')
    if not query_key:
        return jsonify({"status": "失败", "msg": "项目未关联 VCER 查询密钥"})

    vcer = VCERClient(config.VCER_API_URL)
    try:
        result = vcer.request_password_reset(query_key)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})

    if result.get('success') or result.get('status') == '成功':
        return jsonify({"status": "成功", "msg": "密码重置申请已提交，请等待管理员审核"})
    else:
        err_msg = result.get('message', result.get('msg', '申请失败'))
        return jsonify({"status": "失败", "msg": err_msg})


# ═══════════════════════════════════════════════════════════════
# 报告 API
# ═══════════════════════════════════════════════════════════════

# 报告类型 → 阶段门禁映射（该阶段已通过则禁止再生成该报告）
_REPORT_STAGE_GATE = {"pdd": 1, "approval": 2}


def _check_report_gate(proj, report_type):
    """阶段门禁：报告类型对应阶段已通过且未开「开发者选项」时，返回错误消息；否则 None。"""
    stage = _REPORT_STAGE_GATE.get(report_type)
    if not stage or not proj.get(f"stage{stage}_passed"):
        return None
    try:
        cfg = project_manager.get_enterprise_config()
        if cfg and cfg.get("dev_mode"):
            return None
    except Exception:
        pass
    return f"该项目第{stage}阶段已通过，禁止生成此报告（可在设置中开启「开发者选项」解禁）"


@app.route('/api/projects/<int:project_id>/report/generate', methods=['POST'])
@_continuity_local_only
def api_generate_report(project_id):
    """
    生成碳减排报告（Word，docxtpl + Jinja2 模板）。

    请求体 JSON:
        report_type: "verification" | "pdd"
        template: 模板文件名/相对路径（可选，来自模板下拉框）
        start_date: 核查起始日期 (YYYY-MM-DD)，可选
        end_date: 核查结束日期 (YYYY-MM-DD)，可选
    """
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    data = request.get_json(force=True, silent=True) or {}
    report_type = data.get("report_type", "verification")
    start_date = data.get("start_date")
    end_date = data.get("end_date")
    template_file = data.get("template")
    filename_template = data.get("filename_template")

    gate_err = _check_report_gate(proj, report_type)
    if gate_err:
        return jsonify({"status": "失败", "msg": gate_err})

    try:
        result = ReportGenerator.generate_word_report(
            proj, report_type, start_date=start_date, end_date=end_date,
            template_file=template_file, filename_template=filename_template,
            continuity_ack=data.get("continuity_ack")
        )
        return jsonify(result), (409 if result.get("status")=="需要确认" else 200)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


def _ten_year_total_for(project_id):
    """算一个项目的「十年预估减排总量」，返回 dict。

    取值优先级（source 标明来源，供前端提示）：
      1. latest_pdd   — 最近一次生成的 PDD 里固化的值（PDD 报的就是这个数）
      2. project_frozen — 项目上已固化的 pending_validation_10yr（阶段一提交后冻结）
      3. annual_estimate — 预估年减排量 × 10
      4. computed     — 按当前监测数据实时计算的十年总量

    全都取不到时 ten_year_total 为 None。
    单项目路由和批量路由共用这一份，别写第二套口径。
    """
    proj = project_manager.get_project(project_id)
    if not proj:
        return {"status": "失败", "msg": "项目不存在"}

    result = {"status": "成功", "ten_year_total": None, "source": None,
              "source_label": "", "generated_at": None, "report_id": None,
              "range_start": None, "range_end": None}

    # 多机器（≥2 台）：**先**按统一口径实时算（各机器各自分段后相加），
    # 与 B.2/B.3/B.4 的多机器表同源。
    #
    # ⚠️ 但**算不出东西时不能就此返回 None** —— 必须落到下面 1~3 步的固化值兜底。
    # 否则会给「有固化申报值但首年段没数据」的项目报「无数据」，
    # 而报告封面（context_builder 同一链条）是会兜底的 → 封面和接口又对不上。
    # （项目 #17 加第二台机器后就是这么暴露的。）
    machine_count = project_manager.count_machines(project_id)
    if machine_count >= 2:
        result["machine_count"] = machine_count
        computed = _ten_year_total_computed(proj, result)
        if computed.get("ten_year_total") is not None:
            return computed
        # 首年段没有监测数据 → 往下走固化值兜底。单机器时这段逻辑一字不动。

    # 1) 最近一次 PDD 里固化的十年总量
    try:
        from core.project_manager import get_db
        conn = get_db()
        try:
            rows = conn.execute(
                """SELECT id, parameters, generated_at FROM reports
                   WHERE project_id = ? AND report_type = 'pdd'
                   ORDER BY id DESC LIMIT 20""",
                (project_id,)
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            try:
                params = json.loads(row["parameters"] or "{}")
            except Exception:
                continue
            # 只认「新口径」生成的报告：旧版把报告表单的区间天数当分母，
            # 固化下来的值可能是被稀释几十倍的错数（那次 30.61 就是这么来的）。
            # 新报告一定会带 ten_year_ready 字段，旧报告没有 —— 据此跳过，
            # 让接口先按统一口径实时算，等用户重新生成 PDD 自然就带上了。
            if "ten_year_ready" not in params:
                continue
            if params.get("ten_year_ready") is False:
                continue
            val = params.get("ten_year_total")
            if val not in (None, ""):
                result.update({
                    "ten_year_total": round(float(val), 2),
                    "source": "latest_pdd",
                    "source_label": "最近生成的 PDD",
                    "generated_at": row["generated_at"],
                    "report_id": row["id"],
                    # 这份 PDD 是按哪个区间算的，前端要展示出来
                    "range_start": params.get("start_date"),
                    "range_end": params.get("end_date"),
                })
                return result
    except Exception:
        pass

    # 2) 项目上已固化的值
    val = proj.get("pending_validation_10yr")
    if val not in (None, ""):
        result.update({
            "ten_year_total": round(float(val), 2),
            "source": "project_frozen",
            "source_label": "项目已固化值",
        })
        return result

    # 3) 预估年减排量 × 10
    val = proj.get("estimated_annual_reduction")
    if val not in (None, ""):
        result.update({
            "ten_year_total": round(float(val) * 10, 2),
            "source": "annual_estimate",
            "source_label": "预估年减排量 × 10",
        })
        return result

    # 4) 统一口径实时算
    return _ten_year_total_computed(proj, result)


def _ten_year_total_computed(proj, result):
    """第 4 步：按统一口径实时算十年总量（首年段实测 + 其余按首年段折算）。

    绝不能用「近 30 天」之类的表单区间当分母 —— 十年计入期一除就是几十倍稀释。
    多机器时 build_credit_period_segments 会「每台各自分段再相加」。
    """
    try:
        from core.projection import build_credit_period_segments
        seg = build_credit_period_segments(proj)
        result["range_start"] = (seg.get("first_segment") or {}).get("start")
        result["range_end"] = (seg.get("first_segment") or {}).get("end")
        result["first_segment_days"] = seg.get("first_segment_days")
        if seg.get("has_first_segment_data"):
            result.update({
                "ten_year_total": round(float(seg["total_reduction"]), 2),
                "source": "computed",
                "source_label": "按计入期首年段实测值折算",
            })
        else:
            # 不是「算出来是 0」，而是首年段压根没数据 —— 前端要明确提示，别让 0 被当成真实值
            result.update({
                "ten_year_total": None,
                "source": "no_first_segment_data",
                "source_label": "计入期首年段没有监测数据",
            })
    except Exception:
        pass
    return result



@app.route('/api/projects/<int:project_id>/report/ten-year-total', methods=['GET'])
def api_ten_year_total(project_id):
    """返回单个项目的「十年预估减排总量 (tCO₂e)」（逻辑见 _ten_year_total_for）。"""
    return jsonify(_ten_year_total_for(project_id))


def _docx_to_pdf(docx_path):
    """用 LibreOffice 把 docx 转成 pdf（服务器需安装 libreoffice）。
    成功返回 pdf 路径，失败/无 soffice 返回 None。"""
    import subprocess
    import shutil
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf",
             "--outdir", os.path.dirname(docx_path), docx_path],
            timeout=180, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    pdf_path = docx_path.rsplit(".", 1)[0] + ".pdf"
    return pdf_path if os.path.exists(pdf_path) else None


@app.route('/api/projects/<int:project_id>/report/generate-file', methods=['POST'])
@_continuity_local_only
def api_generate_report_file(project_id):
    """
    生成报告并直接返回文件内容（供 VCER 申请材料联动：生成后自动填入文件框）。

    请求体 JSON:
        report_type: "verification" | "pdd"
        template: 模板相对路径（可选）
        start_date / end_date: 日期区间（可选）
        to_pdf: true 时尝试用 LibreOffice 转 pdf（无 soffice 则回退 docx，
                并在响应头 X-Pdf-Converted=0 标明）

    返回:
        文件二进制流；响应头 X-File-Name 为文件名，X-Pdf-Converted 为 1/0。
    """
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    data = request.get_json(force=True, silent=True) or {}
    report_type = data.get("report_type", "verification")
    start_date = data.get("start_date")
    end_date = data.get("end_date")
    template_file = data.get("template")
    to_pdf = bool(data.get("to_pdf"))

    gate_err = _check_report_gate(proj, report_type)
    if gate_err:
        return jsonify({"status": "失败", "msg": gate_err})

    try:
        result = ReportGenerator.generate_word_report(
            proj, report_type, start_date=start_date, end_date=end_date,
            template_file=template_file, continuity_ack=data.get("continuity_ack")
        )
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})

    if result.get("status")=="需要确认": return jsonify(result),409
    if not (result.get("status") == "成功" and result.get("file_path")):
        return jsonify({"status": "失败", "msg": result.get("msg", "生成失败")})

    file_path = result["file_path"]
    pdf_converted = "0"
    if to_pdf:
        pdf_path = _docx_to_pdf(file_path)
        if pdf_path:
            file_path = pdf_path
            pdf_converted = "1"

    filename = os.path.basename(file_path)
    from urllib.parse import quote
    resp = send_file(file_path, as_attachment=True, download_name=filename)
    resp.headers["X-File-Name"] = quote(filename)
    resp.headers["X-Pdf-Converted"] = pdf_converted
    resp.headers["Access-Control-Expose-Headers"] = "X-File-Name, X-Pdf-Converted"
    return resp


@app.route('/api/projects/<int:project_id>/monitoring/import', methods=['POST'])
def api_import_monitoring(project_id):
    """
    批量导入历史监测数据（source='import'），用于老项目历史数据补录。

    请求体 JSON:
        {"machine_id": 1,                            # 可选，缺省=项目主机器
         "rows": [{"timestamp": "YYYY-MM-DD[ HH:MM[:SS]]",
                    "carbon_reduction": 0.12,        # 可选，缺省 0
                    "breakdown": {"delta_power_kwh": 200, ...}}]}

    规则:
    - 同 machine_id + timestamp + source='import' 已存在的行跳过（防重复导入）
    - cumulative_reduction 按 timestamp 升序累加，起点 = 该**机器**早于本批
      最早时间的所有记录减排量之和
    - 若该项目没有实时采集数据（非 import 来源），导入后同步更新
      carbon_projects.lifetime_reduction 为各机器全历史累计值之和；待核证另按已通过核查区间计算
    """
    from core.data_import import normalize_timestamp
    import math
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"status": "失败",
                        "msg": "请求体需为对象，形如 {\"machine_id\": 1, \"rows\": [...]}"})
    try:
        machine_id = _resolve_machine_id(project_id, data.get("machine_id"))
    except ValueError as e:
        return jsonify({"status": "失败", "msg": str(e)})
    rows = data.get("rows") or []
    if not isinstance(rows, list):
        return jsonify({"status": "失败", "msg": "rows 必须是数据行数组"}), 400
    if not rows:
        return jsonify({"status": "失败", "msg": "没有可导入的数据行"})
    if len(rows) > 50000:
        return jsonify({"status": "失败", "msg": "单次最多导入 50000 行，请分批"})

    # ── 规范化 ──
    norm = []
    errors = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            errors.append(f"第{i + 1}行不是有效对象")
            continue
        ts = normalize_timestamp(r.get("timestamp"))
        if not ts:
            errors.append(f"第{i + 1}行时间无效: {r.get('timestamp') or '(空)'}")
            continue
        try:
            cr = float(r.get("carbon_reduction") or 0)
            if not math.isfinite(cr):
                raise ValueError("非有限数字")
        except (TypeError, ValueError):
            errors.append(f"第{i + 1}行减排量必须为有限数字")
            continue
        bd = r.get("breakdown") if isinstance(r.get("breakdown"), dict) else {}
        clean_bd = {}
        for k, v in bd.items():
            if isinstance(v, (int, float)):
                if math.isfinite(v):
                    clean_bd[str(k)] = v
            elif isinstance(v, str):
                try:
                    number = float(v)
                    if math.isfinite(number):
                        clean_bd[str(k)] = number
                except ValueError:
                    pass
        norm.append((ts, cr, clean_bd))

    if not norm:
        return jsonify({"status": "失败", "msg": "没有可导入的有效行", "errors": errors[:20]})

    norm.sort(key=lambda x: x[0])

    from core.project_manager import get_db
    conn = get_db()
    imported = 0
    skipped = 0
    try:
        # 去重与累计起点都**按机器**：两台机器的累计读数是两套
        existing = {
            row[0] for row in conn.execute(
                "SELECT timestamp FROM monitoring_data WHERE machine_id = ? AND source = 'import'",
                (machine_id,)
            ).fetchall()
        }
        # 累计起点：该机器早于本批最早时间的所有记录减排量之和
        base_row = conn.execute(
            "SELECT COALESCE(SUM(carbon_reduction), 0) AS s FROM monitoring_data "
            "WHERE machine_id = ? AND timestamp < ?",
            (machine_id, norm[0][0])
        ).fetchone()
        cum = base_row["s"] if base_row else 0

        for ts, cr, bd in norm:
            if ts in existing:
                skipped += 1
                continue
            cum += cr
            conn.execute(
                """INSERT INTO monitoring_data
                   (project_id, machine_id, timestamp, raw_data, carbon_reduction,
                    cumulative_reduction, breakdown, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'import')""",
                (project_id, machine_id, ts,
                 json.dumps(bd, ensure_ascii=False),
                 cr, round(cum, 6),
                 json.dumps(bd, ensure_ascii=False))
            )
            existing.add(ts)
            imported += 1

        # 若项目没有实时采集数据，用导入数据更新累计核证减排量显示
        # （口径同采集：该项目各机器累计值之和）
        from core.reduction_ledger import refresh_totals
        refresh_totals(conn, project_id)
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "失败", "msg": str(e), "errors": errors[:20]})
    finally:
        conn.close()

    if imported:
        from core.reduction_ledger import reconcile_machine
        reconcile_machine(machine_id, apply=True)
    return jsonify({
        "status": "成功",
        "machine_id": machine_id,
        "imported": imported,
        "skipped": skipped,
        "errors": errors[:20],
    })


@app.route('/api/projects/<int:project_id>/monitoring/import-meta', methods=['GET'])
def api_import_meta(project_id):
    """返回导入所需元信息：公式原始字段、中文名、API 推送端点。"""
    from core import data_import
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})
    try:
        raw_fields = data_import.get_required_raw_fields(proj)
        labels = {f: data_import.RAW_FIELD_LABELS.get(f, f) for f in raw_fields}
        # 单位一并给出：导入历史数据最容易搞错的就是 kWh/MWh、m³/L 这类
        units = {f: data_import.RAW_FIELD_UNITS.get(f, "") for f in raw_fields}
        units["timestamp"] = data_import.RAW_FIELD_UNITS.get("timestamp", "")
        units["carbon_reduction"] = data_import.RAW_FIELD_UNITS.get("carbon_reduction", "")
        return jsonify({
            "status": "成功",
            "hook_name": proj.get("hook_name"),
            "raw_fields": raw_fields,
            "labels": labels,
            "units": units,
            "endpoint": f"/api/projects/{project_id}/monitoring/ingest",
        })
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/projects/<int:project_id>/monitoring/import-template', methods=['GET'])
def api_import_template(project_id):
    """按项目公式生成 CSV/Excel 导入模板。"""
    import io
    import csv as _csv
    from core import data_import
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})
    fmt = (request.args.get('format') or 'csv').lower()
    try:
        template_rows, _ = data_import.build_template_rows(proj)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})

    if fmt == 'xlsx':
        import tempfile
        from core.xlsx_io import write_xlsx
        fd, tmp_path = tempfile.mkstemp(suffix='.xlsx')
        os.close(fd)
        try:
            write_xlsx(template_rows, tmp_path)
            with open(tmp_path, 'rb') as f:
                content = f.read()
        finally:
            os.unlink(tmp_path)
        return send_file(
            io.BytesIO(content),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True, download_name='历史数据导入模板.xlsx')
    else:
        buf = io.StringIO()
        _csv.writer(buf).writerows(template_rows)
        content = ('﻿' + buf.getvalue()).encode('utf-8')
        return send_file(
            io.BytesIO(content), mimetype='text/csv',
            as_attachment=True, download_name='历史数据导入模板.csv')


@app.route('/api/projects/<int:project_id>/monitoring/import-file', methods=['POST'])
def api_import_file(project_id):
    """上传 CSV/Excel 文件导入历史数据。"""
    import io
    import csv as _csv
    from core import data_import
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({"status": "失败", "msg": "请选择文件"})

    # 导入到哪台机器：表单字段 machine_id，缺省落项目主机器
    try:
        machine = data_import.resolve_machine(proj, request.form.get('machine_id'))
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})

    try:
        formula, raw_fields = data_import.get_formula(machine, proj.get("hook_name", ""))
        fname = (file.filename or '').lower()

        if fname.endswith('.xlsx'):
            import tempfile
            from core.xlsx_io import read_xlsx
            fd, tmp_path = tempfile.mkstemp(suffix='.xlsx')
            os.close(fd)
            try:
                file.save(tmp_path)
                table = read_xlsx(tmp_path)
            finally:
                os.unlink(tmp_path)
        else:
            raw = file.read()
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('gbk', errors='replace')
            table = list(_csv.reader(io.StringIO(text)))

        if not table or len(table) < 2:
            return jsonify({"status": "失败", "msg": "文件至少需要表头 + 1 行数据"})
        header_row = [str(h).strip() for h in table[0]]
        data_rows = table[1:]
        rows, parse_errors = data_import.parse_table(header_row, data_rows, raw_fields)
        if not rows:
            return jsonify({"status": "失败", "msg": "没有可导入的有效行", "errors": parse_errors[:20]})
        imported, skipped = data_import.import_rows(proj, rows, source="import",
                                                    machine_id=machine["id"])
        return jsonify({
            "status": "成功",
            "machine_id": machine["id"],
            "imported": imported,
            "skipped": skipped,
            "errors": parse_errors[:20],
        })
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/projects/<int:project_id>/monitoring/ingest', methods=['POST'])
def api_ingest(project_id):
    """API 推送导入：外部系统 POST JSON 数据（走公式或直填）。"""
    from core import data_import
    proj = project_manager.get_project(project_id)
    if not proj:
        return jsonify({"status": "失败", "msg": "项目不存在"})

    data = request.get_json(force=True, silent=True) or {}
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        items = data["rows"]
    elif isinstance(data, dict):
        items = [data]
    elif isinstance(data, list):
        items = data
    else:
        return jsonify({"status": "失败", "msg": "请求体需为 rows 数组、单条对象或对象数组"})

    if not items:
        return jsonify({"status": "失败", "msg": "没有可导入的记录"})

    # 落到哪台机器：请求体的 machine_id，缺省=项目主机器。
    # ⚠️ 请求体可以是**裸数组**（老接口支持），那种形式没地方挂 machine_id，
    #    所以这里必须先判 dict 再取，否则数组请求体会直接 AttributeError。
    body_machine_id = data.get("machine_id") if isinstance(data, dict) else None
    try:
        machine = data_import.resolve_machine(proj, body_machine_id)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})

    try:
        formula, raw_fields = data_import.get_formula(machine, proj.get("hook_name", ""))
        rows = []
        errors = []
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                errors.append(f"第{i + 1}条不是对象")
                continue
            ts = data_import.normalize_timestamp(it.get("timestamp"))
            if not ts:
                errors.append(f"第{i + 1}条时间格式无效: {it.get('timestamp')}")
                continue
            rec = {"timestamp": ts}
            for f in raw_fields:
                rec[f] = it.get(f, "")
            if it.get("carbon_reduction") not in (None, ""):
                rec["carbon_reduction"] = it["carbon_reduction"]
            rows.append(rec)

        if not rows:
            return jsonify({"status": "失败", "msg": "没有有效记录", "errors": errors[:20]})
        imported, skipped = data_import.import_rows(proj, rows, source="api",
                                                    machine_id=machine["id"])
        return jsonify({
            "status": "成功",
            "machine_id": machine["id"],
            "imported": imported,
            "skipped": skipped,
            "errors": errors[:20],
        })
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/projects/<int:project_id>/reserve-code', methods=['POST'])
def api_reserve_code(project_id):
    """为项目预分配 VCER 编号（先预订后转正，解决 PDD 需要编号的死循环）"""
    try:
        from core.code_reservation import reserve_code
        return jsonify(reserve_code(project_id))
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/projects/<int:project_id>/void-code', methods=['POST'])
def api_void_code(project_id):
    """作废项目的预分配编号（留痕不递补；已转正不可作废）"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from core.code_reservation import void_code
        return jsonify(void_code(project_id, reason=data.get("reason", "")))
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/code-reservations', methods=['GET'])
def api_list_code_reservations():
    """列出全部编号预留记录"""
    try:
        from core.code_reservation import list_reservations
        return jsonify({"status": "成功", "reservations": list_reservations()})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/projects/sync-vcer-codes', methods=['POST'])
def api_sync_vcer_codes():
    """
    VCER 编号自动回填：拉取平台已注册项目，按名称模糊匹配写回本地 project_code。

    原理：VCER 平台接口是编号唯一权威源，本地只做镜像匹配；
    已有编号的项目不动，仅回填空缺。匹配规则为规范化名称相等或互相包含。

    返回:
        {"status": "成功", "matched": [{id, name, code}],
         "unmatched_local": [{id, name}], "remote_count": N}
    """
    import re as _re
    cfg = project_manager.get_enterprise_config() or {}
    enterprise_id = (cfg.get("enterprise_id") or "").strip()
    if not enterprise_id:
        return jsonify({"status": "失败", "msg": "请先在「设置」中填写企业序号"})

    try:
        import config as app_config
        from core.vcer_client import VCERClient
        client = VCERClient(cfg.get("vcer_api_url") or app_config.VCER_API_URL,
                            cfg.get("api_access_key"))
        data = client.get_carbon_projects(enterprise_id)
    except Exception as e:
        return jsonify({"status": "失败", "msg": f"调用平台接口失败: {e}"})

    remote = []
    if isinstance(data, dict):
        remote = data.get("projects") or data.get("project") or data.get("data") or []
    elif isinstance(data, list):
        remote = data
    if not remote:
        return jsonify({"status": "失败",
                        "msg": "平台未返回项目列表: " + str(data)[:200]})

    def _norm(s):
        return _re.sub(r"[\s\-_（）()【】\[\]项目]+", "", str(s or "")).lower()

    projects = project_manager.list_projects()
    matched = []
    unmatched_local = []
    used_codes = set()

    from core.project_manager import get_db
    conn = get_db()
    try:
        for p in projects:
            if p.get("lifecycle_status") != "active":
                continue
            if (p.get("project_code") or "").strip():
                continue  # 已有编号不动
            pn = _norm(p.get("project_name"))
            if not pn:
                continue
            best = None
            for r in remote:
                if not isinstance(r, dict):
                    continue
                code = (r.get("project_code") or "").strip()
                if not code or code in used_codes:
                    continue
                rn = _norm(r.get("project_name") or r.get("name"))
                if rn and (pn == rn or pn in rn or rn in pn):
                    best = r
                    break
            if best:
                code = best["project_code"].strip()
                conn.execute(
                    "UPDATE carbon_projects SET project_code = ?, updated_at = ? WHERE id = ?",
                    (code, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), p["id"])
                )
                used_codes.add(code)
                matched.append({"id": p["id"], "name": p.get("project_name", ""), "code": code})
            else:
                unmatched_local.append({"id": p["id"], "name": p.get("project_name", "")})
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "失败", "msg": str(e)})
    finally:
        conn.close()

    return jsonify({
        "status": "成功",
        "matched": matched,
        "unmatched_local": unmatched_local,
        "remote_count": len(remote),
    })


@app.route('/api/reports/batch-download/<filename>')
def api_batch_download(filename):
    """下载批量生成的报告 zip 包"""
    safe_name = os.path.basename(filename)
    if not safe_name.endswith(".zip"):
        return jsonify({"status": "失败", "msg": "非法文件"})
    return send_from_directory(ReportGenerator.OUTPUT_DIR, safe_name, as_attachment=True)


@app.route('/api/reports', methods=['GET'])
def api_list_reports():
    """列出报告历史"""
    project_id = request.args.get("project_id", type=int)
    from core.project_manager import get_db
    conn = get_db()
    try:
        if project_id:
            rows = conn.execute(
                "SELECT * FROM reports WHERE project_id = ? ORDER BY generated_at DESC",
                (project_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM reports ORDER BY generated_at DESC"
            ).fetchall()
        reports = []
        for row in rows:
            report = dict(row)
            path = config.resolve_data_path(report.get("file_path") or "")
            report["file_available"] = bool(report.get("file_path") and os.path.isfile(path))
            # 历史列表只展示仍可下载的原件；保留数据库登记以便原件恢复后重新出现。
            if not report["file_available"]:
                continue
            reports.append(report)
        return jsonify({"status": "成功", "reports": reports})
    finally:
        conn.close()


@app.route('/api/reports/<int:report_id>/download', methods=['GET'])
def api_download_report(report_id):
    """下载已登记的报告原文件；缺失时返回明确的 404。"""
    from core.project_manager import get_db
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        if not row:
            return jsonify({"status": "失败", "code": "REPORT_NOT_FOUND", "msg": "报告登记不存在"}), 404

        file_path = config.resolve_data_path(row["file_path"] or "")
        if not row["file_path"] or not os.path.isfile(file_path):
            return jsonify({"status": "失败", "code": "REPORT_FILE_MISSING",
                            "msg": "原报告文件缺失，历史登记仍保留；请从原部署或备份恢复原件。"}), 404

        directory = os.path.dirname(file_path)
        filename = os.path.basename(file_path)
        return send_from_directory(directory, filename, as_attachment=True,
                                   download_name=filename)
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# 报告占位符 & 模板管理 API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/reports/formats', methods=['GET'])
def api_list_report_formats():
    """
    列出报告类型（固定两类：核查报告 / 项目设计文档 PDD）。

    返回:
        {"status": "成功", "formats": [{"key": "verification", "label": "核查报告"}, ...]}
    """
    try:
        from core.reports import get_report_types
        return jsonify({"status": "成功", "formats": get_report_types()})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/reports/template-keys', methods=['GET'])
def api_template_keys():
    """解析模板内的占位符，返回按命名空间分类的 key（供预设管理高亮模板用到的字段）。

    参数:
        template: 单个模板相对路径（可选）
        all: 传 "1" 时返回该报告类型下所有模板的 {rel_path: {md5, keys}} 映射，
             供前端异步识别后按 md5 缓存。
        report_type: 配合 all 使用
    """
    from core.report_generator import ReportGenerator
    from core.reports import resolve_template_file, list_templates
    from core.reports.template_keys_cache import get_keys, file_md5, prune
    try:
        if request.args.get("all") == "1":
            report_type = request.args.get("report_type")
            templates = list_templates(report_type=report_type)
            result = {}
            seen_md5 = []
            for t in templates:
                try:
                    path = t["template_path"]
                    md5 = file_md5(path)
                    if md5:
                        seen_md5.append(md5)
                    keys = get_keys(path, t["rel_path"], ReportGenerator._extract_all_placeholders)
                    result[t["rel_path"]] = {"md5": md5, "keys": keys}
                except Exception:
                    result[t["rel_path"]] = {"md5": "", "keys": {}}
            # 顺手清掉已经不在模板目录里的条目
            prune(seen_md5)
            return jsonify({"status": "成功", "templates": result})

        template = request.args.get("template", "")
        path = resolve_template_file(template)
        if not path:
            return jsonify({"status": "失败", "msg": "模板不存在"})
        keys = get_keys(path, template, ReportGenerator._extract_all_placeholders)
        return jsonify({"status": "成功", "keys": keys, "md5": file_md5(path)})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/logs', methods=['GET'])
def api_list_logs():
    """列出系统日志（「日志」页签数据源）。

    参数:
        level: all（默认，全部）/ warning（warning 及以上）/ error（error 及以上）
        project_id: 只看某个项目的日志（可选）
        limit: 每页条数，默认 200，上限 1000
        before_id: 键集分页游标，取 id 小于它的（即更早的）。不传则从最新开始。
    """
    level = (request.args.get("level") or "all").lower()
    if level not in ("all", "warning", "error"):
        level = "all"
    limit = min(max(request.args.get("limit", 200, type=int) or 200, 1), 1000)
    before_id = request.args.get("before_id", type=int)
    project_id = request.args.get("project_id", type=int)
    try:
        from core.app_log import query_logs, list_log_projects
        logs, total = query_logs(level=level, limit=limit,
                                 before_id=before_id, project_id=project_id)
        return jsonify({"status": "成功", "logs": logs, "total": total,
                        "level": level, "limit": limit,
                        "project_id": project_id,
                        "projects": (list_log_projects() if before_id is None else []),
                        # 下一页的游标 = 本页最后一条的 id
                        "next_before_id": (logs[-1]["id"] if logs else None),
                        "has_more": len(logs) == limit})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.errorhandler(Exception)
def handle_uncaught(e):
    """未捕获异常记一条 ERROR 再交回 Flask 默认处理。"""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    try:
        logging.getLogger("uncaught").error("未捕获异常: %s", e, exc_info=True)
    except Exception:
        pass
    return jsonify({"status": "失败", "msg": f"服务器内部错误: {e}"}), 500


@app.route('/api/reports/templates', methods=['GET'])
def api_list_templates():
    """
    列出指定报告类型下可用的模板文件（自动发现 templates/reports/docx/ 目录）。

    参数:
        report_type: 可选，报告类型 key。为空时返回全部模板。

    返回:
        {"status": "成功", "templates": [{"file": "verification.docx", "label": "verification.docx", "rel_path": "verification.docx"}, ...]}
    """
    try:
        from core.reports import list_templates
        report_type = request.args.get("report_type")
        templates = list_templates(report_type=report_type)
        result = [{"file": t["file"], "label": t["label"], "rel_path": t["rel_path"]}
                  for t in templates]
        return jsonify({"status": "成功", "templates": result})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})
@app.route('/api/reports/variables', methods=['GET'])
def api_list_variables():
    """
    获取报告模板可用的字段，分为「项目中自有字段」和「自定义字段（预设）」。

    参数:
        project_id: 可选，指定项目以获取该项目实际字段值。

    返回:
        {"status": "成功", "project_fields": [...], "custom_fields": [...], "controls": [...]}
    """
    project_id = request.args.get("project_id", type=int)
    try:
        from core.reports.variable_reference import get_available_fields
        ref = get_available_fields(project_id)
        return jsonify({
            "status": "成功",
            "project_fields": ref.get("project_fields", []),
            "custom_fields": ref.get("custom_fields", []),
            "computed_vars": ref.get("computed_vars", []),
            "all_objects": ref.get("all_objects", []),
            "controls": ref.get("controls", []),
        })
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/reports/variables/export', methods=['GET'])
def api_export_variables():
    """
    导出全部可用字段为 Word 文档（.docx），方便本地查阅模板字段。

    参数:
        project_id: 可选，指定项目则带出该项目实际字段值。
    """
    import io
    from datetime import datetime
    project_id = request.args.get("project_id", type=int)
    try:
        import docx
        from docx.shared import Pt, RGBColor
    except ImportError:
        return jsonify({"status": "失败", "msg": "服务器缺少 python-docx 依赖"}), 500

    try:
        from core.reports.variable_reference import get_available_fields
        ref = get_available_fields(project_id)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})

    document = docx.Document()
    # 全局字体：中文微软雅黑
    style = document.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(10)
    try:
        from docx.oxml.ns import qn
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    except Exception:
        pass

    document.add_heading("报告模板可用字段清单", level=0)
    meta = f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    if project_id:
        meta += f"　项目ID：{project_id}（含实际值）"
    else:
        meta += "　（未指定项目，值为空或示例）"
    document.add_paragraph(meta)

    def add_group_table(sec, headers, row_fn):
        label = sec.get("label", sec.get("key", ""))
        hint = sec.get("hint", "")
        document.add_heading(label, level=1)
        if hint:
            p = document.add_paragraph(hint)
            for run in p.runs:
                run.font.size = Pt(9)
                run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
        variables = sec.get("vars") or sec.get("variables") or []
        table = document.add_table(rows=1, cols=len(headers))
        table.style = "Light Grid Accent 1"
        for i, h in enumerate(headers):
            cell = table.rows[0].cells[i]
            cell.text = h
            for run in cell.paragraphs[0].runs:
                run.font.bold = True
        for v in variables:
            row = table.add_row().cells
            for i, val in enumerate(row_fn(v)):
                row[i].text = val
        document.add_paragraph("")

    # ── 所有对象及其值（主清单）──
    for sec in ref.get("all_objects", []):
        add_group_table(
            sec, ["字段路径 / 变量名", "说明", "当前值", "可计算"],
            lambda v: (str(v.get("path", "")), str(v.get("label", "")),
                       str(v.get("value", "") or ""),
                       "🔢" if v.get("computable") else ""),
        )

    # ── 自定义字段可用状态补充表 ──
    for sec in ref.get("custom_fields", []):
        add_group_table(
            sec, ["字段路径", "说明", "当前值", "状态"],
            lambda v: (str(v.get("path", "")), str(v.get("label", "")),
                       str(v.get("value", "") or ""),
                       "可用" if v.get("available") else "未填写"),
        )

    controls = ref.get("controls", [])
    if controls:
        document.add_heading("Jinja2 控制结构", level=1)
        table = document.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        for i, h in enumerate(["语法", "说明", "备注"]):
            cell = table.rows[0].cells[i]
            cell.text = h
            for run in cell.paragraphs[0].runs:
                run.font.bold = True
        for c in controls:
            row = table.add_row().cells
            row[0].text = str(c.get("syntax", ""))
            row[1].text = str(c.get("label", ""))
            row[2].text = str(c.get("description", ""))

    buf = io.BytesIO()
    document.save(buf)
    buf.seek(0)
    filename = f"可用字段清单_{datetime.now().strftime('%Y%m%d_%H%M')}.docx"
    return send_file(
        buf, as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.route('/api/reports/eval-code', methods=['POST'])
def api_eval_code():
    """
    网页内 Python 控制台：执行一段代码（函数体语义，可 return），返回结果与类型。
    用于模板编写时探查变量（如 type(report.end_date)）。

    请求体 JSON:
        code: 代码文本
        project_id: 可选，指定项目后注入该项目完整报告 context（默认上一年全年日期）
    """
    data = request.get_json(force=True, silent=True) or {}
    code = data.get("code", "")
    project_id = data.get("project_id")

    env = {}
    if project_id:
        try:
            from datetime import datetime
            proj = project_manager.get_project(int(project_id))
            if proj:
                from core.reports.context_builder import ReportContextBuilder
                now = datetime.now()
                last_year = now.year - 1
                ctx = ReportContextBuilder.build(
                    proj, "verification",
                    start_date=f"{last_year}-01-01", end_date=f"{last_year}-12-31")
                env = ReportContextBuilder._build_code_env(ctx, _parse_hook_params(proj))
        except Exception as e:
            return jsonify({"status": "失败", "msg": f"构建执行环境失败: {e}"})

    from core.code_executor import run
    result, error = run(code, env)
    if error:
        return jsonify({"status": "成功", "result": "", "type": "Error", "error": error})
    return jsonify({
        "status": "成功",
        "result": repr(result),
        "type": type(result).__name__,
        "error": None,
    })


def _parse_hook_params(project):
    """解析项目 hook_params 为 dict（多机器改造后取自项目**主机器**）。

    统一走 ReportContextBuilder._parse_hook_params，别在这里写第二套 ——
    否则控制台执行环境与报告渲染会拿到两套参数。
    """
    try:
        from core.reports.context_builder import ReportContextBuilder
        return ReportContextBuilder._parse_hook_params(project or {})
    except Exception:
        return {}


@app.route('/api/reports/custom-placeholders', methods=['GET'])
def api_list_custom_placeholders():
    """列出所有自定义占位符"""
    try:
        from core.custom_placeholders import load_custom_placeholders
        placeholders = load_custom_placeholders()
        return jsonify({"status": "成功", "placeholders": placeholders})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/reports/custom-placeholders', methods=['POST'])
def api_create_custom_placeholder():
    """创建自定义占位符"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from core.custom_placeholders import add_custom_placeholder
        result = add_custom_placeholder(
            name=data.get("name", "").strip(),
            label=data.get("label", "").strip(),
            value=data.get("value", ""),
            description=data.get("description", "").strip(),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/reports/custom-placeholders/<name>', methods=['PUT'])
def api_update_custom_placeholder(name):
    """修改自定义占位符"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from core.custom_placeholders import update_custom_placeholder
        result = update_custom_placeholder(name, data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/reports/custom-placeholders/<name>', methods=['DELETE'])
def api_delete_custom_placeholder(name):
    """删除自定义占位符"""
    try:
        from core.custom_placeholders import delete_custom_placeholder
        result = delete_custom_placeholder(name)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


# ── 企业预设信息管理 ──

@app.route('/api/presets', methods=['GET'])
def api_list_presets():
    """列出所有预设"""
    try:
        from core.presets import load_presets, CATEGORIES
        presets = load_presets()
        return jsonify({
            "status": "成功",
            "presets": presets,
            "categories": CATEGORIES,
        })
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/presets', methods=['POST'])
def api_create_preset():
    """创建预设"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from core.presets import add_preset
        result = add_preset(
            key=data.get("key", "").strip(),
            label=data.get("label", "").strip(),
            preset_type=data.get("type", "text").strip(),
            value=data.get("value", ""),
            category=data.get("category", "自定义").strip(),
            sort_order=data.get("sort_order"),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/presets/<key>', methods=['PUT'])
def api_update_preset(key):
    """更新预设"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from core.presets import update_preset
        result = update_preset(key, data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/presets/<key>', methods=['DELETE'])
def api_delete_preset(key):
    """删除预设"""
    try:
        from core.presets import delete_preset
        result = delete_preset(key)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/presets/upload-image', methods=['POST'])
def api_upload_preset_image():
    """上传预设图片"""
    try:
        if 'file' not in request.files:
            return jsonify({"status": "失败", "msg": "未选择文件"})
        file = request.files['file']
        if file.filename == '':
            return jsonify({"status": "失败", "msg": "文件名为空"})

        from core.presets import save_preset_image
        preset_key = request.form.get("preset_key", "img").strip()
        rel_path = save_preset_image(file, file.filename, preset_key)

        # 构建可访问的 URL
        import os
        filename = os.path.basename(rel_path)
        image_url = f"/api/presets/images/{filename}"

        return jsonify({
            "status": "成功",
            "path": rel_path,
            "url": image_url,
            "filename": filename,
        })
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/presets/images/<filename>')
def api_serve_preset_image(filename):
    """提供预设图片访问"""
    import os
    from flask import send_from_directory
    from core.presets import get_images_dir
    # 安全检查：防止路径穿越
    safe_name = os.path.basename(filename)
    return send_from_directory(get_images_dir(), safe_name)


# ═══════════════════════════════════════════════════════════════
# 项目预设 API（每个项目独立的模板变量）
# ═══════════════════════════════════════════════════════════════

@app.route('/api/project-presets/global', methods=['GET'])
def api_list_global_presets():
    """列出全项目通用预设（scope=global，值对所有项目相同）"""
    try:
        from core.project_presets import load_global_presets, CATEGORIES
        return jsonify({
            "status": "成功",
            "presets": load_global_presets(),
            "categories": CATEGORIES,
        })
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/presets/batch-export', methods=['POST'])
def api_presets_batch_export():
    """批量导出预设为 Excel（行=字段，列=项目）"""
    data = request.get_json(force=True, silent=True) or {}
    project_ids = data.get("project_ids") or []
    if not project_ids:
        return jsonify({"status": "失败", "msg": "请先选择项目"})
    try:
        from core.preset_batch import build_export
        from core.xlsx_io import write_xlsx
        rows, errors = build_export(project_ids)
        if not errors and not rows:
            return jsonify({"status": "失败", "msg": "没有可导出的内容"})
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        write_xlsx(rows, tmp)
        with open(tmp, "rb") as f:
            content = f.read()
        os.remove(tmp)
        from flask import Response
        filename = f"预设批量编辑_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        from urllib.parse import quote
        resp = Response(content,
                        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
        return resp
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/presets/batch-import', methods=['POST'])
def api_presets_batch_import():
    """批量导入 Excel 回写预设值（multipart 上传 .xlsx + project_ids）"""
    from core.xlsx_io import read_xlsx
    try:
        if 'file' not in request.files:
            return jsonify({"status": "失败", "msg": "未选择文件"})
        f = request.files['file']
        if not f.filename.endswith('.xlsx'):
            return jsonify({"status": "失败", "msg": "仅支持 .xlsx 文件"})
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        f.save(tmp)
        grid = read_xlsx(tmp)
        os.remove(tmp)

        project_ids = request.form.get("project_ids", "")
        ids = [int(x) for x in project_ids.split(",") if x.strip().isdigit()]
        if not ids:
            return jsonify({"status": "失败", "msg": "缺少项目列表"})

        from core.preset_batch import apply_import
        result = apply_import(ids, grid)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


# ── 预设矩阵（报告中心批量模式内嵌表格用）──
# 复用 Excel 批量编辑那套 build_export / apply_import，只是把「文件」换成 JSON。
# ⚠️ apply_import 是**按列位置**对齐项目的（校验 len(header)-1 == len(project_ids)），
# 所以前端必须用同一份、同顺序的 id 列表来取和存，中途不能重排或删项目。

@app.route('/api/presets/matrix', methods=['POST'])
def api_presets_matrix():
    """返回「字段 × 项目」矩阵，供批量模式内嵌表格渲染。

    请求体: {"project_ids": [1,2,3]}
    返回:   {"status": "成功", "rows": [[字段, 项目1值, 项目2值, ...], ...],
             "project_names": [...], "errors": [...]}
    """
    data = request.get_json(force=True, silent=True) or {}
    project_ids = data.get("project_ids") or []
    if not project_ids:
        return jsonify({"status": "失败", "msg": "请先选择项目"})
    if len(project_ids) > 50:
        return jsonify({"status": "失败", "msg": "单次最多 50 个项目"})
    try:
        from core.preset_batch import build_export
        rows, errors = build_export(project_ids)
        names = rows[0][1:] if rows else []

        # 每行的类型信息：内嵌表格里**只让 text 类型可编辑**。
        # code / table / richtext 的值是多行的，塞进单行 <input> 的 value 属性
        # 会在换行处截断，保存回去就把预设写坏了 —— 这些行前端渲染成只读。
        import re as _re
        types = _preset_key_types(project_ids)
        row_meta = []
        for r in rows:
            label = str(r[0] if r else "")
            key = None
            for m in _re.finditer(r'\((\w+)\)', label):
                key = m.group(1)
            ptype = types.get(key)
            row_meta.append({
                "key": key,
                "type": ptype,
                "editable": ptype == "text",
                # 分节标题/说明行（"== xxx ==" 或 "（说明）…"）整行不可编辑
                "is_section": label.startswith("==") or label.startswith("（"),
            })
        return jsonify({"status": "成功", "rows": rows, "row_meta": row_meta,
                        "project_names": names, "errors": errors})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


def _preset_key_types(project_ids):
    """{预设 key: 类型}，覆盖 global + shared + 各项目独有。"""
    types = {}
    try:
        from core.project_presets import (load_global_presets, load_shared_presets,
                                          load_all_project_presets)
        for p in load_global_presets():
            types.setdefault(p.get("key"), p.get("type"))
        for p in load_shared_presets():
            types.setdefault(p.get("key"), p.get("type"))
        for pid in project_ids:
            try:
                for p in load_all_project_presets(int(pid)):
                    types.setdefault(p.get("key"), p.get("type"))
            except (TypeError, ValueError):
                continue
    except Exception:
        pass
    return types


@app.route('/api/presets/matrix-save', methods=['POST'])
def api_presets_matrix_save():
    """把（用户改过的）矩阵写回各项目。

    请求体: {"project_ids": [1,2,3], "grid": [[字段, v1, v2, ...], ...]}
    返回:   apply_import 的结果 {"status", "imported", "updated", "errors"}
    """
    data = request.get_json(force=True, silent=True) or {}
    project_ids = data.get("project_ids") or []
    grid = data.get("grid") or []
    if not project_ids:
        return jsonify({"status": "失败", "msg": "请先选择项目"})
    if not grid:
        return jsonify({"status": "失败", "msg": "没有要保存的内容"})
    try:
        from core.preset_batch import apply_import
        return jsonify(apply_import(project_ids, grid))
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/reports/ten-year-totals', methods=['POST'])
def api_ten_year_totals():
    """批量取多个项目的十年预估减排总量（报告中心批量模式用）。

    请求体: {"project_ids": [1,2,3]}
    返回:   {"status": "成功", "items": [{...与单项目接口同款字段}, ...]}

    服务端循环一次，省得浏览器发 N 个请求各开一个连接。
    """
    data = request.get_json(force=True, silent=True) or {}
    project_ids = data.get("project_ids") or []
    if not project_ids:
        return jsonify({"status": "失败", "msg": "请先选择项目"})
    if len(project_ids) > 50:
        return jsonify({"status": "失败", "msg": "单次最多 50 个项目"})

    items = []
    for pid in project_ids:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        row = dict(_ten_year_total_for(pid) or {})
        row["id"] = pid
        items.append(row)
    return jsonify({"status": "成功", "items": items})


@app.route('/api/project-presets/global', methods=['POST'])
def api_create_global_preset():
    """创建全项目通用预设（project_id 无关，传 0 占位）"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from core.project_presets import add_project_preset
        result = add_project_preset(
            0,
            key=data.get("key", "").strip(),
            label=data.get("label", "").strip(),
            preset_type=data.get("type", "text").strip(),
            value=data.get("value", ""),
            category=data.get("category", "自定义").strip(),
            sort_order=data.get("sort_order"),
            scope="global",
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/project-presets/global/<key>', methods=['PUT'])
def api_update_global_preset(key):
    """更新全项目通用预设"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from core.project_presets import update_project_preset
        result = update_project_preset(0, key, data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/project-presets/global/<key>', methods=['DELETE'])
def api_delete_global_preset(key):
    """删除全项目通用预设"""
    try:
        from core.project_presets import delete_project_preset
        result = delete_project_preset(0, key, scope="global")
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/projects/<int:project_id>/presets', methods=['GET'])
def api_list_project_presets(project_id):
    """列出项目的所有预设（全项目通用 + 项目通用字段 + 项目独有，合并视图，带 scope 标记）"""
    try:
        from core.project_presets import load_all_project_presets, CATEGORIES
        presets = load_all_project_presets(project_id)
        return jsonify({
            "status": "成功",
            "presets": presets,
            "categories": CATEGORIES,
        })
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/projects/<int:project_id>/presets', methods=['POST'])
def api_create_project_preset(project_id):
    """创建项目预设（scope=shared 时创建所有项目通用字段）"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from core.project_presets import add_project_preset
        result = add_project_preset(
            project_id,
            key=data.get("key", "").strip(),
            label=data.get("label", "").strip(),
            preset_type=data.get("type", "text").strip(),
            value=data.get("value", ""),
            category=data.get("category", "自定义").strip(),
            sort_order=data.get("sort_order"),
            scope=data.get("scope", "project"),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/projects/<int:project_id>/presets/<key>', methods=['PUT'])
def api_update_project_preset(project_id, key):
    """更新项目预设（自动识别通用/项目独有；data.scope 可转换应用范围）"""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from core.project_presets import update_project_preset
        result = update_project_preset(project_id, key, data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})


@app.route('/api/projects/<int:project_id>/presets/<key>', methods=['DELETE'])
def api_delete_project_preset(project_id, key):
    """删除项目预设（?scope=global 删除全项目通用；?scope=shared 删除通用定义；?scope=project 删除项目独有/清值）"""
    scope = request.args.get("scope")
    if scope not in ("global", "shared", "project"):
        scope = None
    try:
        from core.project_presets import delete_project_preset
        result = delete_project_preset(project_id, key, scope=scope)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})
# ═══════════════════════════════════════════════════════════════
# 告警 API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/alerts/rules', methods=['GET'])
def api_list_alert_rules():
    """列出告警规则"""
    from core.project_manager import get_db
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM alert_rules ORDER BY id").fetchall()
        return jsonify({"status": "成功", "rules": [dict(r) for r in rows]})
    finally:
        conn.close()


@app.route('/api/alerts/rules', methods=['POST'])
def api_create_alert_rule():
    """创建告警规则"""
    data = request.get_json(force=True, silent=True) or {}
    from core.project_manager import get_db
    conn = get_db()
    try:
        cursor = conn.execute(
            """INSERT INTO alert_rules (project_id, metric, operator, threshold, cooldown_minutes)
               VALUES (?, ?, ?, ?, ?)""",
            (
                data.get("project_id"),
                data.get("metric"),
                data.get("operator", "gt"),
                data.get("threshold", 0),
                data.get("cooldown_minutes", 60),
            )
        )
        conn.commit()
        return jsonify({"status": "成功", "id": cursor.lastrowid})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})
    finally:
        conn.close()


@app.route('/api/alerts/rules/<int:rule_id>', methods=['DELETE'])
def api_delete_alert_rule(rule_id):
    """删除告警规则"""
    from core.project_manager import get_db
    conn = get_db()
    try:
        conn.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
        conn.commit()
        return jsonify({"status": "成功"})
    except Exception as e:
        return jsonify({"status": "失败", "msg": str(e)})
    finally:
        conn.close()


@app.route('/api/alerts/log', methods=['GET'])
def api_list_alert_log():
    """列出告警日志"""
    limit = request.args.get("limit", 50, type=int)
    from core.project_manager import get_db
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM alert_log ORDER BY triggered_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return jsonify({"status": "成功", "logs": [dict(r) for r in rows]})
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# 仪表盘 API
# ═══════════════════════════════════════════════════════════════

@app.route('/api/dashboard', methods=['GET'])
def api_dashboard():
    """仪表盘聚合数据"""
    projects = project_manager.list_projects()

    # 汇总碳资产
    total_assets = sum(p.get("total_carbon_assets", 0) or 0 for p in projects)
    total_traded = sum(p.get("traded_amount", 0) or 0 for p in projects)
    # 可用碳资产：list_projects() 已经统一算好（口径见 project_manager.effective_available_net），
    # 这里直接加总即可。**别再写 `net or (total - traded)`** —— 那把「卖完了、可用确实是 0」
    # 当成「没数据」，会给每个卖完的项目凭空加一笔。
    total_available = sum(p.get("available_assets_net", 0) or 0 for p in projects)

    # 项目分类
    applied = [p for p in projects if p.get("project_code")]  # 已申请
    non_applied = [p for p in projects if not p.get("project_code")]  # 未申请

    # 各阶段统计（两态：已通过 / 未通过）
    stage_counts = {}
    for s in range(1, 6):
        passed = sum(1 for p in projects if p.get(f"stage{s}_passed"))
        stage_counts[f"stage{s}"] = {
            "passed": passed,
            "not_passed": len(projects) - passed,
        }

    # 待核证累计减排量（本地计算）
    total_pending = sum(p.get("pending_verification_cumulative") or 0 for p in projects)
    pending_unknown = any(p.get("pending_status") == "unknown" for p in projects)
    pending_stale = any(p.get("pending_status") == "stale" for p in projects)

    return jsonify({
        "status": "成功",
        "summary": {
            "total_projects": len(projects),
            "historical_scope": "含作废项目历史数值",
            "applied_projects": len(applied),
            "non_applied_projects": len(non_applied),
            "total_carbon_assets": total_assets,
            "total_available_assets": total_available,
            "total_traded": total_traded,
            "total_pending_verification": None if pending_unknown else total_pending,
            "pending_status": "unknown" if pending_unknown else ("stale" if pending_stale else "confirmed"),
        },
        "stage_counts": stage_counts,
        "projects": projects,
    })


# ═══════════════════════════════════════════════════════════════
# 测试用模拟数据源 API（用于验证采集器功能）
# ═══════════════════════════════════════════════════════════════

import random as _random

# 模拟传感器累计值（跨请求保持递增）
_sim_state = {
    "cumulative_flow": 5000.0,   # 累计产气量 m³
    "cumulative_power": 200.0,   # 累计耗电量 kWh
}


@app.route('/api/test/sensor_data', methods=['GET'])
def api_test_sensor_data():
    """
    模拟空压站传感器数据接口。

    每次请求返回递增的累计值，模拟真实传感器持续产气、耗电的场景。
    累计产气量每次增加 10~50 m³，累计耗电量每次增加 0.5~2 kWh。

    返回格式与采集器期望完全一致：
    {
        "status": "ok",
        "data": {
            "cumulative_flow": 5123.45,
            "cumulative_power": 205.67,
            "timestamp": "2026-06-11 14:30:00"
        }
    }

    可选参数：
        ?reset=1     重置累计值到初始状态
        ?flow=8000   手动设置累计产气量
        ?power=350   手动设置累计耗电量
    """
    # 处理控制参数
    if request.args.get('reset') == '1':
        _sim_state["cumulative_flow"] = 5000.0
        _sim_state["cumulative_power"] = 200.0
        return jsonify({
            "status": "ok",
            "msg": "模拟传感器已重置",
            "data": {
                "cumulative_flow": _sim_state["cumulative_flow"],
                "cumulative_power": _sim_state["cumulative_power"],
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        })

    # 手动设置值
    manual_flow = request.args.get('flow')
    manual_power = request.args.get('power')
    if manual_flow is not None:
        try:
            _sim_state["cumulative_flow"] = float(manual_flow)
        except ValueError:
            return jsonify({"status": "错误", "msg": "flow 必须是数字"}), 400
    if manual_power is not None:
        try:
            _sim_state["cumulative_power"] = float(manual_power)
        except ValueError:
            return jsonify({"status": "错误", "msg": "power 必须是数字"}), 400

    # 正常请求：递增
    flow_delta = _random.uniform(10, 50)
    power_delta = _random.uniform(0.5, 2.0)

    _sim_state["cumulative_flow"] += flow_delta
    _sim_state["cumulative_power"] += power_delta

    return jsonify({
        "status": "ok",
        "data": {
            "cumulative_flow": round(_sim_state["cumulative_flow"], 2),
            "cumulative_power": round(_sim_state["cumulative_power"], 2),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    })


# ═══════════════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="通用碳账户管理系统")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--debug", action="store_true", default=config.DEBUG)
    args = parser.parse_args()

    config.HOST = args.host
    config.PORT = args.port
    config.DEBUG = args.debug

    init_app()
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
