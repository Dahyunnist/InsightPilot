# IP-4 v13：双领域独立冻结集

## 状态

v13 已构建、独立复核并预登记，**真实模型评测未运行**。不得把规则基线或 mocked planner 回归写成模型结果。v12 的 18/20 保持原样，不复用为 v13 成绩。

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

本阶段已完成执行器与零调用预检：模型、冻结题集、gold、运行时代码、Python 运行时、冻结提交祖先、数据库指纹、余额和待用预留均通过检查，预检模型调用数为 0。定向执行器测试 5/5 通过。下一阶段每题最多发送一次请求；保留全部原始结构化输出、usage、延迟和失败，不改 v13 gold 或评分器。若运行前任一锁检查失败，则不发送请求。
