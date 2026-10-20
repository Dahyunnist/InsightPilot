# Enterprise 合成基准报告

评测日期：2026-10-20。使用固定 `insightpilot-enterprise-v1`、当前 `.env` 模型配置。

数据 fingerprint：`c2d4e473ae87a03f0c252c464b1d1fac393b7da58fbc982935708137d622aa92`。

## 数据与质量

- 50,000 订单、10,454 退款记录、60,429 campaign touch、20,000 合成客户、600 商品、20 个 campaign。
- orphan refund：0；退款权重和异常：0；越权重退款单：0。
- 超过 1 天才入库的退款：9,757/10,454。
- 缺失活动归因订单：498/50,000；覆盖率 99.004%。缺失订单的退款进入“未归因”分组，不丢弃金额。
- customers 表故意含 synthetic email/phone decoy；SQL table/column allowlist 不允许读取。

## Prompt-blind holdout

问题和 gold 分文件保存。Python 聚合器读取固定源行生成 gold；gold 不发给 planner。3 题分别覆盖月退款率、地区订单率、多触点 campaign 归因。

- 主策略：3/3 正确指标、维度、数值和独立复核。
- SQL 执行且独立复核：3/3。
- 请求耗时 p50 4,137.805 ms、p95 9,484.855 ms；provider usage 平均 1,138.33 tokens/正常回答。
- 无语义检索消融：1/3 全面通过。
- 无独立复核消融：2/3 数值正确；该臂 SQL verified 为 0/3（复核被人为关闭）。
- 价格未配置，所以不估费用。逐例 SQL、结果、usage 和失败计划见各 `enterprise-*.jsonl`。

S7 另在 50,000 单库实跑全年 campaign 归因：21 组，分析与独立复核总计 519.462 ms，一次本机测量，不是 SLA。

## 边界

样本只有 3 个 holdout 问题，模型、提示、供应商与本机配置都单一。50,000 单是确定性合成数据，不代表企业流量分布。迟到数据只在质量报告中计数；当前不支持历史快照/as-of 归因。消融的差异仅作小样本诊断，不可当显著性结论。

复现：

```text
.\.venv\Scripts\python.exe -m data.enterprise_seed
.\.venv\Scripts\python.exe -m data.build_enterprise_gold
.\.venv\Scripts\python.exe -m insightpilot.enterprise_evaluate --database data/insightpilot-enterprise.sqlite --output reports
```
