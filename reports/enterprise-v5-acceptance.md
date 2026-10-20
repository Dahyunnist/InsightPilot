# 企业退款分析 Agent：v5 验收

日期：2026-10-20。评测使用仓库生成的 50,000 单合成数据；未读取或上传用户私人业务数据。

## 正式配置结果

冻结集包含 20 道问题：11 道数值题、6 道预期澄清题、3 道预期拒绝题。独立 Python oracle 从合成源表计算金标准，不调用项目 SQL 编译器或模型输出。盲集哈希为 `6ef7103b334a5cbd0197d0bf019c17ca8830ad943e8801ee530fb30da7d7fa17`；数据指纹为 `c2d4e473ae87a03f0c252c464b1d1fac393b7da58fbc982935708137d622aa92`。

按项目默认的每请求最多 1,024 个输出 token 评测后，主臂严格通过 20/20：11/11 数值回答通过指标、维度、地区、日期、数值和运行时 SQL 复核；9/9 澄清或拒绝符合预期。20/20 Wilson 95% 区间为 83.89%–100%；数值题 11/11 区间为 74.12%–100%。这是刻意分层的合成题集，区间不代表生产用户总体表现。

两项消融也保留在同一复测目录：关闭语义指标检索后为 13/20，X01、X03、X04、X05、X06、X09、X10 未通过；关闭独立验证后为 9/20，数值答案不再满足完整验收条件。消融只说明这些组件在当前固定题集中的作用，不构成外部泛化证明。

此结果是在同一冻结集一次主运行。之前一次诊断运行把输出上限临时设为 512 token，结果 17/20；X06、X09、X11 的两次规划响应都恰好用满 512 token 并返回无效 JSON。诊断没有改代码；在恢复项目默认 1,024 token 后重跑，20 题通过。两次结果分别保存在 [512-token 诊断目录](enterprise-v5/) 与 [默认配置复测目录](enterprise-v5-retest/)。复测使用相同题集，不算独立的新样本；简历只可引用默认配置下的 20/20，并注明合成评测和单次运行。

## 修复

- 对“按下单日期逐日分组”的请求，在规划阶段澄清。指标目录只支持整体 cohort 汇总，不支持按订单日期展开。
- 对同一问题要求订单率和金额率时，在调用模型前澄清，防止静默选取其中一个指标。
- 冻结 v5 后，模型执行期间未改代码、题目或 gold。金标准构建器对 11 个数值问题都与受限 SQL 执行结果逐题比对。

## 调用与费用

512-token 诊断运行：50 次 API 请求、44,223 tokens。默认配置复测：38 次请求、38,947 tokens。按 DeepSeek 官方 Flash 峰时费率与缓存 token 估算，保守上界分别为 `$0.03219830` 和 `$0.03001525`，合计 `$0.06221355`；这不是账户账单。官方价格页说明旧名 `deepseek-v4-flash` 请求转由 V4.1 Flash 处理，费率会调整：[DeepSeek 官方价格](https://api-docs.deepseek.com/quick_start/pricing/)。

## 复现

```powershell
.\.venv\Scripts\python.exe -m data.build_enterprise_v5
.\.venv\Scripts\python.exe -m insightpilot.enterprise_evaluate --database data/insightpilot-enterprise.sqlite --blind evals/enterprise-blind-v5.jsonl --gold evals/enterprise-gold-v5.jsonl --repeats 1 --output reports/enterprise-v5-retest --reuse-existing
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

此复现命令读取保存的模型轨迹，不会重新调用 API。完整自动化测试为 66 项通过。

## 结论边界

结果支持“InsightPilot 在这组固定合成退款分析题上，按默认模型输出上限通过全部严格验收”。不能据此声称生产验证、客户数据泛化、竞品优势或 Agentic-RL 增益。题目与真实业务的语义覆盖有限；后续强验证应增加许可明确的独立业务/故障数据，而不是继续重测同一题集。
