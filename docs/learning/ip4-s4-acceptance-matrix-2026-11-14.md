# IP-4 S4 交付验收矩阵

核对日期：2026-11-14。范围：`E:\job\project_plans\02_InsightPilot_企业数据分析Agent.md` 的 S4 要求。此文档核对现有产物，不补造评测数据，也不重新调用模型。

## 计划交付

| 计划项 | 现有证据 | 状态与边界 |
|---|---|---|
| 评测报告 | `reports/current-benchmark/evaluation.md`、`reports/evaluation.md` | 已有。当前主报告记录冻结 30 例：严格通过 26/30；数值题 12/16；规则基线 30/30。另有历史 16 题报告，不能和当前集混报。 |
| 原始逐题结果 | `reports/current-benchmark/model-raw.jsonl`、`model-summary.json`、`baseline-raw.jsonl`、`baseline-summary.json`、`replay-summary.json`；`reports/ip4-retrieval-ablation-model.json` | 已有结构化逐题结果。原始模型结果保留指标、维度、地区、日期、数值、SQL verifier 和 provenance 字段；回放不发模型请求。 |
| 可重跑脚本 | README 中的 `python -m insightpilot.current_benchmark --mode baseline` 与 `--mode replay` | 已有。baseline 重跑规则基线；replay 校验既有模型结果、gold、哈希与账本，不重放模型请求。新模型调用需另行冻结题集并预留预算。 |
| README | `README.md`、`docs/quickstart-evaluation.md` | 已有启动、离线验证、评测复现和模型配置说明。 |
| 五分钟演示 | `docs/demo.md` | 已有 0:00–5:00 演示顺序，包含指标定义、可查看 SQL 与复核结果、澄清、拒绝、MCP 和审计记录。尚未形成录屏证据；计划只要求演示材料。 |
| 两条真实数字简历表述 | `docs/resume-bullets.md` | 已有四条项目 bullet 和 guard/模型答题的分母边界。建议简历只取两条，保留合成数据和小样本限定。 |

## 计划指标映射

| 指标 | 可复核结果 | 判定 |
|---|---|---|
| 答案数值正确率 | 当前 v4 数值题 12/16；已回答的数值结果 12/14。 | 有记录。两个分母回答不同问题，引用时要写清楚。 |
| 指标口径选择 | 当前逐题记录 `metric_ok`：14/16 数值题。 | 有结果；评测报告未单列该项，计算口径是 gold 中的指标 ID 是否匹配。 |
| SQL 可执行率 | SQL verifier 对 14/14 个模型已回答结果通过；另两道数值题被澄清。 | 有结果。不得写成全部 16 题均成功生成 SQL。 |
| 危险查询拦截率 | 当前 4/4 预期拒绝通过；13/13 guard 决策通过。另有 AST、只读连接和 authorizer 负例测试。 | 有相邻证据，但 13 个 guard 不等同危险 SQL 集。现有报告没有独立定义危险查询分母，因此该指标未单独测量。 |
| 证据完整率 | 对当前 30 题原始模型结果采用字段存在性定义：每个已回答结果需有 metric ID、定义版本、定义来源、lineage、SQL 与 verifier 结果。`model-raw.jsonl` 中 14/14 个已回答结果具备六项字段。 | **14/14（100%）元数据完整**。这是本次对既有逐题结果的确定性字段审计，不是来源真实性或逐 claim 支持度评测；两个被澄清题没有回答证据包，不计入分母。 |
| 模型请求时延 | 18 次 HTTP 请求；p50 2,442 ms，p95 5,161 ms。 | 有记录，来自当前 v4 主评测报告。 |
| 端到端时延 | 30 题混合 guard 结果 p50 1,209 ms，p95 4,529 ms。 | 有记录；不是纯模型时延。 |
| Token | 当前 v4 主评测 18,298；输入 8,781，输出 9,517。 | 有记录。 |
| 失败分类与诊断 | N06/N13 地区筛选误作分组；N09/N12 金额率检索到退款金额总和。修复后只对这四道已公开题做定向复核 4/4。 | 满足“至少一例真实诊断”。定向复核不是盲测，不并入 26/30；完整 30 题未在修复后重跑。 |
| 至少一个对比 | `reports/ip4-retrieval-ablation-model.json`：22 题检索开启 21/22（可回答 13/14，guard 8/8）；关闭 10/22（可回答 2/14，guard 8/8）；42 次 API 调用。 | 已有配对消融证据。样本仅 22 题，不能推断统计显著或任意问题上的普遍增益。 |

## 后续运行记录

`reports/ip4-v13-model-evaluation-r5.json` 记录了另一轮 22 题运行：状态 `partial_transport_blocked`，15 次请求均在传输阶段失败，0 个响应、0 token 用量；`$0.031932` 是峰价最坏预留估算，不是确认扣款。该轮不计入任何准确率，也不覆盖已完成的检索消融结果。不得把它写成模型回答失败或成功。

## 关口结论

S4 的评测、逐题结果、复跑入口、README、演示提纲、简历素材、失败诊断和检索消融均有文件证据。数值、指标、SQL、时延、token 和回答元数据完整率可复核。**危险查询拦截率仍没有独立题集分母；证据完整率只核字段存在，不核来源真实性或逐 claim 支持。**因此可以说明 S4 核心交付齐备，但不能声称计划中的每项量化指标全部完成。

完整测试与 IP-3 最终验收记录见 `docs/final-acceptance.md` 和 `reports/ip4-offline-prep-v1-audit.json`。截至核对时，全套测试 206 项通过；本次只新增本矩阵，未重跑模型或整套测试。
