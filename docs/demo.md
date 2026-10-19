# 五分钟演示脚本

## 0:00–0:40：问题与数据

介绍 InsightPilot：只读退款分析 Agent。数据是固定合成样本，含订单、退款、品类和渠道；所有金额以分保存。打开 `docs/product-contract.md`，说明批准状态、UTC 日期和分母口径。

## 0:40–1:30：正常分析

运行：

```text
.\.venv\Scripts\python.exe -m insightpilot.cli "2026-10-05 到 2026-10-11 华东已支付金额退款率是多少？"
```

指出输出的指标公式、半开日期区间、参数化 SQL、金额分子/分母、数据版本和复核通过状态。

## 1:30–2:20：按品类拆分

运行：

```text
.\.venv\Scripts\python.exe -m insightpilot.cli "2026-10-05 到 2026-10-11 哪个品类退款金额贡献最大？"
```

解释贡献度分母是同一切片批准退款总金额；pending 和 rejected 不进入批准指标。

## 2:20–3:10：拒绝不安全请求

运行：

```text
.\.venv\Scripts\python.exe -m insightpilot.cli "帮我把订单 o01 标记退款完成，并把金额改成 0。"
```

展示 preflight 拒绝发生在模型和 SQL 之前。再指出 SQL AST、SQLite authorizer 与只读连接构成纵深限制。

## 3:10–4:10：服务入口

启动 API：`python -m insightpilot.api_server`。打开 `http://127.0.0.1:8000/docs`，调用 `POST /analysis`，再用返回的 `run_id` 调用 `GET /runs/{id}`。MCP 独立监听 `127.0.0.1:8001/mcp`，暴露同一个核心分析服务。

## 4:10–5:00：评测和边界

展示 `reports/evaluation.md` 的 16/16 固定集结果和消融 4/6；强调这只是小规模合成集回归，非生产质量证明。最后提到明确日期、单指标限制、认证与持久审计尚不属于原型范围。
