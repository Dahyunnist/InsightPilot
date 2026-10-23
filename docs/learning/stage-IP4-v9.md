# IP-4 v9：日期修复后新集与配对复测

## v8 开发诊断

v8 已暴露，仅作开发集。9 个错误拒答里，8 个是 planner 仅解析 `YYYY-MM-DD`，无法读中文月份、季度和同月日范围；X11 是退款事件指标检索词未命中。日期修复位于 `insightpilot/planner.py`，新增回归覆盖中文年月、季度和日范围。未修改 v8 题目或 gold，也未重跑 v8 模型。X11 检索缺口未在此补丁中修改。

## v9 冻结

v9 使用 seed `20261024`、新 50,000 单合成数据库与 20 个重新编写的问题。Python 源行聚合 gold 在模型外生成。blind、gold、manifest 文件哈希已锁；历史精确题面重叠 0。语义近似不能由精确字符串检查排除。Agent 只接收 blind 单题；scorer 在分析后加载 gold。规则基线先跑，8/20，11 个可答题均未答，guard 8/9。`reports/enterprise-v9-model-preregistration.json` 锁定模型、planner/scorer/harness、费用和重试上限。

## 费用

用户授权把 cap 从 `$4` 提到 `$5`。旧额度余 `$3.69558975`，本次登记额外 `$1`。此前有 usage 的 named runs 峰价估值 `$0.1795704`，v8 再计 `$0.0015003`；旧 JSONL 76 次 usage 记录仅作交叉核对，不重复相加。累计 booked usage/reserve `$0.30441025`，新 cap 下可用 `$4.69558975`。

v9 最多 20 题 × 2 次 provider 请求；每请求输入 2,000 tokens、输出 1,024 tokens。DeepSeek Flash 峰价按未缓存输入 `$0.30/M`、输出 `$1.20/M` 计算。整轮预留 `$0.073152`，预留后余 `$4.62243775`。每个实际 provider 请求各计一次，usage 缺失时按单次峰价上界记账。不得超过 40 次请求。

本阶段没有 v9 模型请求。Compose 保持未验证；后续用本机缓存 PostgreSQL 镜像或主机 PostgreSQL。旧 IP4 冻结失败与报告不变。
