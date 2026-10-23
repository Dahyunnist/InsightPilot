# IP-3 学习记录：HTTP 服务与运行边界

## 验收

固定规划器处理合成 SQLite 数据，规划延迟 150 ms。真实 Uvicorn HTTP 服务按并发 1、5、20 各发一轮同步 `/analysis` 请求。没有模型调用。p50/p95 含 429 快速拒绝，因此不等于成功分析的独立延迟分布；请求同步等待分析结果，也不是异步任务吞吐。

| 并发 | 请求 | 成功 | 429 | 503 | p50 | p95 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 1 | 0 | 0 | 209.027 ms | 209.027 ms |
| 5 | 5 | 5 | 0 | 0 | 224.398 ms | 234.743 ms |
| 20 | 20 | 8 | 12 | 0 | 13.390 ms | 261.613 ms |

20 并发时 8 个分析占满工作槽，12 个请求收到 429；没有 503。较低 p50 来自快速拒绝。

幂等重放返回相同 run ID 和 `Idempotency-Replay: true`；相同键换问题返回 409。现有运行时回归测试 25 项通过，覆盖速率限制、504 超时、协作取消、工作槽恢复、取消端点和 RunStore 生命周期。PostgreSQL 单连接池耗尽后恢复、statement timeout 后恢复见 `reports/ip1-ip2-postgres-acceptance-v1.json`。

## Compose 限制

已新增 `docker-compose.ip3.yml` 和 `Dockerfile.ip3`，仅装载合成数据与固定规划器。Docker Hub 拉取 `python:3.12-slim` 时，认证请求因网络超时失败；本次未完成容器内测量。本机通过真实 HTTP 跑同一夹具，并清理服务进程及临时数据库目录。本地结果不标为 Compose 结果。

结果：`reports/ip3-http-acceptance-v1.json`。容器可用时复跑：

```powershell
docker compose -f docker-compose.ip3.yml up --build -d --wait
.\.venv\Scripts\python.exe scripts\probe_ip3_http.py --output reports\ip3-http-acceptance-v1.json
docker compose -f docker-compose.ip3.yml down
```

费用账本没有变化；模型调用 0。
