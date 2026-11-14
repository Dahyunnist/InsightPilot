# IP-1～IP-4 当前验收与宿主待测项（2026-11-14）

## 阶段验收表

| 阶段 | 状态 | 已有验收证据 | 当前边界 / 待验证 |
| --- | --- | --- | --- |
| IP-1 查询引擎与租户隔离 | 已完成历史宿主验收；最近离线修复待宿主复验 | `reports/ip1-ip2-postgres-acceptance-v1.json` 与 `reports/ip3-host-integration-20261113.json` 记录两租户隔离、8 张表 FORCE RLS、列授权、只读、超时和池恢复；`tests.test_query_engine` 覆盖 QueryEngine 与数据身份超时映射。 | `fe379ec` 的 `data_meta` `statement_timeout` 改动尚未重跑真实 PostgreSQL；Docker daemon 当前不可访问。 |
| IP-2 第二业务域与语义目录 | 合成数据范围内完成 | `docs/learning/stage-IP2.md`、冻结 ROI gold、独立 Python 账本与 SQL 交叉核对；`tests.test_multidomain` 12 项通过，数据库验收报告包含营销 ROI 与 UTC 边界。 | 固定 seed 与合成业务数据不代表真实企业数据、生产 freshness 或总体准确率。 |
| IP-3 API/MCP 与运行生命周期 | 部分完成 | 2026-11-13 宿主报告记录 Compose HTTP smoke、幂等、短时并发、PostgreSQL/RLS 与 Collector；本轮 API 超时取消标记、取消登记竞态、同键并发及 MCP worker 取消桥接离线回归共 5 项通过。 | `33dd654`/当前 MCP 改动尚未做宿主 HTTP/MCP 联调；供应商 SDK 正在途请求的底层取消、持续负载和生产 Collector/采样保留未验收。 |
| IP-4 独立冻结评测 | 部分完成；v13 模型评测被传输阻断 | v13 冻结集与独立 gold 已封存，RulePlanner 15/22；r5 报告记录 15 次请求尝试、0 个 provider response、0/14 可答输出、费用回执未知。历史评测与费用 ledger 均保留。 | 不将 transport failure 计成模型通过/失败分数；需网络 egress 恢复、provider receipt/cost reconciliation 后，另行决定是否重跑。按当前指令不发模型请求。 |

## 宿主待测清单

1. Docker daemon 可用后，针对当前代码复跑 IP-1 `scripts/run_ip1_rls.ps1 -Execute -ConfirmDatabaseCreation`，特别确认 `data_meta` 锁等待受 `statement_timeout` 限制并在超时后可继续查询。
2. Docker daemon 可用后，复跑 IP-3 Compose/API 与 IP-1 RLS integration；核对 504 取消标记、worker 收尾、同键重放、MCP Streamable HTTP 正常响应及客户端取消后分析停止。当前已有 2026-11-13 报告是历史代码版本证据，不覆盖本轮改动。
3. 对接真实模型供应商时，在供应商客户端允许取消的条件下验证在途请求中断、取消后连接/worker 恢复；测试不调用真实付费模型前须另行确认。
4. 生产级持续负载、Collector 部署、采样/保留策略仍需目标环境验证；现有短 burst 与本机 Collector smoke 不外推为生产验收。
5. IP-4 r5 `reports/ip4-v13-model-evaluation-r5.json` 的 provider receipt 状态为 unknown；确认可能产生的费用后再更新账本。模型调用仍保持关闭，不重跑评测。

## 本轮离线验收

- 新增 `tests.test_ip3_mcp_cancellation`：正常 MCP 工具结果与取消时向 core worker 传递取消事件，2/2 通过。
- API timeout 持久取消标记、取消登记竞态、并发同键预留，3/3 通过。
- `git diff --check` 通过。没有付费模型请求，也没有启动 Docker。
