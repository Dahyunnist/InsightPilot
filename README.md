# InsightPilot

本地、只读的退款经营分析 Agent 原型。支持批准退款订单率/金额率、批准退款金额与贡献度、退款状态金额、退款事件日金额、订单计数、cohort 汇总和双区间订单率比较；要求明确日历日期。

## 启动

需要 Python 3.11+。SQL 策略使用 SQLGlot。根目录 `.env` 需包含 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`；可选 `LLM_TIMEOUT_SECONDS`、`LLM_MAX_COMPLETION_TOKENS`。`.env` 已加入忽略列表，不应提交密钥。

```text
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe data/seed.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m insightpilot.evaluate --output reports
.\.venv\Scripts\python.exe -m insightpilot.cli "2026-10-05 到 2026-10-11 华东金额退款率是多少？"
.\.venv\Scripts\python.exe -m insightpilot.cli "2026-10-05 到 2026-10-11 各品类批准退款金额贡献度是多少？"
```

回答带指标公式、日期区间、数据版本、参数化 SQL、结果行和独立汇总复核。所有 SQL 来自本地模板；模型只抽取结构化计划。SQLGlot AST 白名单、SQLite authorizer、只读 URI、`query_only`、2 秒执行上限、250,000 条 SQLite 虚拟机指令预算与 100 行上限共同约束查询。

当前会澄清相对日期、模糊退款率和多指标问题；会拒绝写请求与个人信息请求。固定数据只有 30 单和 32 条退款，不能代表真实企业数据。扫描预算按 SQLite 虚拟机指令计，不是物理磁盘字节数。

API 绑定 `127.0.0.1:8000`，提供 `POST /analysis`、`GET /runs/{id}`、`POST /runs/{id}/cancel`、`GET /health`。MCP 使用独立 loopback 端口 `127.0.0.1:8001/mcp`，暴露 `analyze_refund` 工具和指标/契约资源。API 和 MCP HTTP 共用 `InsightPilot`、Bearer token、读取 scope 与地区限制。配置 `INSIGHTPILOT_API_TOKEN`、`INSIGHTPILOT_API_SCOPES=refund:read,refund:cancel`，可选 `INSIGHTPILOT_API_REGION=华东`。未配置 token 时仅供本机 loopback 开发；不要改成公网监听。

运行记录持久化到 `data/insightpilot-runs.sqlite`，保留 90 天，可用 `INSIGHTPILOT_RUN_RETENTION_DAYS` 调整。记录保存问题哈希、常见手机号/邮箱/身份证的脱敏文本，以及结果中的指标版本、SQL、参数、数据版本和复核证据；不保存原始问题。重启时未完成任务标记为 `interrupted`。进程内并发默认上限 8，SQL 执行可取消；模型 HTTP 请求只受 `LLM_TIMEOUT_SECONDS` 限制，取消会在模型返回后生效。

API 与 MCP 服务需在两个终端分别启动：

```text
.\.venv\Scripts\python.exe -m insightpilot.api_server
```

```text
.\.venv\Scripts\python.exe -m insightpilot.mcp_server
```

## S4 黄金集评测

`evals/cases.jsonl` 固定 12 个正常分析、2 个澄清和 2 个拒绝用例。运行上方评测命令会逐条调用 `.env` 配置的模型，并生成 `reports/evaluation-raw.jsonl`、`reports/retrieval-ablation-raw.jsonl`、`reports/evaluation-summary.json`。原始结果包含 SQL 与查询证据；只在本地保存，不应上传客户数据或密钥。当前结果见 [评测报告](reports/evaluation.md)。演示流程和简历描述见 [五分钟演示](docs/demo.md) 与 [简历要点](docs/resume-bullets.md)。

## S5 版本化语义目录

各指标现在提供定义版本、生效日、粒度、来源表与字段血缘、适用维度、访问范围和来源文档。MCP `metrics://refund-catalog` 返回同一版本化目录。规划器把检索到的指标元数据按需注入；执行前校验维度符合该指标语义契约。未定义的 `refund_event_rate` 保持禁用，模糊“退款率”继续澄清。API/MCP 的 Bearer scope 与地区约束见 S6。

## S7 企业合成基准与归因

原 S0 固定夹具保持不变。新增 `data/enterprise_seed.py` 生成确定性 50,000 单、约 10,000 笔退款的扩展库，含 4 地区、4 渠道、6 品类、客户分层、20 活动、多触点权重、迟到入库退款和约 1% 缺失归因。运行 `python -m data.enterprise_seed` 后，以 `INSIGHTPILOT_DATABASE_PATH=data/insightpilot-enterprise.sqlite` 切换数据。`data.enterprise_seed.quality_report()` 输出 orphan、超额退款、权重错误、迟到行和归因覆盖率。新活动归因需 enterprise 数据版本；S0 上会澄清，不会发 SQL。表粒度和字段约束见 [enterprise 数据契约](docs/enterprise-data-contract.md)。

新增 `region`、`month_utc` 分组和带权多触点活动归因；组级复核用单独查询路径逐组核对。回答还带确定性 `visualization` 规范，可由 API/MCP 客户端渲染为月趋势线或分类柱图。

`evals/enterprise-blind-v1.jsonl` 只放 3 道 holdout 问题；独立 `evals/enterprise-gold-v1.jsonl` 由 Python 从固定源行计算，不注入模型 prompt。先构建 enterprise 数据和 gold，再运行 `python -m insightpilot.enterprise_evaluate`；评测会各跑主策略、无语义检索、无独立复核，并记录逐题 JSONL。provider 未报告价格时只给 token，不虚构费用。

## S8 轨迹与奖励

每次分析记录六个确定性阶段的状态，不保存问题或模型思维链。企业评测按独立 gold 计算指标、维度、数值和复核奖励；该分数用于诊断，不代表强化学习。设计与边界见 [S8 学习记录](docs/learning/stage-S8.md)。
