# IP-1～IP-3 证据审计（2026-11-14）

## 阶段状态与本轮动作

| 阶段 | 现有证据与状态 | 本轮结论 |
| --- | --- | --- |
| IP-1 查询引擎与 RLS | `reports/ip1-ip2-postgres-acceptance-v1.json`、`reports/ip3-host-integration-20261113.json` 记录双租户、列授权、RLS、只读、超时和连接池恢复通过；`stage-IP1.md` 仍明确宿主证据与离线证据边界。 | 发现 `read_data_identity()` 漏设查询超时，已修复并加回归；新改动尚未经过宿主 PostgreSQL。 |
| IP-2 第二业务域与语义目录 | `stage-IP2.md`、多域合成数据和独立 Python oracle 记录 ROI、去重/撤销、UTC 边界、指标语义及 SQL 交叉核对。 | `tests.test_multidomain` 12 项通过；本轮未发现新的可离线复现缺口。 |
| IP-3 HTTP/MCP 与运行边界 | `reports/ip3-host-integration-20261113.json` 和阶段记录含 Compose HTTP、幂等、短时并发、PG/RLS、故障恢复及本机 Collector 证据；阶段仍部分完成。 | 现有宿主报告可追溯。Windows Python 3.14 本轮 API 异步回归在第二项处再次卡住，故不计为通过；供应商请求取消、持续负载和生产级 Collector 仍属明确边界。 |

## 本轮验证

- `tests.test_query_engine tests.test_s2_policy tests.test_ip1_rls_runner`：18/18 通过。
- `tests.test_multidomain`：12/12 通过。
- IP-3 API 异步回归在 `test_cancelled_run_persists_cancel_state_and_idempotent_replay` 运行时未结束；该宿主环境下没有可报告的完整套件结果。此前 `tests.test_ip3_database_recovery` 的 Windows Python 3.14 `asyncio` socket 初始化问题也已有记录。
- 未运行模型评测；没有模型请求。
- 远端检查：本地 `HEAD` 与本地 `origin/master` 均为 `16a46f2`。`git ls-remote origin refs/heads/master` 因 Windows OpenSSH 初始化 `NtCreateDirectoryObject ... 0xC0000022` 失败，无法据此确认服务端当前引用。

## 变更边界

数据身份读取现在沿用 `statement_timeout_ms`，超时归类为 `statement_timeout`；测试覆盖事务确实设置预算和 SQLSTATE 映射。S2 只读 URI 测试改用 `Path.as_uri()`，使 Windows 盘符路径能由 SQLite 正确解析。IP-4 冻结集、模型报告、费用账本与 `.env` 未改动。
