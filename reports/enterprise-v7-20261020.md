# 企业分析 Agent 独立验收：v7

## 结论

v7 使用新随机种子 `20261022`、50,000 笔合成订单和另一套冻结问题。DeepSeek Flash 主流程严格通过 `19/20`，11 道预设可回答题中通过 `10/11`，9 道安全/澄清题通过 `9/9`。独立 SQL 核验通过 `10/11`。

唯一严格失败是 X01。问题写“出现过退款”，没有说明仅统计 `approved` 状态；产品指标只定义了“批准退款订单率”。模型两次调用后要求确认口径。这是合理的谨慎澄清，但冻结金标准把问题列为可回答题，因此保留严格失败。人工审阅可把清晰且无歧义的 10 道可回答题视作 `10/10`，但报告主分数不做改写。

## 指标

| 指标 | 结果 |
| --- | ---: |
| 严格主流程 | 19/20 |
| 预设可回答题 | 10/11 |
| 安全与澄清题 | 9/9 |
| 独立 SQL 结果核验 | 10/11 |
| 去掉语义指标检索 | 14/20 |
| 去掉独立结果核验 | 9/20 |
| API 请求（主流程与消融） | 43 |
| 总 token | 44,269 |
| 峰值牌价估算 | `$0.0404901` |
| 预算上限 | `$0.10` |

题目级 Wilson 95% 区间：总体 `[0.7639, 0.9911]`；可回答题 `[0.6226, 0.9838]`。样本刻意覆盖已知支持范围与边界，不是线上随机样本。区间只表达本题集的小样本不确定性，不能外推生产成功率。

## 证据解释

修复后，离线路由检查对 11 道可回答题全部召回了金标准指标。真实模型主流程仍有 X01 的语义歧义，说明“召回正确”不等于“问题口径充分”。

消融结果表明，关闭指标检索后严格通过降到 14/20；关闭独立 verifier 后降到 9/20。它们支持检索约束和结果复核的工程价值，但单次小样本结果不足以证明普遍增益。

全部数据均为合成数据；估价按 DeepSeek Flash 峰值未缓存输入 `$0.30/M`、输出 `$1.20/M` 计算，非账单。完整逐题结果、请求用量和失败轨迹见本目录 JSON/JSONL 文件。

## 复现

```powershell
python -m data.enterprise_seed --output data/insightpilot-enterprise-v7.sqlite --orders 50000 --seed 20261022
python -m data.build_enterprise_v7
python -m insightpilot.enterprise_evaluate --output reports/enterprise-v7-20261020 --database data/insightpilot-enterprise-v7.sqlite --blind evals/enterprise-blind-v7.jsonl --gold evals/enterprise-gold-v7.jsonl
```
