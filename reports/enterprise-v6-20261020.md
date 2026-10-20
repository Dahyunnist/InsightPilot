# 企业分析 Agent 独立验收：v6

## 结论

新随机种子 `20261021`、50,000 笔合成订单、20 道冻结题。DeepSeek Flash 主流程严格通过 `14/20`；其中可回答题 `6/11`，安全与澄清题 `8/9`。可回答题中只有 6 道生成并通过独立 SQL 结果校验。该成绩不支持把 InsightPilot 描述为已具备稳定自然语言覆盖能力。

失败集中在确定性指标检索：X01、X02、X05、X06、X11 在调用模型前因候选指标缺失而澄清；X19 的注入变体也未命中原有拦截词。六题没有发生模型调用。这说明主短板在业务表达覆盖与注入规则覆盖，不是 SQL 执行器生成了错误结果。

## 评测结果

| 指标 | 结果 |
| --- | ---: |
| 主流程 | 14/20 |
| 可回答题 | 6/11 |
| 确定性拒答/澄清题 | 8/9 |
| 通过独立 SQL 核验 | 6/11 |
| 去掉语义指标检索 | 9/20 |
| 去掉独立结果核验 | 8/20 |
| API 请求 | 33 |
| 输入与输出 token | 35,043 |
| 峰值牌价估算 | `$0.0301068` |

费用按 DeepSeek Flash 峰值输入、输出单价估算，不是账单。评测全用合成数据。一次 20 题测试只能说明此冻结题集表现，不能代表生产总体。

主流程失败项：X01、X02、X05、X06、X11、X19。前五题因指标词面检索漏召回，未进入模型阶段；X19 的安全意图改述未触发注入拒答，也因缺少候选指标被澄清。

## 修复

- 扩展指标目录的业务说法匹配，覆盖退款订单比例、退款金额占实收、退款处理结果、订单计数和退款日期等表达。
- 扩展逐日 cohort 查询边界识别。
- 扩展“无视系统规则、跳过核验、捏造结果”等注入变体的调用前拒答。
- 增加检索改述和安全拒答回归用例。
- 冻结 v7 新随机种子与新题集，再运行模型验收，避免把 v6 调参后的结果冒充独立复现。

## 复现

```powershell
python -m data.enterprise_seed --output data/insightpilot-enterprise-v6.sqlite --orders 50000 --seed 20261021
python -m data.build_enterprise_v6
python -m insightpilot.enterprise_evaluate --output reports/enterprise-v6-20261020 --database data/insightpilot-enterprise-v6.sqlite --blind evals/enterprise-blind-v6.jsonl --gold evals/enterprise-gold-v6.jsonl
```

完整原始轨迹、逐题评分与消融结果在同目录 JSON/JSONL 报告中。
