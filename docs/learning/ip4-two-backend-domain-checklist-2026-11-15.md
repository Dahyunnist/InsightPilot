# IP-4 双数据库、双业务域验收清单（2026-11-15）

## 范围

只核对 synthetic IP-4 refunds 与 marketing ROI。区分 v13 冻结集与 v14 独立候选；不把规则基线写成模型成绩，也不把 v13/v14 合并计分。

| 版本 / 域 | SQLite | PostgreSQL tenant-a | PostgreSQL tenant-b | 结论 |
| --- | --- | --- | --- | --- |
| v13 refunds（12 题） | Python oracle/只读 SQL 与规则基线有离线证据；RulePlanner 22 题总分 15/22 | 冻结 22 题向量逐题一致 | 冻结 22 题向量逐题一致 | 确定性 parity 通过；模型证据不完整 |
| v13 marketing ROI（10 题） | 独立 Python event oracle、SQL 交叉核对与规则基线有离线证据；RulePlanner 22 题总分 15/22 | 冻结 22 题向量逐题一致 | 冻结 22 题向量逐题一致 | 确定性 parity 通过；模型证据不完整 |
| v14 refunds（11 题） | Python oracle、SQL 交叉核对；RulePlanner 3/11 | 已归档报告显示 22 题评分向量与 SQLite 一致 | 已归档报告显示 22 题评分向量与 SQLite 一致 | parity 报告通过；候选待审 |
| v14 marketing ROI（11 题） | 两套 Python oracle、SQL 交叉核对；RulePlanner 5/11 | 已归档报告显示 22 题评分向量与 SQLite 一致 | 已归档报告显示 22 题评分向量与 SQLite 一致 | parity 报告通过；候选待审 |

## 证据审阅

- 11 月 15 日的离线 acceptance audit 曾因执行代码锁失配和缺 v13 PostgreSQL 同哈希证据标为 `blocked`。11 月 16 日的同哈希复验已补齐该 parity 缺口；旧 12 题 PostgreSQL 报告仍不覆盖 v13 的 22 题。
- v14 摘要 `reports/ip4-v14-postgres-parity-summary-2026-11-15.json` 校验源 parity 报告 SHA；摘要标记 PostgreSQL 16.15、两个 tenant、22 题 score vector 与 SQLite 全匹配。原始逐题 parity 文件位于候选目录之外，没有纳入仓库；本次没有重新启动 PostgreSQL。
- v14 SQLite gold 由独立 oracle 与只读 SQL 交叉核验。v14 PostgreSQL parity 证明规则评分向量一致，不证明规则基线高准确率：总分 8/22，refunds 3/11，marketing ROI 5/11。
- `tests/test_ip4_v14_postgres_integration.py` 是显式 opt-in，默认跳过；不得把 mock tenant adapter 测试当作 live PostgreSQL。
- IP-1 的一般 RLS/只读/连接池证据另见 `reports/host-validation-ip1-ip3-2026-11-15/` 与 `reports/ip1-ip2-postgres-acceptance-v1.json`。这些证据不替代 v13 固定 hash parity。

## 2026-11-15 本轮验证

使用项目 `.venv` 运行 IP-4 holdout、离线 bundle、v14 candidate/evaluator/parity-summary、PostgreSQL opt-in gate、IP-1 RLS canary/runner、QueryEngine 和多域测试：**56 passed、1 skipped、24 subtests passed**。跳过项是需要显式 opt-in、会准备专用 PostgreSQL 测试库的 live v14 integration。未启动 Docker、未查询余额、未调用模型；没有改题目、gold 或评分语义。

## 2026-11-16 release candidate 离线复核

gold-free 库存、权限与 IP3 定向组在隔离网络的 Python 3.12 容器中 39 项通过、6 项因需要专用 PostgreSQL 和显式 opt-in 跳过。9 项 PowerShell 专项在 Windows 宿主复跑均通过。MCP 取消异步组在 Python 3.12 容器 2/2 通过；模型调用与外网请求均为 0。Gold-reading 测试不在本组范围内。

## 未完成项

1. v13：同一 frozen hash 的 SQLite、PostgreSQL tenant-a、tenant-b 确定性逐题 parity 已通过。模型消融报告未记录 backend，执行代码锁仍不匹配；模型评测仍受锁、实时预算快照和审批门槛约束。
2. v14：所有者仍需审核题目与 gold 后，才可决定是否把候选正式冻结。现存 PostgreSQL 摘要不能替代该审核。
3. 生产数据、外部客户准确率、持续负载和 SLA 不在本清单证据范围内。

因此，IP-4 的确定性双后端 parity 已有 v13 与 v14 证据；模型执行范围和 v14 候选审阅仍未完成，不能将 IP-4 标为完整验收。

## v13 PostgreSQL parity 阻断与复现

本轮只检查配置是否存在，不读取或输出凭据值。进程环境与项目 `.env` 都没有 `INSIGHTPILOT_IP4_ADMIN_DSN`、`INSIGHTPILOT_IP4_ADMIN_PASSWORD`、`INSIGHTPILOT_IP4_APP_DSN`、`INSIGHTPILOT_IP4_APP_ROLE`、`INSIGHTPILOT_IP4_APP_PASSWORD`。Docker CLI 存在，但 Docker Engine 返回 named-pipe `permission denied`；本机没有 `psql`。因此未启动容器、未重建数据库、未声称 parity 通过。

Docker 与专用连接配置可用后，使用独立 Compose project、临时 Compose 副本（去掉固定 `container_name`，端口改为 `127.0.0.1::5432`）和 tmpfs，避免碰现有容器或持久卷。此时只在用户宿主显式启动专用临时服务。先设置仅进程内的 admin/app DSN 与密码，再按顺序从 v13 固定 SQLite 装载同一 schema，并跑两个 tenant：

新增宿主入口 `scripts/run_ip4_v13_postgres_parity.ps1`。默认 dry-run 不启动服务；执行必须同时传 `-Execute -ConfirmDedicatedDatabase`。它核对 v13 manifest/blind/gold 与 SQLite fixture 哈希，使用缓存中的 PostgreSQL 镜像（不会 pull）、随机项目名、随机回环端口和 tmpfs；所有数据库凭据只在子进程环境变量中短暂存在。容器启动、fixture 导入、parity 评测和项目级清理均有等待上限。成功或失败只打印脱敏 case ID、失败检查分类、parity 状态和 model_calls；不会输出 DSN、SQL、参数或结果行。finally 仅对该随机 Compose project 执行 `down --remove-orphans`，不加 `-v`，不删除卷，也不影响其他服务。

```powershell
.\.venv\Scripts\python.exe scripts\prepare_ip4_postgres.py --database data\insightpilot-ip4-v13.sqlite
.\.venv\Scripts\python.exe scripts\run_ip4_offline_audit.py --bundle benchmarks\ip4-offline-prep-v1 --database data\insightpilot-ip4-v13.sqlite --postgres-dsn $env:INSIGHTPILOT_IP4_APP_DSN --output reports\ip4-v13-postgres-parity-current.json
```

第一条只可指向一次性 `insightpilot_ip4` 测试数据库：loader 会重建该库的 `public` schema。第二条现在会先将 bundle `source_v13` manifest/blind/gold 哈希与仓库冻结件逐一比对；不匹配立即停止。应报告 SQLite 与 tenant-a、tenant-b 各 22/22 结果一致，并保持 `model_calls=0`。通过后仍需审阅执行代码锁；不能由此放行模型请求。结束时只停止本次唯一 Compose project 的 PostgreSQL 容器；不运行 `down -v` 或删除卷。

宿主入口用户命令（仅在 Docker Engine 权限、依赖和缓存镜像满足后手动执行）：

```powershell
.\scripts\run_ip4_v13_postgres_parity.ps1 -Execute -ConfirmDedicatedDatabase
```

临时 Compose 启动前必须确认 project 名唯一、宿主端口动态分配、data dir 为 tmpfs、目标数据库名固定为 `insightpilot_ip4`。不能确认上述条件时，不运行 loader。当前工作机不满足 Docker/DSN 前置条件，以上步骤只作待复现实验说明。

## 2026-11-16 v13 复验更新

此前 v13 两后端 parity 缺口已由零模型调用复验关闭：同一冻结 22 题（refunds 12、marketing ROI 10）在 SQLite 与 PostgreSQL 两租户逐题一致，各 22/22，冻结 manifest/blind/gold 哈希匹配。完整隔离、运行 ID、PostgreSQL 版本与清理证据见 `reports/ip4-v13-postgres-parity-a2ea8ab495c14543afab476f0496ae65.json`；字段级总审计见 `reports/ip4-release-requirement-audit-2026-11-16.json`。

该结论只适用于当前确定性 RulePlanner parity。36-call 模型消融的 run manifest 未记录执行 backend，不能外推为 PostgreSQL 模型执行；v13 模型执行代码锁仍 blocked。v14 的 22-case PostgreSQL parity 仍属待 owner review 候选证据，不计入冻结 release gate。
