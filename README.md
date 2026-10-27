# 通用碳账户管理系统

> 开发与交付版本规则见 [版本管理与发布约定](版本管理与发布约定.md)：唯一通用主线、独立企业包、冻结交付副本。

> **AI 接手必读：** 先读 [AGENTS.md](AGENTS.md)。企业包支持单包自动识别；移除包后重启恢复通用模式，具体选择规则见 [企业目录说明](enterprises/README.md)。

> **2026-10-26 企业目录调整：** 企业专属适配代码、模板与原 `data/` 已集中到 `enterprises/<企业标识>/`。当前鑫钻目录为 `enterprises/xinzuan/`，本机通过 `.enterprise.local.json` 选择。以下旧章节中的 `data/` 均指选定企业内的 `data/`。目录选择、交付与升级以 [企业目录说明](enterprises/README.md) 为准；鑫钻专属内容见 [鑫钻目录清单](enterprises/xinzuan/README.md)。

> 企业侧独立部署的碳账户管理系统。每个企业运行一套自己的实例，管理碳项目、计算碳减排量、自动生成核查报告，通过促进会 VCER API 完成碳资产申请与审定。

---

## 一、项目概述

本系统是一个**独立项目**，由企业部署在自己的服务器上运行。它提供：

| 功能 | 说明 |
|------|------|
| 📊 **碳资产看板** | 仪表盘总览：总碳资产、可用碳资产、已交易量、项目阶段统计 |
| 📋 **项目管理** | 创建/编辑项目，跟踪 VCER 5 阶段进度（申请→审定→核查→交易→抵消） |
| 📡 **实时监测** | 对接企业数据源（MQTT / REST API / 手动录入），自动计算碳减排量 |
| 📄 **报告自动生成** | 一键生成 PDF / Word 核查报告，支持 .docx 模板上传和自定义占位符 |
| 🔄 **促进会同步** | 通过 VCER API 同步碳资产、项目状态、提交申请 |
| 🧩 **公式插件** | 7 种预装公式（空压站/光伏/余热回收/节能电机/生物质/LED/热泵），支持自定义扩展 |
| ⚠ **告警引擎** | 可配置告警规则，监测数据异常时触发告警 |

## 二、架构说明

```
企业侧（本项目）                   促进会侧（VCER 系统）
┌─────────────────────┐           ┌─────────────────────┐
│  通用碳账户管理系统  │  HTTP API │  VCER 后端          │
│  ┌───────────────┐  │ ───────→ │  /cn/api/carbon_*   │
│  │ Flask Web App │  │ ←─────── │  /cn/api/vcer/*      │
│  ├───────────────┤  │           │  /cn/api/account/*   │
│  │ 碳计算引擎    │  │           └─────────────────────┘
│  ├───────────────┤  │
│  │ 公式插件      │  │  ← 7 种预装公式 + 支持自定义扩展
│  ├───────────────┤  │
│  │ 数据源适配器  │  │  ← 对接企业 IoT / API / 手动录入
│  ├───────────────┤  │
│  │ SQLite 数据库 │  │  ← 企业本地数据存储
│  └───────────────┘  │
└─────────────────────┘
```

**核心设计原则：**
- 企业侧拥有完整的数据主权（数据库、计算引擎都在本地）
- 促进会只提供 API（查碳资产、提交申请、审定核查），不持有企业运营数据
- 公式插件机制使系统可适配任意项目类型（空压站、光伏、生物质、热泵…）

## 三、快速开始

### 3.1 环境要求

- Python 3.10+
- pip

### 3.2 安装

```bash
# 1. 进入项目目录
cd enterprise-carbon-account

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务
python app.py

# 服务运行在 http://localhost:5000
```

正式运行建议安装 `waitress` 后执行 `python serve.py`，或
`waitress-serve --call --port=5000 serve:create_app`。该入口会先初始化数据库、公式和后台任务。
不要使用 `waitress-serve app:app`：它只导入 Flask 对象，不会执行系统初始化。
同一数据库只运行一个服务进程，避免重复采集和任务调度。

### 3.3 配置

首次使用需要在 Web 界面「设置」页配置：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| 企业名称 | 企业全称 | 我的企业 |
| 企业序号 | 促进会分配的企业序号 | 036 |
| VCER API 地址 | 促进会 API 基础地址 | http://api.environdec.cn |
| 电网排放因子 | 可选，默认 0.5777 | 0.5777 |

也可以通过环境变量配置：

```bash
export ENTERPRISE_NAME="我的企业"
export ENTERPRISE_ID="036"
export VCER_API_URL="http://api.environdec.cn"
export PORT=5000
python app.py
```

### 3.4 Docker 部署

```bash
docker run -d -p 8080:5000 \
  -v /data/carbon-account:/app/data \
  -e VCER_API_URL=http://api.environdec.cn \
  -e ENTERPRISE_ID=036 \
  gdes/carbon-account:latest
```

## 四、使用流程

### 4.1 创建第一个项目

1. 打开 Web 界面 → 「项目管理」→「新建项目」
2. 填写项目名称
3. 选择计算公式（如「空压站节能碳减排」或「光伏发电碳减排」）
4. 填写公式参数（JSON 格式），例如空压站项目：
   ```json
   {
     "dew_point": 0,
     "rated_pressure": 0.3,
     "avg_flow_rate": 400,
     "has_oil": "有油"
   }
   ```
   光伏项目：
   ```json
   {
     "installed_capacity": 500,
     "panel_type": "mono"
   }
   ```
5. 设置基准日期
6. 点击保存

### 4.1.1 多台机器（多设备）

**一个项目可以有多台机器**，每台各自配置数据源与公式参数：

- 保存项目后系统会自动创建「1号机器」，在项目弹窗的「机器」区块里编辑它
- 点「+ 加机器」再加一台（可改成「空压站A」这样的名字）
- 每台机器单独选**数据源类型**（可以一台 MQTT、另一台手动录入）、填**数据源配置**与**公式参数**
- **公式本身是项目级的**（PDD 的方法学一般只有一个），机器上不单独选公式
- 监测数据按机器分开存放；PDD 的 B.2 / B.3 / B.4 表会自动变成
  「年份 | 1号机器 | 2号机器 | … | 合计」，十年减排总量按各机器汇总
- 删机器会**连同它名下的监测数据一起删除**（前端会先提示条数），
  且项目至少要保留一台机器

### 4.2 录入监测数据

**手动录入：** 项目列表 →「录入数据」→ 填写原始数据字段 → 保存并计算

**自动采集：** 配置数据源后，系统按设定间隔自动拉取数据并计算减排量。

### 4.3 申请碳资产

1. 项目运行一段时间后，在「项目管理」点击「同步」获取促进会最新碳资产数据
2. 在「VCER」标签页可查看各阶段审核状态和多轮核查历史
3. 如需正式申请碳资产审定，需在促进会 VCER 系统中提交

### 4.4 生成核查报告

**PDF 报告：**
1. 进入「报告中心」
2. 选择项目和报告类型
3. 点击「生成报告」
4. 下载 PDF

**Word 报告（支持自定义模板）：**
1. 在「报告中心」→「模板管理」面板上传 .docx 模板
2. 模板中使用 `{{占位符名}}` 引用数据（在「占位符参考」面板查看所有可用占位符）
3. 可选择添加自定义占位符
4. 选择项目和模板后生成，系统自动替换所有占位符

报告包含：
- 项目基本信息
- 碳资产概况
- 阶段状态
- 监测数据摘要（受核查日期范围约束，按选定区间汇总减排量）
- 计算公式及参数
- 计算分解
- 法律声明

## 五、预装公式插件

系统预装 7 种碳减排计算公式，启动时自动扫描 `hooks/` 目录注册：

| 公式标识 | 中文名 | 适用场景 | 参考方法学 | 核心公式 |
|---------|--------|---------|-----------|---------|
| `air_compressor` | 空压站节能碳减排 | 空压站变频节能 | 促进会 API | `(Q_b × Z_b × 10⁻⁵) - (AD_电 / 1000 × EF_grid)` |
| `solar_pv` | 光伏发电碳减排 | 分布式/集中式光伏 | CDM ACM0002 | `EG × EF_grid` |
| `waste_heat_recovery` | 余热回收碳减排 | 烟气/废水/工艺余热 | CDM ACM0012 | `Q_recovered / η × EF_thermal` |
| `energy_saving_motor` | 节能电机/变频器碳减排 | 电机变频调速、高效替换 | CDM AMS-II.D | `ΔE × (η_baseline/η_actual - 1) × EF_grid` |
| `biomass_boiler` | 生物质锅炉替代碳减排 | 生物质替代燃煤/燃油/燃气 | CDM ACM0006 | `Q_steam × (EF_fossil/η_old - EF_biomass/η_new)` |
| `led_lighting` | LED照明改造碳减排 | LED替换传统灯具 | CDM AMS-II.J | `(P_old - P_new) × Hours × EF_grid` |
| `heat_pump` | 热泵替代碳减排 | 空气源/地源/水源热泵 | CDM AMS-II.C | `Q_heat × (EF_fuel/η_old - EF_grid/COP/3.6)` |

> **注意：** 目前一个项目绑定一个公式。如果企业有多个不同类型的减排设备（如既有光伏又有热泵），需要创建多个项目分别管理。

### 5.1 开发新公式

1. 复制 `hooks/TEMPLATE.py` → 重命名为新文件名（如 `my_formula.py`）
2. 修改类属性：
   ```python
   class MyFormula(BaseCarbonFormula):
       formula_name = "my_formula"         # ★ 必须与文件名一致（不含 .py）
       formula_label = "我的碳减排公式"     # UI 显示名
       formula_version = "1.0"
       description = "适用项目类型描述"

       required_raw_fields = ["field_1", "field_2"]        # 原始数据字段
       required_formula_params = ["param_1", "param_2"]    # 公式参数（不需要则设为 []）
   ```
3. 实现核心计算：
   ```python
   def calculate(self, raw_data, cumulative_data):
       """使用累计值增量法计算"""
       current = float(raw_data.get("field_1", 0))
       prev = float(cumulative_data.get("prev_field_1", 0))
       delta = max(current - prev, 0)  # 防止累计值回退

       reduction = delta * self.some_factor  # tCO₂e
       cumulative = cumulative_data.get("cumulative_reduction", 0) + reduction

       return {
           "carbon_reduction": round(reduction, 6),
           "cumulative_reduction": round(cumulative, 6),
           "breakdown": {"delta": delta, "formula": "..."}
       }
   ```
4. 将文件放入 `hooks/` 目录
5. 重启服务 → 自动加载（控制台打印 `[FORMULA] 已注册: my_formula`）

### 5.2 公式基类接口

详见 `hooks/base_hook.py`，每个公式必须实现：

| 方法 | 说明 |
|------|------|
| `fetch_baseline_factors(raw_data)` | 获取基准线排放因子（不需要外部数据的公式返回 `{}` 即可） |
| `calculate(raw_data, cumulative_data)` | ★ 核心 — 返回 `{carbon_reduction, cumulative_reduction, breakdown}` |
| `estimate_annual(period_data)` | 预估年度减排量（基类有默认实现：日均 × 365） |
| `get_formula_text()` | 返回可读公式文本（用于报告） |
| `get_parameter_descriptions()` | 返回参数中文说明（用于 UI 和报告） |

## 六、数据源适配器

系统支持多种数据源：

| 适配器 | 类型标识 | 适用场景 |
|--------|----------|----------|
| MQTT | `mqtt` | IoT 设备通过 MQTT Broker 实时上报数据（不可回溯） |
| API | `api` | 平台提供「按时间段查历史」接口，每个周期拉一个时间窗批量入账（自动补断档，天然支持回溯） |
| 手动录入 | `manual` | 无自动化数据源，人工录入或 CSV 导入 |

### API 配置示例（定时拉取 + 回溯）

平台接口形态（首个接入企业鑫钻的 ESG API，基地址 `https://oa.020xz.com.cn/api/esg`）：
- 认证：`POST /auth/login`(username/password) → JWT（24h）→ 之后 `Authorization: Bearer`
- 查数据：`POST /data/{dataId}`，body `{"beginDate","endDate","aliases"}`（逗号分隔一次查两个测点）
- 批量：`POST /data/batch`，一次查多个 dataId；项目列表：`GET /projects`
- 响应：`{"series":{"<alias>":[{value,apartValue,recordTime,...}]}}`，5 分钟粒度，recordTime 为 ISO 微秒

**平台地址 + 账号密码是公司级**，配在「设置 → 企业配置」（账号密码即登录凭据）；机器编辑页单独填写站点 ID（dataId）；下面是保存后的机器数据源配置示例：

```json
{
  "site_id": "1024",
  "record_time_type": "normal"
}
```

平台怎么认证、URL 怎么拼、响应怎么解析，全部写死在前置转换文件 `enterprises/xinzuan/normalizers/xinzuan.py` 里
（登录拿 token 缓存 24h、401 自动重登、单机查询一次查两个测点）。机器的「前置转换」开关单独选择 `xinzuan`，
保存在 `normalizer_config`；URL 统一在「设置 → 企业配置 → 历史数据 API 地址」填写，
机器可编辑请求方法、Header、Params、Data 和指标别名，
一键应用时仍保留每台机器的 `site_id`。单次查询窗口受平台限制
（当前转换器默认按 28 天分块，实际限制以目标接口测试为准）。

**批量回溯（历史补录）**：每台机器可配「历史回溯方式」（无 / API / 手动录入）。API 机器天然可回溯；
MQTT 机器也可选「API 回溯」。项目卡片对含可回溯机器的项目显示「⏳ 回溯历史」，工具栏另有
「⏳ 批量回溯历史」对全部项目。回溯时**同公司机器合并走平台批量接口**（一次请求拉多台，减少请求次数），
按 dataId 分发逐台入账；平台不支持批量则回退逐台单查。从基准日期（弹窗指定 → 机器配置
`backfill_since` → 项目 `baseline_date` 依次取）到当前全量补拉，回溯序列内不做断档重置、
以上一条历史记录为上期做差。**幂等**：按「机器+来源+时间戳」去重，已存在的时间戳自动跳过不覆盖。

### 数据前置归一化层（normalizer）

MQTT 只规定「怎么传」，不规定「传什么」—— 每家平台的报文格式都不一样。
系统在数据入口挂了一层前置归一化（与 `hooks/` 公式同机制），不管对方什么格式，
出来都是内部统一记录（字段名以项目公式的 `required_raw_fields` 为准，如空压站公式
是 `cumulative_flow` / `cumulative_power`），下游采集/公式完全无感。

**一个企业一个转换文件**，放在 `normalizers/` 目录，自动发现注册；机器编辑表单里
「前置转换」是个**开关**，开了就选一个文件（独立存入机器的 `normalizer_config`），
关了 = 数据已是系统格式、原样直通。旧的 `data_source_config.normalizer` 仍兼容读取。文件里按需实现（以鑫钻为例）：
- `aliases`：内部字段 ↔ 平台别名（单一来源）
- `convert_mqtt`（MQTT 通道）、`convert_api`（API 单条记录映射）
- `build_history_request` / `parse_history_response`：单机查询的 URL/请求体/响应解析
- `auth_headers` / `on_auth_failure`：认证头 + 401 重登（登录前置）
- `list_projects`：站点列表（前端「获取站点列表」按钮用）
- `build_batch_request` / `parse_batch_response`：批量查询（机器多时回溯用）
- `poll_interval_seconds` / `backfill_chunk_days`：拉取节奏与分块
新企业复制 `normalizers/TEMPLATE.py` 即可。

**历史数据补录**：`tools/fetch_ruixin_history.py`（首个接入企业的实现，可作其他平台的参照）
一键拉取并生成系统可导入的 CSV（`timestamp,cumulative_flow,cumulative_power`）。
**本地联调**：`tools/api_simulator.py` 模拟鑫钻 ESG API（login/projects/data/batch），无真实账号即可端到端测试。

### MQTT 配置示例

```json
{
  "broker_host": "192.168.1.100",
  "broker_port": 1883,
  "topic": "enterprise/036/air_station/stn1/data",
  "username": "mqtt_user",
  "password": "mqtt_pass"
}
```

## 七、报告占位符系统

生成 Word 报告时，模板中使用 `{{占位符名}}` 引用数据。系统提供 35+ 个可用占位符，按 7 大分类管理：

| 分类 | 包含占位符 | 说明 |
|------|-----------|------|
| 项目信息 | `project_name`, `project_code`, `project_type`, `baseline_date`, `project_summary` | 项目基础字段 |
| 企业信息 | `enterprise_name`, `enterprise_id` | 企业配置字段 |
| 报告元信息 | `report_start_date`, `report_end_date`, `report_generated_at`, `report_type_label` | 报告生成时的元数据 |
| 碳资产数据 | `total_carbon_assets`, `available_assets_gross`, `available_assets_net`, `traded_amount`, `cumulative_reduction` | 碳资产数值 |
| 监测数据 | `total_records`, `total_reduction`, `daily_avg_reduction`, `annual_estimate`, `breakdown_summary` | **受核查日期范围约束** |
| 阶段信息 | `stage1_status`~`stage5_status`, `stage1_name`~`stage5_name` | 五阶段审核状态 |
| 计算公式 | `formula_text`, `ef_grid`, `hook_name`, `hook_label` | 公式文本和参数 |
| 公式参数（动态） | 随公式不同自动变化 | 如 `dew_point`, `installed_capacity`, `cop_value` 等 |

> **核查日期范围说明：** `total_reduction`、`total_records`、`daily_avg_reduction`、`annual_estimate` 均按选定日期范围过滤监测数据后汇总计算。例如项目全周期 A→C 累计减排 20t，范围选 B→C，则 `total_reduction` = B→C 期间的新增减排量，而非全周期累计值。

### 自定义占位符

上传模板时可添加自定义占位符（如 `{{contact_person}}`、`{{audit_opinion}}`），值由用户填写，用于报告中不变更的手动内容。

## 八、API 参考

系统自带 REST API，可供企业现有系统集成：

### 项目管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/projects` | 项目列表 |
| POST | `/api/projects` | 创建项目 |
| GET | `/api/projects/:id` | 项目详情 |
| PUT | `/api/projects/:id` | 更新项目 |
| DELETE | `/api/projects/:id` | 删除项目 |

### 公式管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/formulas` | 可用公式列表（含参数定义和描述） |
| GET | `/api/formulas/:name/params` | 公式参数详情 |

### 监测与计算

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/projects/:id/collect` | 触发一次数据采集 |
| POST | `/api/projects/:id/compute` | 触发碳减排计算 |
| GET | `/api/projects/:id/compute/estimate` | 预估年度减排量 |
| GET | `/api/projects/:id/monitoring` | 监测数据历史 |
| GET | `/api/projects/:id/monitoring/latest` | 最新监测数据 |
| POST | `/api/projects/:id/monitoring/manual` | 手动录入监测数据 |

### VCER 同步

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/projects/:id/sync` | 同步促进会碳资产 |
| GET | `/api/projects/:id/vcer/status` | 查询 VCER 阶段状态 |
| GET | `/api/projects/:id/vcer/rounds` | 查询多轮核查历史 |
| POST | `/api/projects/:id/vcer/submit` | 向促进会提交申请 |
| POST | `/api/projects/:id/vcer/change_password` | 修改 VCER 查询密码 |
| POST | `/api/projects/:id/vcer/request_password_reset` | 申请密码重置 |
| GET | `/api/vcer/file` | 获取 VCER 审核文件 |

### 报告与模板

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/projects/:id/report/generate` | 生成报告（支持 `template_id` 参数） |
| GET | `/api/reports` | 报告历史列表 |
| GET | `/api/reports/:id/download` | 下载报告文件（PDF 或 .docx） |
| GET | `/api/reports/placeholders` | 获取分类占位符列表（支持 `?project_id=` 预览示例值） |
| GET | `/api/reports/templates` | 模板列表（支持 `?type=&hook=` 过滤） |
| POST | `/api/reports/templates` | 上传 .docx 模板（multipart） |
| PUT | `/api/reports/templates/:id` | 更新模板元数据 |
| DELETE | `/api/reports/templates/:id` | 删除模板 |
| POST | `/api/reports/templates/:id/set-default` | 设为默认模板 |

### 告警与仪表盘

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dashboard` | 仪表盘聚合数据 |
| GET | `/api/alerts/rules` | 告警规则列表 |
| POST | `/api/alerts/rules` | 创建告警规则 |
| DELETE | `/api/alerts/rules/:id` | 删除告警规则 |
| GET | `/api/alerts/log` | 告警日志 |

### 配置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 获取企业配置 |
| POST | `/api/config` | 更新企业配置 |

## 九、项目结构

```text
enterprise-carbon-account/
├── app.py / serve.py           # 通用应用与服务入口
├── config.py                  # 通用配置，运行数据路径随企业选择
├── enterprise_paths.py        # 企业目录选择与路径约定
├── database/                  # 通用数据库结构
├── core/                      # 计量账本、核对、项目、VCER、报告等业务逻辑
├── adapters/                  # 通用数据采集通道
├── hooks/                     # 通用行业公式
├── normalizers/               # 转换器基类与编写示例
├── presets_packs/             # 企业预设包编写示例
├── static/                    # 共用前端
├── templates/reports/         # 共用页面模板
├── tools/                     # 通用维护与测试
├── .enterprise.local.json     # 本机选择文件，Git 忽略
└── enterprises/
    ├── README.md              # 企业目录通用说明
    └── <企业标识>/            # 私有交付，Git 忽略
        ├── data/              # 正式库、历史报告、图片、归档、备份等
        ├── normalizers/       # 本企业 API/报文转换代码
        ├── templates/reports/docx/ # 本企业 Word 模板和占位符设置
        ├── presets_packs/     # 可选本企业预设扩展
        ├── tools/             # 本企业专用维护工具
        ├── docs/              # 企业手册和私有验收记录
        └── README.md          # 该企业的具体清单与安装说明
```

当前本机使用 `enterprises/xinzuan/`。首次安装和升级详见 [企业目录说明](enterprises/README.md)。

## 十、数据库表

| 表名 | 说明 |
|------|------|
| `enterprise_config` | 企业配置（单行） |
| `carbon_projects` | 碳项目（核心表，含公式绑定和 VCER 凭据） |
| `project_machines` | 项目机器/设备（一个项目多台，每台一份数据源配置 + 公式参数） |
| `monitoring_data` | 监测数据（时间序列，含原始数据、减排结果、计算分解；按 `machine_id` 归属机器） |
| `reports` | 生成的报告记录 |
| `report_templates` | Word 模板（含自定义占位符和默认模板标志） |
| `alert_rules` | 告警规则 |
| `alert_log` | 告警日志 |

详见 `database/schema_sqlite.sql`。

## 十一、促进会侧配套改动

本系统依赖促进会 VCER 系统提供以下 API：

| API | 说明 | 状态 |
|-----|------|------|
| `GET /cn/api/carbon_assets` | 碳资产查询 | 已有 |
| `GET /cn/api/carbon_projects` | 项目列表查询 | 已有 |
| `GET /cn/api/air_compressor_params` | 空压站基准线参数获取 | 已有 |
| `GET /cn/api/vcer/project_by_key` | 项目详情查询 | 已有 |
| `GET /cn/api/vcer/stage_rounds` | 多轮核查历史 | 已有 |
| `GET /cn/api/account/enterprise_summary` | 企业碳账户汇总 | **新增** |
| `POST /cn/api/account/project_sync` | 项目数据同步 | **新增** |
| `GET /cn/api/account/hook_params` | 公式参数查询 | **新增** |

新增 API 已在 `E:\Python\main_app.py` 和 `E:\Python\db.py` 中实现。

---

*项目版本: 1.1 | 最后更新: 2026-07-07*


### 连续计量与计量阶段（2026-10-20）

监测读数回零后按机器建立新计量阶段，阶段内继续计算，项目累计为各阶段之和。日期缺样且两端累计非负时按实测差计量；停机连续性由操作员在事件窗口预览并确认，已确认区间不再弹报告提示。完整业务口径与操作见2026-10-20整本重写的《鑫钻项目交付说明与操作手册》第4、9、10章；逐项目缺口、站点映射及核查范围见附录A～E。清理与多企业部署统一看该手册第17章，不沿用早期演示清理步骤。

当前应用没有登录鉴权，以下新增/调整的核对、生成与第三阶段操作仅接受真正回环 IP，转发头不能解锁局域网访问：

- `GET /api/projects/:id/monitoring/segments`：机器阶段摘要；`monitoring/all` 增加可选 `segment_id`。
- `POST /api/projects/:id/monitoring/continuity/preflight`：`start_date/end_date` → `warnings/signature/requires_confirmation`。
- `POST /api/projects/:id/monitoring/quality/recheck`：`machine_id/start_row_id` → 本地任务 ID。
- `GET/POST /api/projects/:id/monitoring/quality/probes/:job`：读取任务摘要；POST `action=refresh` 复用已存源记录刷新预览，POST `reviewer/note` 应用备份后的补录事务。接口不返回源响应体或待补入的原始记录数组。
- 报告生成遇未确认缺口或阶段边界返回 HTTP 409、`code=continuity_confirmation_required` 及预检数据；提交 `continuity_ack=signature` 后继续。第三阶段表单新增本地 `verification_start/verification_end/continuity_ack`，不向促进会转发这三个字段。
- 停机确认使用 `confirmed_shutdown`，其他连续性确认使用 `confirmed_continuous`；确认后新增区间内读数会使旧确认失效并保留原计量，事件窗口预览 `accept_new_data` 后才正式接纳新计算。所有确认、失效、撤销和报告提示均有数据库记录。

`tools/reconcile_monitoring.py --preview` 提供年度、机器阶段、项目累计及待核证差异；`--apply` 应在停采后运行，会先完整备份，再在一笔事务中校验并应用。运行数据库、归档和报告文件均应随部署备份。
