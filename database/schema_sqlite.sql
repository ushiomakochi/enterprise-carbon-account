-- ============================================================
-- 通用碳账户管理系统 — SQLite 建表脚本
-- 首次运行时自动执行，创建所有必要的表结构。
-- ============================================================

-- 企业配置表（单行记录）
CREATE TABLE IF NOT EXISTS enterprise_config (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    enterprise_name   TEXT    NOT NULL DEFAULT '我的企业',
    enterprise_id     TEXT    DEFAULT '',           -- 促进会分配的企业序号
    vcer_api_url      TEXT    NOT NULL DEFAULT 'http://api.environdec.cn',
    api_access_key    TEXT    DEFAULT NULL,         -- API 访问密钥（未来使用）
    ef_grid_override  REAL    DEFAULT NULL,         -- 自定义电网排放因子 tCO₂/MWh（NULL=使用默认值0.5777）
    report_logo_path  TEXT    DEFAULT NULL,         -- 报告 Logo 文件路径
    presets           TEXT    DEFAULT '[]',          -- JSON：企业预设信息 [{key, label, type, value, category, sort_order}]
    dev_mode          INTEGER DEFAULT 0,             -- 开发者选项：1=解禁阶段门禁（测试用）
    history_api_base  TEXT    DEFAULT '',            -- 历史 API 平台基地址（如 https://api.cloud.ruixin-ac.com.cn）
    history_api_username TEXT DEFAULT '',            -- 平台账号（公司级，访问所有机器）
    history_api_password TEXT DEFAULT '',            -- 平台密码
    created_at        TEXT    DEFAULT (datetime('now','localtime')),
    updated_at        TEXT    DEFAULT (datetime('now','localtime'))
);

-- 初始化配置（如果为空则插入默认行）
INSERT OR IGNORE INTO enterprise_config (id) VALUES (1);

-- 注意：presets 列通过 Python 迁移脚本添加（见 core/presets.py::migrate_db）

-- 项目表
CREATE TABLE IF NOT EXISTS carbon_projects (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_code        TEXT UNIQUE,                -- GDES-VCER-036-1（审定通过后由促进会分配）
    project_name        TEXT    NOT NULL,
    project_type        TEXT    NOT NULL,           -- 项目类型显示名
    project_summary     TEXT    DEFAULT '',
    hook_name           TEXT    NOT NULL,           -- 使用的计算公式标识（DB字段名保留hook_name以兼容）

    -- VCER 项目凭据
    vcer_query_key      TEXT    DEFAULT NULL,       -- 促进会查询密钥
    vcer_query_pwd      TEXT    DEFAULT NULL,       -- 促进会查询密码

    -- 阶段状态（本地镜像，可从 VCER API 同步；legacy，改版后不再写具体状态）
    stage1_status       TEXT    DEFAULT '未提交',   -- 申请
    stage2_status       TEXT    DEFAULT '未提交',   -- 审定
    stage3_status       TEXT    DEFAULT '未提交',   -- 核查（可多轮）
    stage4_status       TEXT    DEFAULT '未提交',   -- 交易
    stage5_status       TEXT    DEFAULT '未提交',   -- 抵消

    -- 阶段是否通过（布尔，本地唯一事实；具体状态由 VCER API 现场获取）
    stage1_passed       INTEGER DEFAULT 0,          -- 申请
    stage2_passed       INTEGER DEFAULT 0,          -- 审定
    stage3_passed       INTEGER DEFAULT 0,          -- 核查
    stage4_passed       INTEGER DEFAULT 0,          -- 交易
    stage5_passed       INTEGER DEFAULT 0,          -- 抵消

    -- 碳资产数据（从 VCER API 同步）
    total_carbon_assets     REAL DEFAULT 0,         -- 总碳资产 = 审定减排量合计 tCO₂e
    available_assets_gross  REAL DEFAULT 0,         -- 可用碳资产A = 全部已核证量合计 tCO₂e
    available_assets_net    REAL DEFAULT 0,         -- 可用碳资产B = 已核证量-已交易量 tCO₂e
    traded_amount           REAL DEFAULT 0,         -- 已交易量 tCO₂e

    -- 本地计算数据
    baseline_date                   TEXT DEFAULT NULL,   -- 基准日期（如 "2024-01-01"）＝计入期开始日
    estimated_annual_reduction      REAL DEFAULT NULL,   -- 预估年减排量 tCO₂e
    pending_validation_10yr         REAL DEFAULT NULL,   -- 待审定10年预估减排量 tCO₂e
    pending_verification_cumulative REAL DEFAULT 0,      -- 累积核证减排量（自基线日起）tCO₂e
    projection_10yr                 TEXT DEFAULT NULL,   -- JSON：十年逐年预估 [{year,start,end,baseline_emission,project_emission,reduction}]；为空则按公式实时算

    -- ⚠️ legacy（多机器改造后不再被采集与报告读取）：
    -- 数据源与公式参数已搬到 project_machines，每个项目一台机器一份。
    -- 这两列只保留作「新建机器时的默认值来源」，见 project_manager.create_machine()。
    data_source_type    TEXT DEFAULT 'manual',      -- mqtt / api / manual
    data_source_config  TEXT DEFAULT '{}',          -- JSON 配置

    -- ⚠️ legacy（同上）：公式参数已按机器存放于 project_machines.hook_params
    hook_params         TEXT DEFAULT '{}',          -- JSON（DB字段名保留hook_params以兼容）

    -- 项目预设信息（项目级模板变量，每个项目独立配置）
    presets             TEXT DEFAULT '[]',          -- JSON: [{key, label, type, value, category, sort_order}]

    -- 上次同步到促进会的时间
    last_sync_time      TEXT DEFAULT NULL,

    created_at          TEXT DEFAULT (datetime('now','localtime')),
    updated_at          TEXT DEFAULT (datetime('now','localtime'))
);

-- 项目机器表（一个项目多台设备，每台一份数据源配置 + 公式参数）
-- 公式（carbon_projects.hook_name）仍是**项目级**的 —— PDD 的方法学一般只有一个，
-- 机器上不再单独选公式。真要一台一个公式，给本表加一列即可。
CREATE TABLE IF NOT EXISTS project_machines (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id         INTEGER NOT NULL REFERENCES carbon_projects(id) ON DELETE CASCADE,
    name               TEXT    NOT NULL,            -- 「1号机器」（可改成「空压站A」）
    sort_order         INTEGER DEFAULT 0,
    hook_params        TEXT    DEFAULT '{}',        -- 该机器的公式参数 JSON
    data_source_type   TEXT    DEFAULT 'manual',    -- mqtt / api / manual
    data_source_config TEXT    DEFAULT '{}',        -- 该机器的数据源配置 JSON
    backfill_method    TEXT    DEFAULT 'none',      -- 历史回溯方式：none / api / manual
    normalizer_config TEXT DEFAULT '{}',       -- 前置转换文件和参数
    backfill_config    TEXT    DEFAULT '{}',        -- 回溯配置 JSON（mqtt+api 回溯时存 {site_id,aliases,record_time_type}）
    created_at         TEXT    DEFAULT (datetime('now','localtime')),
    updated_at         TEXT    DEFAULT (datetime('now','localtime'))
);
-- ⚠️ 本表的 idx_machine_project 与 monitoring_data.machine_id 上的索引
-- **只在 _migrate_schema 里建**：老库的 monitoring_data 没有 machine_id 列，
-- 而本脚本跑在补列迁移之前 —— 在这里建会报 "no such column" 把初始化搞挂。
-- （同 system_logs.project_id 的坑，见文件末尾注释。）

-- 实时监测数据表
CREATE TABLE IF NOT EXISTS monitoring_data (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES carbon_projects(id) ON DELETE CASCADE,
    machine_id      INTEGER DEFAULT NULL,           -- 所属机器（老库由迁移回填，见 _migrate_schema）
    timestamp       TEXT    NOT NULL,               -- 采集时间
    raw_data        TEXT    DEFAULT '{}',           -- JSON：原始采集数据
    carbon_reduction REAL   DEFAULT NULL,           -- 计算结果：本期减排量 tCO₂e
    cumulative_reduction REAL DEFAULT NULL,         -- 累计减排量 tCO₂e（**按机器**各自累计）
    breakdown       TEXT    DEFAULT NULL,           -- JSON：计算过程分解
    source          TEXT    DEFAULT 'auto',         -- auto / manual / import / api
    created_at      TEXT    DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_monitor_project_ts ON monitoring_data(project_id, timestamp);
CREATE TABLE IF NOT EXISTS monitoring_quality_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    machine_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    start_row_id INTEGER NOT NULL,
    resolution TEXT NOT NULL DEFAULT 'pending',
    baseline_row_id INTEGER,
    reviewer TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    snapshot_json TEXT DEFAULT '{}',
    case_signature TEXT DEFAULT '',
    invalidated_at TEXT DEFAULT '',
    UNIQUE(machine_id,kind,start_row_id)
);
CREATE INDEX IF NOT EXISTS idx_quality_project
    ON monitoring_quality_decisions(project_id,machine_id);

CREATE INDEX IF NOT EXISTS idx_monitor_anomaly_summary
ON monitoring_data(project_id, json_extract(breakdown,'$.sampling_conflict'))
WHERE json_valid(breakdown)
  AND (json_extract(breakdown,'$.meter_review')=1
       OR json_extract(breakdown,'$.boundary_review')=1);

CREATE INDEX IF NOT EXISTS idx_monitor_gap_summary
ON monitoring_data(project_id)
WHERE json_valid(breakdown) AND json_extract(breakdown,'$.coverage_gap')=1;

-- 批量删除监测记录时保留可恢复的完整原始行和操作批次。
CREATE TABLE IF NOT EXISTS monitoring_deletion_archive (
    batch_id       TEXT NOT NULL,
    original_id    INTEGER NOT NULL,
    project_id     INTEGER NOT NULL,
    machine_id     INTEGER,
    row_json       TEXT NOT NULL,
    deleted_at     TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    restored_at    TEXT,
    PRIMARY KEY (batch_id, original_id)
);
CREATE INDEX IF NOT EXISTS idx_monitor_deletion_project
ON monitoring_deletion_archive(project_id, deleted_at DESC);

-- 报告记录表
CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES carbon_projects(id) ON DELETE CASCADE,
    report_type     TEXT    NOT NULL,               -- verification / pdd
    report_title    TEXT    NOT NULL,
    file_path       TEXT    NOT NULL,               -- Word 文件路径
    file_size_bytes INTEGER DEFAULT NULL,
    parameters      TEXT    DEFAULT NULL,           -- JSON：生成时的参数快照
    generated_at    TEXT    DEFAULT (datetime('now','localtime'))
);

-- 告警规则表
CREATE TABLE IF NOT EXISTS alert_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER REFERENCES carbon_projects(id) ON DELETE CASCADE,
    metric          TEXT    NOT NULL,               -- carbon_reduction / flow_rate / power
    operator        TEXT    NOT NULL,               -- gt (大于) / lt (小于)
    threshold       REAL    NOT NULL,               -- 阈值
    cooldown_minutes INTEGER DEFAULT 60,            -- 冷却时间（分钟）
    is_enabled      INTEGER DEFAULT 1,
    created_at      TEXT    DEFAULT (datetime('now','localtime')),
    updated_at      TEXT    DEFAULT (datetime('now','localtime'))
);

-- 告警日志表
CREATE TABLE IF NOT EXISTS alert_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id         INTEGER REFERENCES alert_rules(id) ON DELETE SET NULL,
    project_id      INTEGER REFERENCES carbon_projects(id) ON DELETE CASCADE,
    alert_message   TEXT    NOT NULL,
    metric_value    REAL    DEFAULT NULL,
    threshold_value REAL    DEFAULT NULL,
    acknowledged    INTEGER DEFAULT 0,
    triggered_at    TEXT    DEFAULT (datetime('now','localtime'))
);

-- 系统日志表（「日志」页签的数据源）
-- 由 core/app_log.py 的 SqliteLogHandler 异步批量写入，超出上限自动删最旧的。
CREATE TABLE IF NOT EXISTS system_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,               -- 记录产生时间 YYYY-MM-DD HH:MM:SS
    level       INTEGER NOT NULL,               -- logging 数字级别，便于 >= 过滤
    level_name  TEXT    NOT NULL,               -- DEBUG / INFO / WARNING / ERROR / CRITICAL
    logger      TEXT    DEFAULT NULL,           -- logger 名字（哪个模块）
    message     TEXT    NOT NULL,
    detail      TEXT    DEFAULT NULL,           -- 异常时的 traceback
    project_id  INTEGER DEFAULT NULL            -- 归属项目（logging extra={"project_id":…}）
);
CREATE INDEX IF NOT EXISTS idx_system_logs_level ON system_logs(level);
CREATE INDEX IF NOT EXISTS idx_system_logs_ts ON system_logs(ts);
-- 注意：project_id 的索引**不在这里建**。老库的 system_logs 没有这一列，
-- 而本脚本跑在补列迁移（_migrate_schema）之前 —— 在这里建会直接报
-- "no such column: project_id" 把整个初始化搞挂。放到 _migrate_schema 里建。

-- ============================================================
-- 初始数据示例（可选，部署后可手动配置）
-- ============================================================

-- 示例：创建一个空压站项目的初始记录（需根据实际情况修改参数）
-- INSERT OR IGNORE INTO carbon_projects (
--     project_name, project_type, hook_name,
--     data_source_type, data_source_config,
--     hook_params, baseline_date
-- ) VALUES (
--     '示例空压站项目', '空压站节能', 'air_compressor',
--     'manual', '{}',
--     '{"dew_point": 0, "rated_pressure": 0.3, "avg_flow_rate": 400, "has_oil": "有油"}',
--     '2024-01-01'
-- );

-- Continuity review, audit and local gap probes

    CREATE TABLE IF NOT EXISTS monitoring_quality_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,machine_id INTEGER NOT NULL,
        kind TEXT NOT NULL,start_row_id INTEGER NOT NULL,action TEXT NOT NULL,payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));
    CREATE INDEX IF NOT EXISTS idx_quality_history ON monitoring_quality_history(machine_id,id);
    CREATE TABLE IF NOT EXISTS monitoring_continuity_acks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,action TEXT NOT NULL,
        start_date TEXT NOT NULL,end_date TEXT NOT NULL,signature TEXT NOT NULL,warnings_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));
    CREATE TABLE IF NOT EXISTS monitoring_gap_probes (
        id TEXT PRIMARY KEY,project_id INTEGER NOT NULL,machine_id INTEGER NOT NULL,
        start_row_id INTEGER NOT NULL,case_signature TEXT NOT NULL,status TEXT NOT NULL,
        result_json TEXT NOT NULL DEFAULT '{}',started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),finished_at TEXT);
    CREATE INDEX IF NOT EXISTS idx_gap_probe_case ON monitoring_gap_probes(machine_id,start_row_id,started_at);
