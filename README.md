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

S4 的 16/16 是历史结果：12 次正常回答调用模型；2 次澄清与 2 次拒绝由确定性 guard 处理。旧简历和演示文本曾把 16 次都写成正常回答，本次已更正。

`evals/cases.jsonl` 固定 12 个正常分析、2 个澄清和 2 个拒绝用例。运行上方评测命令会逐条调用 `.env` 配置的模型，并生成 `reports/evaluation-raw.jsonl`、`reports/retrieval-ablation-raw.jsonl`、`reports/evaluation-summary.json`。原始结果包含 SQL 与查询证据；只在本地保存，不应上传客户数据或密钥。当前结果见 [评测报告](reports/evaluation.md)。演示流程和简历描述见 [五分钟演示](docs/demo.md) 与 [简历要点](docs/resume-bullets.md)。

## S5 版本化语义目录

各指标现在提供定义版本、生效日、粒度、来源表与字段血缘、适用维度、访问范围和来源文档。MCP `metrics://refund-catalog` 返回同一版本化目录。规划器把检索到的指标元数据按需注入；执行前校验维度符合该指标语义契约。未定义的 `refund_event_rate` 保持禁用，模糊“退款率”继续澄清。API/MCP 的 Bearer scope 与地区约束见 S6。

## S7 企业合成基准与归因

原 S0 固定夹具保持不变。新增 `data/enterprise_seed.py` 生成确定性 50,000 单、约 10,000 笔退款的扩展库，含 4 地区、4 渠道、6 品类、客户分层、20 活动、多触点权重、迟到入库退款和约 1% 缺失归因。运行 `python -m data.enterprise_seed` 后，以 `INSIGHTPILOT_DATABASE_PATH=data/insightpilot-enterprise.sqlite` 切换数据。`data.enterprise_seed.quality_report()` 输出 orphan、超额退款、权重错误、迟到行和归因覆盖率。新活动归因需 enterprise 数据版本；S0 上会澄清，不会发 SQL。表粒度和字段约束见 [enterprise 数据契约](docs/enterprise-data-contract.md)。

新增 `region`、`month_utc` 分组和带权多触点活动归因；组级复核用单独查询路径逐组核对。回答还带确定性 `visualization` 规范，可由 API/MCP 客户端渲染为月趋势线或分类柱图。

历史 `evals/enterprise-blind-v1.jsonl` 只有 3 道题，结果保留供审计，不作为泛化证据。S10 新增 11 道未运行过模型的 `enterprise-blind-v2`，gold 由独立 Python 逻辑生成，题集 manifest 记录哈希和精确题泄漏检查。评测报告与边界见 [S10 学习记录](docs/learning/stage-S10.md)。

S11 对 v2 每题运行三轮真实模型评测；原始 21/33 结果、逐题波动、bootstrap 区间、token 与延迟分布见 [重复评测报告](reports/enterprise-v2/evaluation.md)。事后审计发现三道题的日期早于指标生效日，原始 gold/分数保留并明确标为无效口径样本；v2 不再作盲 holdout。

S12 根据 v2 暴露的问题修复生效日期校验、地区字面值规范化和注入拦截。API/MCP 认证失败、脱敏审计、并发拒绝、取消和独立进程重启恢复均有自动化证据。新固定路径测试见 [S12 学习记录](docs/learning/stage-S12.md)；v2 已暴露，不用它声称修复后模型泛化。

S13 已冻结修复后 v3 holdout：11 题，4 道需数值回答，均核对指标生效日期；独立 Python gold、数据指纹与题目哈希见 [v3 manifest](evals/enterprise-holdout-manifest-v3.json)。冻结与静态验证记录见 [S13 学习记录](docs/learning/stage-S13.md)。模型运行在冻结提交之后进行。

S14 v3 三轮结果为 33/33，含 12 次正确数值模型回答；题目级 Wilson 区间与小样本边界、两组消融和 24 次实际模型调用记录见 [v3 评测报告](reports/enterprise-v3/evaluation.md)。这只支持当前合成 holdout，不支持客户总体泛化、生产竞争力或 Agentic-RL。

当前统一基准为 v4：30 道独立合成题、16 道数值回答、13 道确定性 guard、1 道模型澄清；独立 Python gold、同题规则基线和真实模型逐题结果见 [当前评测](reports/current-benchmark/evaluation.md)。运行步骤见 [快速启动与复现](docs/quickstart-evaluation.md)。S4、v2、v3 保留为历史记录，不与 v4 混算。

## S8 轨迹与奖励

每次分析记录六个确定性阶段的状态，不保存问题或模型思维链。企业评测按独立 gold 计算指标、维度、数值和复核奖励；该分数用于诊断，不代表强化学习。设计与边界见 [S8 学习记录](docs/learning/stage-S8.md)。

## S9 冻结集策略实验

运行 `.venv/Scripts/python -m insightpilot.strategy_experiment`，从配对消融估计检索动作奖励，用 B03 冻结评估。当前训练偏向语义检索，但冻结集没有胜过固定检索基线；不支持 Agentic-RL 声明。详见 [S9 学习记录](docs/learning/stage-S9.md) 和 [策略报告](reports/strategy-experiment.json)。
