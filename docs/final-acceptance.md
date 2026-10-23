# 最终验收与复现

## 一条命令

在项目根目录运行：

```powershell
.\scripts\verify_final_acceptance.ps1
```

脚本启动隔离的临时 PostgreSQL，装载合成多租户数据，重跑 IP-1/IP-2 强制权限负例、R01/R02 开发诊断与分域规则基线，校验 IP4 冻结封存和已保存的模型盲评报告，最后清理临时容器。**不会调用模型 API，也不会覆盖旧 IP4 冻结报告。**需要 Docker、项目 `.venv` 和已安装依赖。模型盲评结果是对已有单轮产物的复核；不重发模型请求。

全套测试单独运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

全套测试当前共 97 项。MCP Streamable HTTP 回归使用 FastAPI `TestClient` 运行完整 JSON-RPC 初始化、工具枚举和工具调用，避免随机端口及手动 Uvicorn 线程的生命周期抖动。IP-1/IP-2 补充两条边界用例与一条外部连接池所有权回归。

此前基础验收命令连续两次通过。加入 IP-1/IP-2 权限负例后的增强命令，修复发现的问题后完整通过一次：强制权限负例、R01/R02 开发诊断、分域规则基线六臂、旧 IP4 封存和模型盲评报告哈希校验均通过。全程 0 模型 API 调用，冻结评测结果未改。

## 结果索引

| 内容 | 文件 | 记录 |
|---|---|---|
| 分域盲测集与 manifest | `evals/domain-validation-v1/` | refunds、marketing ROI 各 6 题：4 可答、2 guard |
| 规则基线 | `reports/domain-validation-v1.json` | SQLite 与两个 PostgreSQL tenant 共 6 臂，各 6/6；0 模型调用 |
| 模型盲评预注册 | `reports/domain-validation-model-preregistration.json` | scorer、prompt、数据哈希、输出上限与费用预留 |
| 模型盲评逐题结果 | `reports/domain-validation-model-blind-report.json` | 3 臂各 12/12，含 oracle、SQL、usage 与评分字段 |
| IP4 冻结封存 | `reports/ip4-freeze-seal.json` | 旧题集、gold 和评测报告哈希 |
| 旧 IP4 模型结果 | `reports/ip4-model-blind-verification.json` | tenant-a 10/12；R01/R02 在 query 阶段失败 |
| R01/R02 开发诊断 | `reports/ip4-r01-r02-dev-diagnostic.json` | RulePlanner 在 SQLite 与两个 PG tenant 均通过；RLS scope 探针 |
| IP-1/IP-2 PostgreSQL 权限验收 | `reports/ip1-ip2-postgres-acceptance-v1.json` | reader 列授权、8 表 RLS、跨租户、敏感列、DDL/DML、注入、边界、上下文清理、超时与池恢复 |
| IP-3 HTTP 压测 | `reports/ip3-http-acceptance-v1.json` | 本机真实 HTTP 合成压测；Compose 镜像拉取受网络阻断，未声称容器内通过 |
| IP-4 独立集比较 | `reports/ip4-stage-comparison-v1.json` | 冻结哈希、双后端规则基线、历史消融、拒答/误接受与样本限制 |
| 费用账本 | `reports/model-experiment-cost-ledger.json` | 请求与 token 记录、估价方法及剩余预算 |
| 阶段说明 | `docs/learning/stage-IP4.md` | IP4 冻结、失败边界和分域复验背景 |

## 分域盲评读数

固定的 12 个独立题目由两个领域组成，每领域 4 道可答题和 2 道 guard。模型执行 3 臂：SQLite、PostgreSQL tenant-a、PostgreSQL tenant-b。因此是每臂 12/12、共 36 个 case-run；**独立样本仍只有 12 题**。三个臂均为 8/8 可答、4/4 guard，false refusal 和 false accept 均为 0。规则基线在相同三个后端臂亦全部通过。

模型调用共 24 次；输入 14,874 tokens，输出 10,680 tokens。按 DeepSeek 官方 Flash 峰价（未缓存输入 $0.30/百万 tokens，输出 $1.20/百万 tokens）且按所有输入未缓存估算为 **$0.0172782**；不是供应商账单金额。官方价格：[DeepSeek API 定价](https://api-docs.deepseek.com/quick_start/pricing/)。

## 旧 IP4 失败边界

旧冻结评测中，SQLite 12/12、PostgreSQL tenant-b 12/12、tenant-a 10/12。tenant-a 的 IP4-R01、IP4-R02 在 query 阶段失败。旧模型报告没有保留 SQL、脱敏数据库错误或 SQL fingerprint，故根因未知。开发副本的 RulePlanner 诊断通过，不能据此推断旧失败原因。冻结数据与报告保持原样，封存脚本会检查哈希。

## 简历边界

可写：实现受约束的自然语言到查询计划流程、固定 SQL 编译、只读多租户查询和独立结果复核；在合成分域集上完成 12 个独立题目的单轮评测，三个执行臂各 12/12，并列明题集构成。

不可写成：生产准确率、客户数据效果、普遍泛化能力、独立重复验证，或“所有阶段全部无失败”。样本是小规模合成集，且旧 IP4 的 tenant-a 有两题 query 阶段失败、原因未查明。可参考 `docs/resume-bullets.md` 与 `docs/final-delivery-one-pager.md`，以本页限定为准。

## 提交记录

分域盲评阶段提交：`e65091b`（记录分域模型盲评与成本）。本验收文档与复现脚本随最终验收提交；最终提交哈希由本次交付回报。

IP-3/IP-4 补记：IP-3 本机真实 HTTP 测量通过，但 Compose 容器验收因 Docker Hub 拉取超时未完成；IP-4 哈希封存及独立集复核通过。详见 `docs/learning/stage-IP3.md` 与 `docs/learning/stage-IP4.md`。
