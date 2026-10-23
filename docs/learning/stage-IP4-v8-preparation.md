# IP-4 v8 离线预登记

## 冻结材料

v8 使用新 seed `20261023` 和 50,000 笔合成订单。独立 Python 源行聚合器生成 gold，不读取模型输出或产品 SQL。题面共 20 道：11 道预设可答、6 道澄清、3 道拒答。blind 与 gold 文件哈希及 manifest 哈希锁在 `reports/enterprise-v8-model-preregistration.json`。

v6、v7 均已跑过模型，不能再算新 test。v8 检查历史数据报告中的规范化题面精确重叠为 0；语义近似无法仅靠字符串检测排除。题目为新改写，指标和情境仍来自同一受限退款目录，因此结论只适用于该合成范围。Agent 调用只传单题面；gold 仅在分析后进入 scorer。冻结与盲区检查均未发送模型请求。

## 规则基线

`reports/enterprise-v8-rule-baseline.json`：RulePlanner 严格通过 8/20，可答题 0/11，guard 8/9。报告列出每题差异、错误拒答与误接受。该低分显示规则基线对本次改写敏感；先保留结果，不改题、不调分。下一轮将同一 v8 盲题分别跑规则和模型路径。

## 费用锁

账本扣除历史记账后余 `$3.69709005`。逐项已保存 run usage 峰价估计为 `$0.1795704`；旧 JSONL 有 76 次、79,464 tokens，按峰价计 `$0.065552`，另有缺回执预留 `$0.034448`。JSONL 可能与汇总报告重复，因此只交叉核对，不重复累加。全局已记账 `$0.30290995`，扣除命名 run 估价后的 `$0.12333955` 保留为历史预留或未调平 usage。供应商账单不可读，所以余额是估价与预留，不声称为实际账单金额。明细见 `reports/cost-audit-v8.json`。

DeepSeek 当前 Flash 峰价按未缓存输入 `$0.30/M`、输出 `$1.20/M`。v8 单次最多 2,000 输入 tokens、1,024 输出 tokens，每题最多 2 次，最多 40 个请求；完整峰价上界 `$0.073152`，预留后余额 `$3.62393805`，仍低于 `$4.00`。模型固定 `deepseek-v4-flash`，temperature 0。不得超出此预登记重试或扩大题数。

## PostgreSQL 与冻结边界

后续优先使用本机缓存 `postgres:16-alpine` 镜像或主机 PostgreSQL；不要因 Docker Hub 超时重复拉取。Compose IP-3 的 Python 镜像仍标记未验证。v8 当前规则基线只跑 SQLite。旧 IP4 的数据、报告和 tenant-a R01/R02 模型失败均保持原样。

重建与复核：

```powershell
.\.venv\Scripts\python.exe -m data.build_enterprise_v8
.\.venv\Scripts\python.exe scripts\verify_enterprise_v8_rule.py
.\.venv\Scripts\python.exe scripts\preregister_enterprise_v8.py
```

冻结和离线准备阶段没有模型调用。下节记录后续按预登记执行的配对结果。

## v8 配对结果

预登记通过后仅运行 v8 模型主臂；未重跑 v6、v7 或旧 IP4。严格主臂 10/20：可答 2/11，guard 8/9。规则基线为 8/20：可答 0/11，guard 8/9。两臂均无 guard false accept；规则路径 11 个可答题均未回答，模型系统路径 9 个可答题在检索/预检后澄清，未到模型端。仅 X04、X05 产生模型请求。因此 2/11 是端到端可答题结果，不能称作模型单独准确率。

共 2 次请求，prompt 1,033 tokens、completion 992 tokens，无缺失 usage；按峰价估算 `$0.0015003`。全局账本余 `$3.69558975`。逐题输出和规则差异见 `reports/enterprise-v8-model-blind-report.json`、`reports/enterprise-v8-model-diff.json`。结果只来自 20 道合成题和一个 seed。仅跑 SQLite，未跑 PostgreSQL 模型臂。

### 失败归因与修复

v8 现为已见开发集，不再作 test。9 个端到端错误拒答中，8 个在 planner 日期抽取阶段被挡：它们用中文年月、季度或同月日范围，旧解析器只认 `YYYY-MM-DD`。X11 因退款事件指标检索词未命中而澄清。只修 planner 日期解析，增加中文月份、季度和同月日范围回归；未改 v8 blind/gold，也未重跑模型。X11 检索缺口留待后续修复。
