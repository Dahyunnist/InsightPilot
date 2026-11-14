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
| 危险查询拦截率 | `evals/sql-safety-v1.jsonl` 冻结 10 个危险 SQL 与 1 个允许查询；`reports/sql-safety-v1.json` 逐例记录结果。 | **10/10 拦截，1/1 允许查询通过**。由生产 AST 策略判定；危险 SQL 未执行。固定合成样本，不证明对抗绕过覆盖或生产拦截率。 |
| 证据完整率 | `scripts/audit_current_benchmark_evidence.py` 对 14 个已回答结果核对指标 ID、版本、公式、时间口径、退款状态、定义来源、来源标题、lineage、SQL 策略和 verifier。 | 六项字段均存在 14/14；来源字段和标题、公式/版本等目录元数据匹配 14/14；但 lineage 与当前指标目录仅 **0/14** 完全匹配。完整 bundle **0/14**，审计为 partial。历史逐题记录不可据字段存在性宣称证据完整；需用同一冻结评测版本重建或确认 lineage 契约。 |
| 模型请求时延 | 18 次 HTTP 请求；p50 2,442 ms，p95 5,161 ms。 | 有记录，来自当前 v4 主评测报告。 |
| 端到端时延 | 30 题混合 guard 结果 p50 1,209 ms，p95 4,529 ms。 | 有记录；不是纯模型时延。 |
| Token | 当前 v4 主评测 18,298；输入 8,781，输出 9,517。 | 有记录。 |
| 失败分类与诊断 | N06/N13 地区筛选误作分组；N09/N12 金额率检索到退款金额总和。修复后只对这四道已公开题做定向复核 4/4。 | 满足“至少一例真实诊断”。定向复核不是盲测，不并入 26/30；完整 30 题未在修复后重跑。 |
| 至少一个对比 | `reports/ip4-retrieval-ablation-model.json`：22 题检索开启 21/22（可回答 13/14，guard 8/8）；关闭 10/22（可回答 2/14，guard 8/8）；42 次 API 调用。 | 已有配对消融证据。样本仅 22 题，不能推断统计显著或任意问题上的普遍增益。 |

## 后续运行记录

`reports/ip4-v13-model-evaluation-r5.json` 记录了另一轮 22 题运行：状态 `partial_transport_blocked`，15 次请求均在传输阶段失败，0 个响应、0 token 用量；`$0.031932` 是峰价最坏预留估算，不是确认扣款。该轮不计入任何准确率，也不覆盖已完成的检索消融结果。不得把它写成模型回答失败或成功。

## 关口结论

S4 的评测、逐题结果、复跑入口、README、演示提纲、简历素材、失败诊断和检索消融均有文件证据。数值、指标、SQL、时延、token 和固定危险 SQL 拦截结果可复核。**历史回答虽包含完整字段，但 lineage 与当前目录不一致，严格证据审计为 0/14；危险 SQL 结果限于 10 个固定合成用例。**因此 S4 展示材料齐备，证据完整性关口仍未通过。

完整测试与 IP-3 最终验收记录见 `docs/final-acceptance.md` 和 `reports/ip4-offline-prep-v1-audit.json`。新增 SQL 评测前，上一轮完整套件 206 项通过。本次新增后，相关定向回归为 7 项、9 个子测试通过；完整套件重跑超过三分钟无输出后中断，新增后的全套结果未确认。没有重跑模型。
