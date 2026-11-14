# IP1–IP4 离线状态审计（2026-11-13）

## 已有离线证据

- IP1：`tests/test_query_engine.py` 覆盖 tenant context、只读事务、DDL/DML 拒绝、statement timeout 与连接池行为；`tests/test_ip1_rls_runner.py` 检查本地端口绑定、合成数据库、清理不删卷。宿主真实 RLS 仍待运行 `scripts/run_ip1_rls.ps1`。
- IP2：`tests/test_metric_versions.py` 覆盖当前版本、未知版本拒绝、历史定义追加不覆盖；`tests/test_s6_runtime.py` 覆盖历史版本无专属 SQL 时 fail closed。持久化历史 SQL 迁移尚未实现。
- IP3：`tests/test_s3_mcp_api_parity.py` 覆盖 API/MCP scope 拒绝一致；`tests/test_ip3_tracing.py` 覆盖 parent/child span、JSONL fallback 与敏感字段过滤。真实 Collector 持续联调不在本证据内。
- IP4：旧锁与当前 scorer/runtime 哈希不一致，已停止使用。用户授权仅覆盖 22 道冻结合成题；此前余额接口返回 `URLError`，因此未发送模型请求。之后离线 revision r4 将题目归类为 `development_only`，规则基线 15/22、无 verifier 消融 15/22，均为零模型调用。实时余额门仍未通过；不得称旧题为盲测。

## 未验收门槛

1. 用户启动 Docker Desktop 后，在仓库根目录运行 `.\scripts\run_ip1_rls.ps1`，回传脱敏的退出码和策略结果。
2. IP4 需实时查询 DeepSeek 可用余额，并按当前代码重新建立预注册锁；余额接口恢复且预算充足前，不发模型请求。历史余额快照不可代替实时查询。
3. IP3 Collector 生产部署、长期采样/保留策略尚未实测。

本审计当时未读取 `.env`、密钥或临时目录，也未发外部请求。后续曾经只读查询 DeepSeek 余额；授权仅限 22 道冻结合成题，余额接口返回 `URLError`，未发模型请求。随后离线审计固定开发集、gold、scorer 哈希，并完成零调用规则基线和 verifier 消融。详情见 [v13 单轮预检](stage-IP4-v13-full-suite-2026-11-13.md#2026-11-13-恢复预检与余额阻断) 与 `reports/ip4-v13-offline-revision-20261113-r4.json`。当前没有自动审核阻断状态。

IP1 后续离线切片补齐 QueryEngine 后端能力契约、共享核心构造入口和 SQLite adapter contract tests；43 项 SQLite/API/MCP/CLI/tenant/tracing/parity 定向回归通过。PostgreSQL/RLS integration gate 继续 pending，未启动 Docker；详情见 [IP1 查询引擎记录](stage-IP1.md#2026-11-13-离线前置切片)。

## 后续主机验证尝试（2026-11-13）

后续经授权尝试 Docker：IP-3 Compose runner 已修复并运行，Docker Hub 拉取认证 token 请求超时，容器未启动；IP-1 合成 PostgreSQL 健康检查通过，但宿主端口连接超时，RLS 未开始。仅本次 runner 创建的已退出 PostgreSQL 容器已移除；未删卷、未用私有数据、未调用模型。两项 gate 仍 pending，详见 `reports/ip3-docker-smoke-20261113.json` 与 `reports/ip1-rls-attempt-20261113.json`。前文“未启动 Docker”仅描述离线切片当时状态，不描述此次后续尝试。

## 宿主验收后续（2026-11-13）

前述离线审计后，IP-3 Compose health/HTTP smoke 与 IP-1 PostgreSQL/RLS 宿主验收均已通过。Compose 首次拉取遇 Docker Hub token 超时，使用已缓存基础镜像重试后通过。RLS 两租户各 12/12，RLS/只读/超时/池恢复有效。专用 PostgreSQL 容器已移除，未删卷；无模型调用。旧审计中的 IP-1 pending 状态由后续实测覆盖。详见 `reports/ip3-host-integration-20261113.json`。
