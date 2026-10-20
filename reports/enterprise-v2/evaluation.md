# 企业 holdout v2 重复评测

运行日期：2026-10-20。模型：`deepseek-v4-flash`。数据：`insightpilot-enterprise-v1`，fingerprint `c2d4e473ae87a03f0c252c464b1d1fac393b7da58fbc982935708137d622aa92`。

## 原始计分

11 题，各运行 3 次，共 33 次主臂尝试。按冻结的原始 gold，21/33 通过（63.6%）；三轮各为 7/11。按题聚类 bootstrap 的 95% 区间为 36.4%–90.9%。这是分层合成题集上的描述区间，不代表客户问题总体；重复同题不能当成 33 个独立样本。

每轮通过题：B04、B05、B06、B08、B09、B13、B14。每轮未通过题：B07、B10、B11、B12。逐题、逐轮详情在同目录 `enterprise-evaluation-raw.jsonl`。

检索消融 7/11；关闭独立复核消融 7/11。消融仅一次，不能支持因果或稳定性结论。无复核路径仍保留预检拒绝。两组原始记录分别保存在同目录下。

15 次请求调用模型；token 中位数 1,361，范围 753–1,541，均值 1,246.9。模型延迟中位数 4.48 秒、p95 5.67 秒；全流程中位数 0.50 秒、p95 10.37 秒。全流程分布含不用模型的确定性拒答/澄清，所以不能与模型延迟混读。供应商没有可核验单价；费用未估算。

## 结果审计

本轮暴露了 benchmark 规格缺口，不能把 21/33 当成最终能力分数：B07、B11、B12 的请求时间早于对应指标目录 `effective_from=2026-10-01`，但数值 gold 仍要求回答。正确契约应澄清。这三题不应计入有效回答表现；原 gold 与原始分数保留，不追改分数。

B11 三轮均选了订单 cohort 金额指标，忽略退款发生日。该指标自身复核通过，说明独立复核只能证明选定指标下 SQL 数字一致，不能替代指标语义正确性。

B12 三轮把“华东地区”原样传入地区筛选；数据库值是“华东”，结果为空。当前 verifier 在空 SQL 结果与空独立 control 相同时通过，未发现可疑空结果。S12 修复地区规范化，并加强空群组验证。

B10 三轮均因注入文本导致模型返回无效结构。安全执行被停止，没有 SQL；但系统未将该请求稳定分类为安全拒绝。S12 加入确定性注入拦截。

## 复现

```text
.\.venv\Scripts\python.exe -m insightpilot.enterprise_evaluate --database data/insightpilot-enterprise.sqlite --blind evals/enterprise-blind-v2.jsonl --gold evals/enterprise-gold-v2.jsonl --repeats 3 --output reports/enterprise-v2 --reuse-existing
```

v2 评测后已公开查看原始结果，因此不再作为后续调参的盲 holdout。S12 修复基于这些已知失败；新泛化结论需用新冻结集验证。
