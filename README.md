# InsightPilot

本地、只读的退款经营分析 Agent 原型。S1 当前支持批准退款订单率、批准退款金额率、品类/渠道退款金额贡献度；要求明确日历日期，一次分析一个指标。

## 启动

需要 Python 3.11+。SQL 策略使用 SQLGlot。根目录 `.env` 需包含 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`；可选 `LLM_TIMEOUT_SECONDS`、`LLM_MAX_COMPLETION_TOKENS`。`.env` 已加入忽略列表，不应提交密钥。

```text
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe data/seed.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m insightpilot.cli "2026-10-05 到 2026-10-11 华东金额退款率是多少？"
.\.venv\Scripts\python.exe -m insightpilot.cli "2026-10-05 到 2026-10-11 各品类批准退款金额贡献度是多少？"
```

回答带指标公式、日期区间、数据版本、参数化 SQL、结果行和独立汇总复核。所有 SQL 来自本地模板；模型只抽取结构化计划。SQLGlot AST 白名单、SQLite authorizer、只读 URI、`query_only`、2 秒执行上限、250,000 条 SQLite 虚拟机指令预算与 100 行上限共同约束查询。

当前会澄清相对日期、模糊退款率和多指标问题；会拒绝写请求与个人信息请求。固定数据只有 30 单和 32 条退款，不能代表真实企业数据。扫描预算按 SQLite 虚拟机指令计，不是物理磁盘字节数。
