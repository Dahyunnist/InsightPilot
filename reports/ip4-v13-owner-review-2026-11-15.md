# IP-4 v13 运行锁审阅摘要

**结论：保持阻塞；没有新建运行锁、没有重封、没有发模型请求。** 当前余额与总预算有余量，但当前代码不等于 v13 r5 锁定运行时；planner 变更会改变 M09 的预模型路径。需先审阅新版运行时和基线，再决定是否为指定 revision 重新授权。

## M09 的确切影响

M09 题目要求同一时间段内分别提供营销净 ROI 与毛 ROI。v13 gold 期望 `clarify`；冻结 RulePlanner baseline 报告把 M09 列为 `false_accepts`，即实际接受并作答。

旧 planner 哈希为 `4712d4ef7ed234ac8129453275e00cb0395d49d5c2d40597bb346e939a0f3489`；当前为 `60f56f057f5226d26e7359aff135c723dcf6b093ecc65f9bae3ff5b619bc9c33`。新增 preflight 识别“毛 ROI”，在 `_complete` 之前返回“当前目录只定义营销净 ROI；毛 ROI 口径尚未定义。”本地 fake-completion 回归确认 M09 返回澄清，模型调用数为 0。

因此当前模型臂会跳过 M09 的 provider 请求，而历史 baseline 对 M09 是错误接受。该变化影响实际题目覆盖和对照可比性；不能把它当成纯审计改动后直接重封。

## 哪些锁定内容变化

| 项目 | r5 锁定值 | 当前值 | 影响 |
|---|---|---|---|
| Planner 全文件 | `4712d4ef…f3489` | `60f56f05…bc9c33` | M09 执行路径变化 |
| 模型 runner | `5db06cae…80733a` | `a2ec91fc…b50449` | 运行编号、报告覆盖保护、预检/余额证据流程变化 |
| Query engine | `b23602ec…63d63` | `052a758e…bc355` | PostgreSQL 身份读取超时分支变化；v13 当前锁定 backend 是 SQLite |

- `_complete` 中的 prompt 与请求 payload 构造方法哈希前后相同：`0d508b5440f9e548a2c74d1fce2c6d14bbe8a487276563976b717d464f373052`。
- Scorer 哈希前后相同：`d545910893f47527bde115faa7af0967869bce7c416d99802650077f76692ef6`。
- payload allowlist 回归通过：请求只含 `question`、`candidate_metrics`、`allowed_metric_ids`；不含 gold、SQL、查询结果、数据行或私人字段。
- 发送字段与 prompt/scorer 没变；M09 会被 preflight 拦下，因此有效输入题集会少一道。
- Frozen blind、gold、manifest 与数据 fingerprint 未变：
  - blind SHA-256：`c7c8ecdd498cdaae44e7744457b54a2ade850c47e0340f5d3acb66c4f33bfa66`
  - gold SHA-256：`f10959a4be664a69f39d6ff3079b6d3d93705e310e09be5abdc6c2b0fe335df2`
  - manifest SHA-256：`c7b7875dcf01380fc6f9da32c7ad8593f9f19f5ca5fc38f32a3521236e349932`
  - data fingerprint：`9171b5a859f09882038c38516e725122383f4e252e773a302324ba1b016da0e9`

## 授权上限与余额证据

用户已授权 v13 最多 88 次请求、最坏预留 `$0.1873344`，总 cap `$5`。加上历史未确认暴露预留 `$0.0468336`，合计 `$0.234168`；账本当时剩余 `$4.50483355`，预算足够。余额只读查询于 2026-11-15 05:36:37 UTC 返回 ¥9.08；按保守汇率 7.5 CNY/USD，含历史预留需 ¥1.75626。

历史 r5 有 15 次请求尝试、0 次 provider response；该 run 确认实际费用仍为 null。两个 CSV 文件名都声明 2026-10-17 至 2026-11-15。amount 中 `insightpilot` key 有 538 次请求、32 行、6 个不同 start date；匹配区间从 2026-10-19 开始并于 2026-11-15 结束。这不代表 v13 单独请求数。排除 `request_count` 后，按该 key 汇总 `price × amount` 得 ¥1.84931692；amount 全表同算法 ¥10.91277486，与 cost 全表 CNY 总额精确相等。因此 ¥1.84931692 是经全表对账的按 key 派生费用。cost CSV 没有 `api_key_name`，不能直接按 key 列账。[DeepSeek API FAQ](https://api-docs.deepseek.com/faq/) 未说明零用量日期是否省略；六个有行日期不能证明周期不完整，也不能证明其余日期用量为零。Provider CNY 总额与本地 USD 调用账本保持分开，不能直接相加；本地账本是运行尝试/预算暴露记录，不是第二张供应商账单。逐请求归属和汇率未核清前，合并会重复计费。

## v13 与 v14

| 版本 | 状态与题集 | 标识 |
|---|---|---|
| v13 | 冻结集；退款 12、营销 ROI 10；14 可答、8 guard | blind `c7c8ecdd…3bfa66`；gold `f10959a4…335df2` |
| v14 | 独立候选；退款 11、营销 ROI 11；每域 7 可答、4 guard；待所有者审题 | blind `aecba9db…e4e080`；gold `8c01a8cb…e9558c3`；尚未授权模型运行 |

本次仅审 v13。v14 的 blind、gold、数据库和余额均未用于本次预算或执行。

## 版本化锁与回归

Runner 可通过 `--preregistration` 显式选择版本化锁。回归指定 `reports/ip4-v13-model-preregistration-20261114-r5.json`，确认 hash 不符时先于余额查询和模型调用 fail closed，且 r5 锁原样保留。

r5 本身只登记最多 44 次 API call、计划 22 次、预留 `$0.0936672`；小于当前授权上限 88 次。未生成 r6，也未静默扩展旧锁。版本化锁、fail-closed、prompt/scorer 哈希稳定性、payload allowlist 和 M09 零调用回归共 16 passed、27 subtests passed。详细差异见 `reports/ip4-v13-authorized-preflight-2026-11-15.json`。

## 需要所有者确认

后续运行前需明确：是否将当前 planner 的 M09 preflight 纳入评测；若纳入，先用当前运行时重跑固定规则基线并签新版本锁；该锁是 22 次单轮还是授权上限 88 次；cost CSV 缺少 key 维度时是否接受现有余额及预留作为费用门禁。未获这些决定前保持阻塞。
