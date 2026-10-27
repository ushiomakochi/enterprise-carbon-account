"""
通用碳账户管理系统 — 配置文件
企业部署时修改此文件以适配自己的环境。
"""

import os

# ── 基础配置 ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 数据库配置 ──
# 默认使用 SQLite（零依赖），文件存储在 data/ 目录
DATABASE_TYPE = os.getenv("DB_TYPE", "sqlite")  # sqlite | mysql
from enterprise_paths import ENTERPRISE_DIR, DATA_DIR, NORMALIZERS_DIR, PRESET_PACKS_DIR
from enterprise_paths import TEMPLATE_DIR as REPORT_DOCX_TEMPLATE_DIR
DATABASE_PATH = os.path.join(DATA_DIR, "carbon_account.db")


def data_path(*parts):
    """All runtime files follow the selected database, including isolated test copies."""
    return os.path.join(os.path.dirname(DATABASE_PATH), *parts)


def resolve_data_path(value):
    """Read portable data/... paths and legacy absolute paths without rewriting records."""
    if not value:
        return ""
    raw = str(value).replace("\\", "/")
    relative = None
    if raw.startswith("data/"):
        relative = raw[5:]
    else:
        marker = "/enterprise-carbon-account/data/"
        offset = raw.lower().find(marker)
        if offset >= 0:
            relative = raw[offset + len(marker):]
    if relative is not None:
        base = os.path.abspath(data_path())
        candidate = os.path.abspath(os.path.join(base, *relative.split("/")))
        try:
            if os.path.commonpath([base, candidate]) != base:
                return ""
        except ValueError:
            return ""
        return candidate
    return str(value) if os.path.isabs(str(value)) else os.path.join(BASE_DIR, str(value))


def portable_data_path(value):
    """Store database file references relative to data, so the folder can be relocated."""
    base = os.path.abspath(data_path())
    candidate = os.path.abspath(value)
    try:
        if os.path.commonpath([base, candidate]) == base:
            return "data/" + os.path.relpath(candidate, base).replace("\\", "/")
    except ValueError:
        pass
    return str(value)


# MySQL 配置（仅 DATABASE_TYPE=mysql 时使用）
MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "carbon_account"),
}

# ── 促进会 VCER API ──
VCER_API_URL = os.getenv(
    "VCER_API_URL",
    "https://api.environdec.cn"
)

# 碳数据接口的 Bearer 密钥（**一般不用动这里**）。
#
# 密钥的正常维护入口是界面上的「⚙ 设置 → 企业配置 → 促进会 API 密钥」，
# 存 `enterprise_config.api_access_key`，优先级高于本文件。
# 这个环境变量只是留给"用 systemd/容器环境变量部署"的场合做兜底。
#
# 促进会的 /cn/api/carbon_assets、/cn/api/carbon_projects、
# /cn/api/account/enterprise_summary 已启用鉴权，**不配会一律 401**。
# 本系统要读多家企业，必须用**系统级密钥**（enterprise_id=SYS）；
# 生成方式（在促进会那台机器上跑）：python make_carbon_api_key.py（不带参数）
VCER_API_KEY = os.getenv("VCER_API_KEY", "")

# ── 企业信息 ──
ENTERPRISE_NAME = os.getenv("ENTERPRISE_NAME", "我的企业")
ENTERPRISE_ID = os.getenv("ENTERPRISE_ID", "")  # 促进会分配的企业序号

# ── 服务配置 ──
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
SECRET_KEY = os.getenv("SECRET_KEY", os.urandom(24).hex())

# ── 数据采集配置 ──
COLLECTION_INTERVAL_SECONDS = int(os.getenv("COLLECTION_INTERVAL", "60"))  # 默认60秒采集一次

# 监测数据保留天数。**默认 0 = 永不过期**（不要改小）。
#
# 曾经默认是 90 天，采集器每 24 小时会把超期的 monitoring_data 直接删掉 —— 只留一行日志，
# 不写告警、界面上完全看不出来。这对本系统是**会出事故的**：
# monitoring_data 不只是"原始遥测"，它同时是报告的取数来源 ——
#   · core/projection.py 十年逐年预估的「首年段实测值」
#   · core/reduction.py 任意区间的减排量
#   · 各类核查报告的 SUM(carbon_reduction)
# 全是对这张表算出来的。而本系统的计入期是 10 年，90 天就把取数基础删了。
#
# 确实需要控制体积时，用 tools/reset_data.py 按项目**手工**清（它会先自动备份并要你确认），
# 不要靠这个开关。真要开，请先确认「删掉的历史区间将来不需要再出报告」。
DATA_RETENTION_DAYS = int(os.getenv("DATA_RETENTION_DAYS", "0"))

# 断档重基线阈值（小时）。超过这个时长的间隔，新读数**只当新起点**（本期记 0），
# 不跟上一条做差 —— 否则停采期间积累的产量会被一次性记成"本期减排量"。
#
# 为什么必须这么做：累计读数做差只能表达"一个正常采集间隔"。如果中间断了很久
# （项目换核查周期、系统停机、设备离线几周），那条差值实际上等于"断档期间的全部产量"，
# 而这段时间**很可能已经在别处核查过了** —— 直接算进来就是重复计算，
# 还会永久留在累计里（后续每条都对，没人看得出来）。
#
# ⚠️ 触发时系统会**写告警 + 日志**，不是静默丢弃：
# 如果那段确实没被核查过、需要计入，按提示导入那一段数据即可。
#
# 默认 168 小时（7 天）：连续采集时两条之间只有几十秒，正常批次导入之间是 0，
# 都远小于它；而"项目换周期/长期停机"这种空档远大于它。设 0 关闭该保护。
COLLECT_GAP_RESET_HOURS = int(os.getenv("COLLECT_GAP_RESET_HOURS", "168"))

# 断档是否**自动兜底**（1=开，0=关）。**默认为 0**：检测到断档时只记新起点 + 告警，
# 由人在界面上「项目 → ⚠ 断档兜底」看过计算结果后再确认执行。
#
# 为什么默认关：兜底是一次**自动的账面调整**（扣已核查量、补记未核查部分、改项目累计）。
# 算错了会静默写进库、还会进报告，事后只能靠翻告警才发现 —— 改成人工确认，让改动可见可审。
#
# 设成 1 可以回到全自动（采集时就地算完并写库）：覆盖到了已核查期间就扣掉已核查量、
# 只把未核查的部分补记成减排量（见 core/gap_bridge.py）；没覆盖到就整段补记。
# 为什么能这么算：公式对读数差是线性可加的，促进会只审核不另算一套，两边同口径。
# 取不到核查记录时（没配密钥/接口不通）**自动退回"只作新起点"**，绝不会重复计算。
# 每次兜底都会写告警留痕，并给那一行打 bridge 标记，报告里能看出不是原始采集值。
COLLECT_GAP_AUTO_BRIDGE = int(os.getenv("COLLECT_GAP_AUTO_BRIDGE", "0"))

# ── 电网排放因子 ──
# 来源：生态环境部《关于发布2024年电力碳足迹因子数据的公告》
# 单位：tCO₂e/MWh
EF_GRID_DEFAULT = 0.5777

# ── 日志 ──
# 落库到 system_logs 表，供「日志」页签查看。
# LOG_LEVEL 设为 "WARNING" 可只收告警及以上（werkzeug 的每请求 INFO 就不会进库了）。
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# 保留条数**按级别分档**，不是全表一个上限 —— 否则高频 INFO 会把 ERROR 挤掉。
# 曾经就是一个 5000 的总上限，100 个项目 / 5 秒写入时整张表只覆盖约 4 分钟，
# ERROR 根本留不住。
LOG_MAX_ROWS = int(os.getenv("LOG_MAX_ROWS", "5000"))          # DEBUG / INFO 档
LOG_MAX_WARNINGS = int(os.getenv("LOG_MAX_WARNINGS", "5000"))  # WARNING 档
LOG_MAX_ERRORS = int(os.getenv("LOG_MAX_ERRORS", "50000"))     # ERROR / CRITICAL 档

# 清理频率（秒）。每次都要 COUNT + DELETE + commit，而本机是网络盘
# （journal_mode=delete，每次 commit 都要 fsync + 建删 journal），
# 所以别太勤 —— 原来每 2 秒一次，纯属浪费。
LOG_TRIM_INTERVAL_SECONDS = int(os.getenv("LOG_TRIM_INTERVAL", "30"))

# ── 报告配置 ──
REPORT_DIR = data_path("reports")
# REPORT_DOCX_TEMPLATE_DIR comes from the selected private enterprise directory.
REPORT_LOGO_PATH = os.getenv("REPORT_LOGO_PATH", "")  # 企业 Logo 路径（可选）

# ── 钩子目录 ──
HOOKS_DIR = os.path.join(BASE_DIR, "hooks")
