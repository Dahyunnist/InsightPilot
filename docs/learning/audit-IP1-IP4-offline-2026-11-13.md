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

本审计不读取 `.env`、密钥或临时目录，不发外部请求。
