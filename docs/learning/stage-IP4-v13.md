# IP-4 v13：双领域独立冻结集

## 状态

v13 已构建、独立复核并预登记，**真实模型评测未完成**。2026-11-14 r5 记录 15 次请求尝试、0 provider response，usage receipt 与确认费用未知；未生成可评分模型结果。不得把规则基线或 mocked planner 回归写成模型结果。v12 的 18/20 保持原样，不复用为 v13 成绩。

## 冻结集

- 22 道新题：退款 12、营销净 ROI 10；14 道可答、8 道澄清/拒答。
- 零售 seed `20261111`、营销 seed `20261112`；数据库 fingerprint `9171b5a859f09882038c38516e725122383f4e252e773a302324ba1b016da0e9`。
- 规范化精确题面重叠 0；此检查不排除语义相似。blind 只有 `case_id` 与 `question`。
- 退款 gold 用独立 Python 源行 oracle，并与只读 SQL 交叉核对。营销 gold 用独立 Python 事件账本与第二 Python oracle，再与 SQL 交叉核对。
- 跨月/跨年题 7 道。营销 cohort 中含 57 笔已记录退款撤销；涉及重复 business event 的营销题 6 道。4 道营销题含 27 个 `NULL` ROI 组；另有 498 个无 campaign attribution 订单，作为“未归因”组参与 gold。5 道澄清 guard 覆盖退款口径、多指标、缺日期、地区费用不可分摊、未定义毛 ROI。

冻结文件：`evals/ip4-v13-blind.jsonl`、`evals/ip4-v13-gold.jsonl`、`evals/ip4-v13-manifest.json`。blind SHA-256：`c7c8ecdd498cdaae44e7744457b54a2ade850c47e0340f5d3acb66c4f33bfa66`；gold SHA-256：`f10959a4be664a69f39d6ff3079b6d3d93705e310e09be5abdc6c2b0fe335df2`；manifest SHA-256：`c7b7875dcf01380fc6f9da32c7ad8593f9f19f5ca5fc38f32a3521236e349932`。gold seal SHA-256：`964b3b356a298e7ba5fb837c12a2e267d850d4ee475d8b53b89fc6b56df09bf3`。

## 离线验证

冻结前 prompt 与解析测试用 mock HTTP 响应检查生产 planner；不发送网络请求。14 道可答题全部能检索到 gold 指标并解析 gold 日期、地区和维度。生产 prompt 字节数受 `2800` 上限约束，留在登记的 3000 输入 token 预算内。

RulePlanner 基线为 15/22：可答 8/14、guard 7/8。四个错误拒答是 R02、R05、R06、R07；错误接受是 M09。逐题结果在 `reports/ip4-v13-rule-baseline.json`。这是规则基线，不是模型评测。

定向测试 19/19 通过，覆盖新 seed、freeze hashes、双域 gold 交叉核对、边界题覆盖、所有可答题的指标/日期解析、prompt 字节上限与预登记封存。

## 费用预登记

`reports/ip4-v13-gold-seal.json` 与 `reports/ip4-v13-model-preregistration.json` 锁定题集、gold、manifest、scorer、planner、runner、baseline、构建器及运行时代码 SHA。预登记状态 `frozen_pre_model`，模型调用数 0。锁文件 SHA-256：`3b95b8b4cca74f1c93799d1916cf991933d79c539b4912c152863e5fbce05346`；scorer SHA-256：`0bdfeb6afe819305aecb8248e007f7b45370471a32cec1a46f620f82cb0000c4`。执行计划为每题一次，共最多 22 次；单请求输入上限 3000 tokens、输出上限 1024 tokens。费用仍按最多 44 次锁定，覆盖重试上界。

按 DeepSeek Flash 峰价未缓存输入 `$0.30/百万 tokens`、输出 `$1.20/百万 tokens`，完整最坏预留 `$0.0936672`，保守按 ¥7.50/USD 换算为 ¥0.702504。官方价表：[DeepSeek API 定价](https://api-docs.deepseek.com/quick_start/pricing/)。内部 `$5` cap 评测前余 `$4.62376135`，预留后余 `$4.53009415`。本轮只读余额检查为 ¥9.33；足以覆盖预留。费用账本已登记 pending reserve，没有记作实际花费。

执行器与零调用预检已完成：模型、冻结题集、gold、运行时代码、Python 运行时、冻结提交祖先、数据库指纹、余额和待用预留均通过检查；冻结集 22 题，额度余额 ¥9.33，最坏费用预留 ¥0.702504。定向执行器测试 5/5 通过。随后真实请求被自动审查拦截，理由为无法确认本地冻结题集可发送给第三方模型服务。模型调用仍为 0，费用仍为 pending reserve；没有生成模型结果。不得将 RulePlanner 基线表述为模型成绩。继续前需明确授权把这 22 道合成题发送至 DeepSeek；获批后仍按每题一次运行，并保留全部原始输出、usage、延迟和失败，不改 v13 gold 或评分器。

## 2026-11-14 预算口径更正

本节预注册的 `max_api_calls=44` 是 v13 单臂 22 题、每题最多两次 planner 调用；`planned_max_api_calls=22` 是单次通过。它不是检索 on/off 双臂的总上限。双臂对照计划首轮 44 次，含 planner 最多两次调用后保守上限 88 次；reserve `$0.1873344`。完整 cap、ledger、失败请求、输入字段与 synthetic 数据清单见[离线授权摘要](ip4-model-comparison-authorization-2026-11-14.md)。此前 `$4` cap 属 2026-10-23 增额前记录，当前 ledger 与本节 r5 预注册 cap 为 `$5`。调用仍 pending；摘要未访问 balance/API。

## 2026-11-14 Gold 与 Agent 输入隔离离线回归

盘点确认 IP-2 已有营销 ROI 与库存周转业务域，因此不重复新增指标或合成数据。为 IP-4 v13 补充 `tests.test_ip4_v13_blind_payload_contract`：静态检查执行器只把盲题 `question` 交给 `InsightPilot.analyze`；在 mock HTTP 的生产 planner 请求中逐题检查 user payload 仅有 `question`、`candidate_metrics`、`allowed_metric_ids`，不含 case ID、gold 判定状态或期望值。测试读取现有合成冻结题与 gold，但网络调用全部 mock，模型调用为 0；冻结 blind/gold、manifest、评分器与预注册没有变更。此项验证当前代码路径的隔离契约，不证明第三方模型服务端或未记录的外部上下文隔离。

## 2026-11-15 确定性验收审计

新增 `scripts/audit_ip4_v13_acceptance.py`、`tests/test_ip4_v13_acceptance_audit.py` 与 `reports/ip4-v13-acceptance-audit-2026-11-15.json`。审计只读冻结文件，输出哈希、字段形状和计数，不输出题面，不改 blind/gold。v13 仍为 22 题：退款 12、营销 ROI 10；14 可答、5 澄清、3 拒答；5 道歧义 guard。blind/gold case ID 对齐，盲题 schema 仅 `case_id` 与 `question`。manifest、gold seal、预登记锁和 SQLite RulePlanner baseline 哈希一致。SQLite baseline 15/22 是规则结果；gold builder 的源行/事件 oracle 与只读 SQL 一致性由 `tests/test_ip4_v13_holdout.py` 覆盖。

定向离线回归 28 passed、41 subtests passed。扩展 dry-run 锁状态测试，覆盖 `partial_transport_blocked`、`blocked_by_review`、`blocked_pre_model_balance_unverified`、`blocked`，逐项确认不读 blind/gold、不联网、0 请求；冻结审计命令会写出 `blocked` 报告并返回退出码 2。Windows 临时路径短/长名比较也改为先 resolve 再比较，回归通过。

## 2026-11-15 已授权评测预检

按所有者指示仅访问供应商只读余额端点，未发送模型请求。当前余额 ¥9.08；按本次最多 88 次请求预留 `$0.1873344`，另保留历史未确认暴露 `$0.0468336`，合计需 ¥1.75626；余额通过。账本总 cap `$5`，累计剩余 `$4.50483355`；新授权预算与历史不确定预留合计 `$0.234168`，仍在 cap 内。历史 v13 有 15 次请求尝试、0 次 provider response；确认实际费用仍为 null，保留峰价估算 `$0.031932` 与不确定预留。

执行锁未通过：blind/gold/manifest/seal 哈希均匹配；但 `insightpilot/planner.py`、`scripts/run_ip4_v13_model_blind.py`、`insightpilot/query_engine.py` 与锁定哈希不一致。planner 新增币种/毛 ROI 的确定性澄清，会改变冻结 M09 的执行路径，并使历史 RulePlanner 基线不再与当前运行时同版；runner 更新运行编号、报告覆盖保护、预检与余额证据路径；query engine 变更仅落在 PostgreSQL 身份读取超时分支，但整体文件哈希已变化。离线门禁回归通过 18 项、27 子断言；其中 payload allowlist 专项为 2 项、22 子断言。旧锁会在余额检查和模型请求前因代码哈希不符而 fail closed。

这些变化不只是本地审计/测试，因此不生成新锁、不重跑模型。详情见 `reports/ip4-v13-authorized-preflight-2026-11-15.json`。新授权仍待 planner 行为差异复审、当前基线重新确认和锁版本审阅；没有触碰冻结题或 gold，也没有使用 v14。

## 2026-11-15 用量导出与账本核对

仅按 `api_key_name=insightpilot` 汇总 amount 导出：32 行，覆盖 2026-10-19 00:00 至 2026-11-15 00:00（+08:00）；文件名起始日 10-17 至 10-18 无匹配数据。请求数 538；输入 cache-hit 83,781 tokens、cache-miss 168,202 tokens、输出 312,926 tokens。`amount×price` 合计 1.84931692，但该文件没有价格币种字段，不能记作已确认 CNY 费用。

cost 导出没有 `api_key_name` 列，只有禁止读取的 `user_id` 标识；未读取行值，故无法将其中 cost/CNY 账单归属到 `insightpilot`。这使每 key 的 provider CNY 实际费用无法与本地 ledger 精确核对。余额与预留金额仍充足，但账单归属门禁未通过。没有复制 CSV，也没有模型请求。详见同一预检报告。

失败分类审计确认历史 r5 为 15 次请求尝试、0 provider response；15 条均标 `not_scored_transport_error`，score 为 null，不计作模型失败分或成功分。runner 另将错误澄清、错误拒答、数值错、guard 错误响应、拒答题被接受分开记录；本次没有可评分模型响应。预算测试覆盖逐请求先预留、调用数/成本超限即停；新增 dry-run 回归验证超出 `$0.1873344` reserve 上限时在请求前拒绝。离线预检状态仍为 `blocked_pre_model_balance_unverified`，余额未查询。模型 runner 的 22 次计划、单题单次上限、$5 总 cap 和已登记预留硬门禁通过静态/离线审查。

**阻塞项：**本机 Docker Engine 权限不可用。现有 PostgreSQL 报告仅有旧 12 题套件且无 v13 hash，不能证明这份 22 题 v13 gold 的 PostgreSQL 一致性或双租户逐题覆盖；审计明确标记 `postgres_22_case_v13_gold_parity` blocked。通用 API 跨租户读取/取消测试存在，但不能替代 v13 双租户全套验收。v14 仍是候选，未运行模型；所有者审题与实时余额预检未完成。审计结论为 blocked，不得宣称 IP-4 双后端验收完成。
