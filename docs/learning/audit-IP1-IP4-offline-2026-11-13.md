# IP1–IP4 离线状态审计（2026-11-13）

## 已有离线证据

- IP1：`tests/test_query_engine.py` 覆盖 tenant context、只读事务、DDL/DML 拒绝、statement timeout 与连接池行为；`tests/test_ip1_rls_runner.py` 检查本地端口绑定、合成数据库、清理不删卷。宿主真实 RLS 仍待运行 `scripts/run_ip1_rls.ps1`。
- IP2：`tests/test_metric_versions.py` 覆盖当前版本、未知版本拒绝、历史定义追加不覆盖；`tests/test_s6_runtime.py` 覆盖历史版本无专属 SQL 时 fail closed。持久化历史 SQL 迁移尚未实现。
- IP3：`tests/test_s3_mcp_api_parity.py` 覆盖 API/MCP scope 拒绝一致；`tests/test_ip3_tracing.py` 覆盖 parent/child span、JSONL fallback 与敏感字段过滤。真实 Collector 持续联调不在本证据内。
- IP4：冻结集与 gold 哈希已核对；`tests/test_ip4_v13_stale_lock_fail_closed.py` 验证过期锁阻止余额查询前继续。manifest 保持 `blocked_by_review`，供应商 receipt 与实际费用均未知/null。没有模型结果。

## 未验收门槛

1. 用户启动 Docker Desktop 后运行 `Set-Location E:\job\insightpilot; .\scripts\run_ip1_rls.ps1`，回传脱敏的退出码和策略结果。
2. IP4 四项预注册哈希与成本账本锁过期。不得用历史余额刷新。模型请求曾被自动审核阻止，不重试或换路由。
3. IP3 Collector 生产部署、长期采样/保留策略尚未实测。

本审计当时未读取 `.env`、密钥或临时目录，也未发外部请求。后续 2026-11-13 v13 单轮预检多次调用 DeepSeek 只读余额接口；详情、哈希、预算和自动审核阻断见 [v13 单轮预检](stage-IP4-v13-full-suite-2026-11-13.md#2026-11-13-单轮预检与审批阻断)。没有 Docker 操作或模型请求。

IP1 后续离线切片补齐 QueryEngine 后端能力契约、共享核心构造入口和 SQLite adapter contract tests；43 项 SQLite/API/MCP/CLI/tenant/tracing/parity 定向回归通过。PostgreSQL/RLS integration gate 继续 pending，未启动 Docker；详情见 [IP1 查询引擎记录](stage-IP1.md#2026-11-13-离线前置切片)。


## 后续主机验证尝试（2026-11-13）

后续经授权尝试 Docker：IP-3 Compose runner 已修复并运行，Docker Hub 拉取认证 token 请求超时，容器未启动；IP-1 合成 PostgreSQL 健康检查通过，但宿主端口连接超时，RLS 未开始。仅本次 runner 创建的已退出 PostgreSQL 容器已移除；未删卷、未用私有数据、未调用模型。两项 gate 仍 pending，详见 `reports/ip3-docker-smoke-20261113.json` 与 `reports/ip1-rls-attempt-20261113.json`。前文“未启动 Docker”仅描述离线切片当时状态，不描述此次后续尝试。
