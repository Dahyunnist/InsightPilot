# IP-3 学习记录：HTTP 服务与运行边界

## 本机 HTTP 基线（非 Compose）

固定规划器处理合成 SQLite 数据，规划延迟 150 ms。真实 Uvicorn HTTP 服务按并发 1、5、20 各发一轮同步 `/analysis` 请求。没有模型调用。p50/p95 含 429 快速拒绝，因此不等于成功分析的独立延迟分布；请求同步等待分析结果，也不是异步任务吞吐。

| 并发 | 请求 | 成功 | 429 | 503 | p50 | p95 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 1 | 0 | 0 | 209.027 ms | 209.027 ms |
| 5 | 5 | 5 | 0 | 0 | 224.398 ms | 234.743 ms |
| 20 | 20 | 8 | 12 | 0 | 13.390 ms | 261.613 ms |

20 并发时 8 个分析占满工作槽，12 个请求收到 429；没有 503。较低 p50 来自快速拒绝。

幂等重放返回相同 run ID 和 `Idempotency-Replay: true`；相同键换问题返回 409。现有运行时回归测试 25 项通过，覆盖速率限制、504 超时、协作取消、工作槽恢复、取消端点和 RunStore 生命周期。PostgreSQL 单连接池耗尽后恢复、statement timeout 后恢复见 `reports/ip1-ip2-postgres-acceptance-v1.json`。

## Compose 容器复验

`docker-compose.ip3.yml` 和 `Dockerfile.ip3` 只装载合成数据与固定规划器。首次尝试因 Docker Hub 认证网络超时中断。2026-10-24 用户在本机拉取基础镜像后完成一次 Compose 构建与容器验收；容器 Healthy，之后容器和网络均已清理。该结果只证明固定合成夹具通过一次容器验收。

本机 Compose 验收结果：

| 检查 | 结果 |
| --- | --- |
| 幂等首请求 / 重放 | 200 / 200，同一 `run_id` |
| 幂等冲突 | 409 |
| 并发 1 / 5 | 全部 200 |
| 并发 20 | 8 个 200，12 个 429，0 个 503 |
| 后端 / 规划器 / 模型调用 | 合成 SQLite / 固定规划器 / 0 |
| 容器与网络 | 验收后清理 |

以上数字来自用户提供的本机运行结果。历史本机 HTTP 延迟数据仍见 `reports/ip3-http-acceptance-v1.json`，不作为 Compose 延迟证据；Compose 输出本次未归档延迟字段。

复现命令：

```powershell
.\scripts\run_ip3_compose.ps1
```

本次仅覆盖合成 SQLite 与固定规划器的一次 Compose 验收。结果不代表真实业务数据、模型能力、生产表现或一般性能提升。Docker 不可用时可运行 `scripts/smoke_local.py` 验证本机健康检查与只读查询；该冒烟不替代容器测量。

## Compose 强制进程终止与恢复（2026-11-11）

路线 IP-3 还要求主动终止运行中的服务进程。本轮新增 `docker-compose.ip3-restart.yml` 与 `scripts/verify_ip3_process_restart.py`，复用本机 `insightpilot-api-probe:latest`（Python 3.12.15，image ID 前缀 `e42bdb34efa5`），未构建或拉取镜像。环境只挂载源码目录，不挂载 `.env`；规划器固定等待 4 秒，数据为合成 SQLite，无模型或外部 API 调用。请求经 localhost 真实 HTTP 端口映射进入 FastAPI。

运行命令：

```powershell
.\.venv\Scripts\python.exe scripts\verify_ip3_process_restart.py
```

结果：运行中的幂等请求先以 202 重放并取得同一 `run_id`；对 API 容器发送 SIGKILL 后，客户端收到 `RemoteDisconnected`。Compose 启回同一个容器 ID，`RunStore` 恢复后 `/runs/{id}` 返回 `interrupted` 且无结果；同一幂等键重试返回 409 `result_unavailable`，没有重复执行。验收用时 6.688 秒；容器和专用网络清理成功。完整报告见 `reports/ip3-process-restart-acceptance.json`。

该验收只覆盖固定规划器、合成 SQLite 和一次强制进程终止；不证明 PostgreSQL 场景、模型请求中断、OpenTelemetry、持续负载或生产恢复能力。IP-3 仍部分完成。

## SQLite 故障注入与 PostgreSQL 连接池恢复（2026-11-11）

新增 `tests/test_ip3_database_recovery.py` 与 `scripts/verify_ip3_postgres_disconnect.py`。SQLite 回归用合成数据库、固定规划器和一次性故障注入器：首次查询阶段断连返回并持久化 `failed`；规划调用数为 1，证明无自动重试；相同幂等键重放原失败且不再规划；同键换问题返回 409；新键重试成功。另一项并发 API 回归在固定规划器执行期间请求取消，验证结果和持久运行状态均为 `cancelled`、`cancel_requested=true`，幂等重放不重复执行。Python 3.12 容器定向测试 2/2 通过。

PostgreSQL 回归在一次性 tmpfs PostgreSQL 16 容器中装入合成双租户 fixture，由管理员定向 `pg_terminate_backend` 断开应用连接池会话。池丢弃坏连接并重新连库，后续只读查询成功；报告为 `reports/ip3-postgres-disconnect.json`。脚本不会自动重试业务查询。

此轮没有在 PostgreSQL 整个服务停机期间经过 FastAPI 请求，因此 PostgreSQL 故障时 API `RunStore` 状态与幂等重放尚未端到端验证；也未测模型请求中断、OpenTelemetry、持续负载或生产恢复。仅验证 PostgreSQL 客户端会话被强制断开后的连接池恢复。没有使用项目 `.env`、客户数据或真实模型调用。
