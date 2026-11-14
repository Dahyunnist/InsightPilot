# IP-4 模型对照调用授权摘要（离线编制）

## 状态

此摘要只核对本地冻结 manifest、成本 ledger、预注册和 prompt 构造代码。未访问 balance/API，未调用模型，也未读取私人数据。模型检索 on/off 对照仍 pending，本文不是调用授权。

## 预算口径

- 当前项目 cap 为 **$5.00**。`reports/ip4-model-preregistration.json` 与 `reports/domain-validation-model-preregistration.json` 的 **$4.00** 是 2026-10-23 的历史预注册值；同日 `reports/cost-audit-v9.json` 记载旧 cap `$4.00` 加已授权增额 `$1.00` 后为 `$5.00`。当前 `reports/model-experiment-cost-ledger.json` 与 `reports/ip4-v13-model-preregistration-20261114-r5.json` 都锁定 `$5.00`。
- 最新 ledger 余额为 **$4.54499575**，位于 `remaining_global_cap_after_ip4_v13_model_usd`。相对 `$5.00` cap，累计峰价估算与预留合计 **$0.45500425**。这不是供应商账单。
- 可由本地记录核对的已回 usage 运行峰价估算合计 **$0.25289910**。V13 最近 15 次请求没有 provider response 或 usage receipt；`$0.031932` 是按峰价记账的上界估算，确认实际费用为 `null`。更早一次未决暴露仍保留 **$0.04683360** reserve。旧账另有 **$0.12333955** prior reserve/unreconciled usage。合计 `$0.25289910 + $0.03193200 + $0.04683360 + $0.12333955 = $0.45500425`。不能把未决金额当成零。
- V13 两批无回执尝试的合计峰价暴露上界为 **$0.07876560**（`$0.031932 + $0.0468336`），已包含在最新 ledger 余额中；计算新预留时不要再次扣除。
- 新对照按 3,000 input tokens、1,024 output tokens计价；每次峰值 `$0.0021288`。两臂共 **44 个计划首轮请求**。planner 对单次分析最多调用两次，因此保守上限为 **88 次请求**，reserve **$0.18733440**。当前代码会在模型前拒绝 3 个写入/注入 guard；因此当前代码路径最多 76 次，但预留仍按 88 次计算。
- 若仅做 44 次首轮请求，费用上界为 `$0.09366720`；这不覆盖 planner 的结构化响应重试。扣除完整保守 reserve 后，预算余额为 **$4.35766135**。目前没有执行该 reserve，也没有新调用。

注意：V13 当前预注册里的 `max_api_calls=44` 指 **单臂** 22 题各最多两次 planner 调用；其 `planned_max_api_calls=22` 是单次通过计划。它不能用作两臂比较的最大调用数。旧 `$4.00` 与新 `$5.00` 也分别属于不同时间的 cap，不是同一版预注册冲突。

## 两臂与请求内容

冻结集是 22 道 synthetic 题：退款 12、营销 ROI 10；14 道可答、8 道 guard。44 个计划首轮请求覆盖全部题目：检索开启臂每题一次、检索关闭臂每题一次。该模型对照限定 SQLite 单后端；PostgreSQL 仍由 RulePlanner 与逐租户一致性回归覆盖。部分 guard 会在本地 preflight 停止，不会发给模型。

两臂共用固定 system prompt、JSON 输出格式、temperature 0 和 1,024 输出 token 上限。每个 user payload 只有三个字段：

- `question`：一条冻结的 synthetic 问题。
- `candidate_metrics`：检索开启时是静态指标定义列表；关闭时为空。
- `allowed_metric_ids`：开启时为检索结果 ID；关闭时为静态指标目录全部 ID。

实现锁：计划模型沿用 v13 预注册的 `deepseek-v4-flash`；请求结构由 [`insightpilot/planner.py`](../../insightpilot/planner.py) SHA-256 `4712d4ef7ed234ac8129453275e00cb0395d49d5c2d40597bb346e939a0f3489` 构造，静态指标定义来自 [`insightpilot/metrics.py`](../../insightpilot/metrics.py) SHA-256 `8b4f089d62b4be61006f4c4592477022c6c5fe0a0a3a14092094c78196adf026`。代码、prompt、模型别名或费率改变时，须重算预算并重新冻结。

请求**不含** case ID、gold、期待状态、SQL、查询结果、数据库行、租户数据或生产数据。14.5 MB SQLite fixture 只在本地用于确定性审计。Agent 可见输入与 gold 已分置于 [`benchmarks/ip4-offline-prep-v1/`](../../benchmarks/ip4-offline-prep-v1/) 的 `agent-visible/` 和 `scoring-private/`。

现有工具链会把同一模型计划离线重放到 verifier 开/关评分臂，不额外请求模型。Verifier-disabled 结果仍受只读 SQL policy 约束，不代表可绕开安全控制。

## 数据与锁定值

- Manifest：[`benchmarks/ip4-offline-prep-v1/manifest.json`](../../benchmarks/ip4-offline-prep-v1/manifest.json)
- 数据 fingerprint：`9171b5a859f09882038c38516e725122383f4e252e773a302324ba1b016da0e9`
- 重建 SQLite SHA-256：`654663f4176fc4c91aa723c8d83d5f1e462ce90e0967e8b5c76cb53df5fcf311`
- 退款 blind/gold SHA-256：`252993f200a1efb05ca0345ca36c233de3abc8f5e3907780e830b213969d1e7f` / `088bcb677b189fb4d66b2f40be8a190fab331fdffc1c6c7a70d6e7da93222ae4`
- 营销 ROI blind/gold SHA-256：`b1fc54513b86cf47d57b4d6e609c0bd4ca3e2acc9ee95d9021f6829328f16307` / `0c3dfb710f74820a9aa865b6e5df0395daab2550d9cc16a4431e9937ae8eb43b`

调用仍 pending，直到用户单独授权；供应商 transport 也须可用。不得把 `$0.18733440` 写成实际消费或已登记 reserve。
