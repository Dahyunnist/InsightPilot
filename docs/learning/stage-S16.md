# 阶段 S16：补齐规划边界并验收 v5

## 这次解决什么

v5 合成评测覆盖 7 种退款指标、日期区间、品类/渠道/月/活动维度、空时间窗、歧义请求和安全拒绝。审计时发现两种容易静默出错的提问：要求按下单日期逐日汇总，但目录没有这个维度；一次要求两个不同退款率指标。规划器现在在模型调用前澄清这两种请求。

## 请求经过哪些层

```text
用户问题
  -> 确定性范围检查
  -> 指标目录检索与受限计划
  -> 参数化 SQL 编译
  -> SQLite 只读执行
  -> 独立 verifier 复算并检查结果
```

模型只填充指标、日期、地区和维度计划，不生成 SQL。SQL 编译器只访问允许的表列；只读连接、authorizer、查询超时与行数限制构成执行边界。verifier 从源数据独立汇总结果，核对组和值。规划不明确时不执行查询。

## 评测如何保持独立

`data/build_enterprise_v5.py` 直接读取合成订单、退款和活动归因源表，用 Python 计算金标准。它不调用模型，也不复用生产 SQL 编译器。测试再把 11 道可回答题的 gold 与只读 SQL 执行结果逐题比较，检查 oracle 和执行路径是否一致。

题集在模型评测前提交冻结。manifest 记录数据指纹、题目与 gold 哈希，并检查历史题目的完全相同文本。这个检查不能排除语义相似。v5 只有 20 题，而且是刻意设计的合成问题。

## 模型结果与失败解释

默认配置把每次模型输出限制为 1,024 token。该配置下严格通过 20/20：11 道数值题全部通过 gold 和 SQL verifier，9 道澄清/拒绝题全部符合预期。区间很宽：总题 Wilson 95% 区间 83.89%–100%，数值题区间 74.12%–100%。

第一轮诊断把输出上限临时设成 512 token。X06、X09、X11 的两次响应都耗尽上限，返回无效 JSON；运行器将它们作为失败记录。恢复默认 1,024 后重跑同一冻结集，20/20 通过。两轮原始记录都保留；第二轮不是新样本，不能算作独立复现。没有为提高分数修改模型提示、题目或代码。

同一复测的消融结果：关闭语义指标检索为 13/20；关闭独立 verifier 为 9/20。后者的数值结果不再达到完整验收条件。它们支持“这些组件在本题集有作用”，不代表组件能提升所有业务集的成功率。

## 测试和复现

- 全套自动化测试：66 项通过。
- 定向测试检查双指标请求和不支持的订单日期分组会在调用模型前澄清。
- v5 builder 测试检查 20 个唯一案例、独立 gold、哈希和 11 个 SQL 对照。
- 主模型调用与两组消融结果都保留在 `reports/enterprise-v5-retest/`。

重建 gold、读取已保存评测记录并运行全套测试：

```powershell
.\.venv\Scripts\python.exe -m data.build_enterprise_v5
.\.venv\Scripts\python.exe -m insightpilot.enterprise_evaluate --database data/insightpilot-enterprise.sqlite --blind evals/enterprise-blind-v5.jsonl --gold evals/enterprise-gold-v5.jsonl --repeats 1 --output reports/enterprise-v5-retest --reuse-existing
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

验收详情见 [v5 报告](../../reports/enterprise-v5-acceptance.md)。

## 自己动手

1. 打开 `evals/enterprise-blind-v5.jsonl`，任选 X07、X13，确认规则为何在模型前澄清。
2. 打开 `data/build_enterprise_v5.py`，追踪 X01 的 category 订单率如何从源订单和 approved refunds 得到分子、分母。
3. 改变临时副本里一个预期数值，再运行 `test_v5_builder_uses_independent_synthetic_gold_and_freeze_hashes`；确认测试能发现 gold 和 SQL 结果不一致。
4. 查看 X06 的运行轨迹，确认无效规划没有进入 SQL 执行阶段。

## 面试追问

**为什么两个指标同时出现时不让模型挑一个？**

因为用户要两个答案，而当前执行计划只表达一个指标。静默挑选会返回表面合理、实际不完整的结果。明确澄清可以避免漏答。

**gold 为什么不直接用生产 SQL 生成？**

同一 SQL bug 可能同时污染系统结果和标准答案。Python oracle 与 SQL 执行路径分开实现，再以测试对照，能发现两侧不一致；这仍不能证明业务口径本身等同于真实企业口径。

**20/20 能证明什么？**

只能证明当前代码在这 20 个固定合成案例、当前模型配置的一次运行通过严格验收。它不证明随机用户问题、真实公司数据或未来模型版本的成功率是 100%。

**有没有实现 Agentic-RL？**

没有。这里是受限结构化规划、确定性 SQL 执行和独立复核。没有训练策略并在独立数据上证明收益，因此不能称为 Agentic-RL。
