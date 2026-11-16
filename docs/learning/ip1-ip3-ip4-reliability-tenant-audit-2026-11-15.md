# IP-1/IP-3/IP-4 runner 与租户边界离线复核（2026-11-15）

## 发现与修复

- IP-3 Compose runner 的 MCP cancellation `docker compose exec` 使用无上限等待；挂起时不会进入脚本失败和 Compose 清理。现在 120 秒超时后终止宿主子进程，原始输出通过 `finally` 删除，再由外层清理本次 Compose project。
- IP-3 Compose 清理此前也可能无限等待。现在只对固定 IP-3 project 执行 `down --remove-orphans --timeout 10`，宿主等待最多 30 秒；超时会终止 CLI 并明确警告容器或网络可能仍在。没有删卷。
- IP-4 v13 runner 接受已存在的 `ReportPath`，失败时还可能把旧报告当本次摘要。现在服务启动前拒绝路径冲突，且只读取本次 parity 命令成功生成的报告。Compose 清理仍有 30 秒上限并恢复进程环境变量。

## 租户边界复核

新增同一 fake pooled connection 连续执行 tenant-a、tenant-b 请求的回归，检查每个只读事务都先设置自己的 transaction-local `app.tenant_id`。现有测试继续检查缺少租户时拒绝、reader role、只读事务、调用方 pool 所有权、失败 trace 脱敏和运行记录按 principal 隔离。

这些离线/mock 测试只证明应用正确绑定上下文，不证明 PostgreSQL RLS 实际生效。RLS 现场门禁仍是 `scripts/run_ip1_rls.ps1`；IP-4 parity 现场门禁仍是：

```powershell
.\scripts\run_ip4_v13_postgres_parity.ps1 -Execute -ConfirmDedicatedDatabase
```

Windows Python 3.14 的 `TestClient.__enter__` 确实卡在请求前：`run_blocking_portal` 建立 Proactor loop 时进入 `socket._fallback_socketpair()` 的阻塞 `accept()`。栈未进入应用 endpoint。为保留关键租户断言且不跳过测试，现改为并发调用 FastAPI `APIRoute.endpoint`：验证 bearer token 到 tenant 映射、无效/缺失 bearer 拒绝、同 idempotency key 按 principal 隔离、运行记录不可跨 principal 读取，并检查 `/analysis` 没有 `X-Tenant-ID` header 参数。

此替代证明 route 层契约，不覆盖 HTTP 字节解析、ASGI middleware/exception handler 或真正发送伪造 `X-Tenant-ID` 的网络请求。真实连接池复用、并发 RLS 与 PostgreSQL 清理仍未在本轮验证。

## 验证

定向离线回归：34 项通过，覆盖取消/超时/失败后恢复、API route 租户并发绑定、PostgreSQL adapter fake-pool 契约与逐请求 tenant scope、失败 trace 脱敏、principal 隔离、IP-3/IP-4 PowerShell 解析与 runner 清理/失败路径静态约束、RLS runner 静态检查及本地服务 bounded cleanup。没有启动 Docker/PostgreSQL，没有调用模型，也没有改冻结集或账单。

IP-3 超时后的 Compose 清理若自身超过 30 秒，只能报告可能残留；操作者需检查本次固定 project。该宿主行为需要 Docker 可用时再验证。
