# IP-1 / IP-3 宿主复验入口（2026-11-14）

## 一条命令

在仓库根目录执行：

```powershell
.\scripts\run_ip1_ip3_host_validation.ps1
```

入口先检查 Docker daemon。Docker CLI/daemon 不可用时，只写 `blocked_docker_*` 汇总和脱敏日志到系统临时目录 `insightpilot-host-ip1-ip3-<run-id>`，退出并停止；不会启动容器或自动重试。可用 `-ArtifactDirectory <path>` 指定其他报告目录。仓库 `.tmp/` 不会被入口读写。当前执行环境的 Docker API 返回 `permission denied`，因此本轮没有运行宿主验收。

## 执行内容

- IP-1 启动 `insightpilot-ip1-host-acceptance` 专属 Compose project，在 `127.0.0.1:55432` 使用合成 PostgreSQL + tmpfs，运行双租户 API/RLS verifier，再对 `data_meta` 加 ACCESS EXCLUSIVE 锁验证 100 ms `statement_timeout` 和超时后的恢复。
- IP-3 构建/启动 `insightpilot-ip3-compose` 专属 API probe，运行合成 SQLite 的真实 HTTP 健康、幂等与 burst smoke；随后在容器内运行 TCP Streamable HTTP MCP 客户端取消探针，验证取消信号到 worker 的传递与退出。
- 两阶段 JSON 报告、汇总和脱敏日志落到该次 `.tmp` 目录；原始重定向日志经脱敏后删除。模型调用数固定记 0。
- IP-1/IP-3 runner 各自 finally 清理自己专属 project 的容器与网络，执行 `down --remove-orphans`；没有 `-v`、卷删除命令或宿主 bind mount。IP-1 fixture 的破坏性 schema 重建仅限本次临时 `insightpilot_ip4` 数据库，数据库目录为容器 tmpfs。

若构建所需镜像或包未缓存，Docker build 可能尝试一次网络拉取；该运行器不会因失败自动重试。宿主验收仍不触发模型。

## 尚未覆盖

MCP smoke 使用合成 core 与真实本机 TCP/Streamable HTTP 协议，不代表模型供应商的在途 HTTP 请求可取消；现有查询只在供应商响应后继续观察取消事件。Docker daemon、PostgreSQL 锁超时和 Compose/MCP 宿主 smoke 均需本机 Docker 可用后运行。生产负载、生产 Collector 与供应商取消能力仍未验收。
