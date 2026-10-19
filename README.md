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

API 绑定 `127.0.0.1:8000`，提供 `POST /analysis`、`GET /runs/{id}`、`GET /health`。MCP 使用独立 loopback 端口 `127.0.0.1:8001/mcp`，暴露 `analyze_refund` 工具和指标/契约资源。两种入口与 CLI 都调用同一个 `InsightPilot` 服务。API 运行记录只保存在进程内，最多保留 100 条。

API 与 MCP 服务需在两个终端分别启动：

```text
.\.venv\Scripts\python.exe -m insightpilot.api_server
```

```text
.\.venv\Scripts\python.exe -m insightpilot.mcp_server
```

## S4 黄金集评测

`evals/cases.jsonl` 固定 12 个正常分析、2 个澄清和 2 个拒绝用例。运行上方评测命令会逐条调用 `.env` 配置的模型，并生成 `reports/evaluation-raw.jsonl`、`reports/retrieval-ablation-raw.jsonl`、`reports/evaluation-summary.json`。原始结果包含 SQL 与查询证据；只在本地保存，不应上传客户数据或密钥。当前结果见 [评测报告](reports/evaluation.md)。演示流程和简历描述见 [五分钟演示](docs/demo.md) 与 [简历要点](docs/resume-bullets.md)。
