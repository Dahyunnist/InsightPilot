# IP1 / IP3 安全测试覆盖矩阵

## 本轮验证

- `tests/test_query_engine.py`：9项通过。覆盖 PostgreSQL tenant context、只读事务、reader role、tenant GUC、statement timeout、结果预算及 SQL policy 对 DML/DDL 和敏感列的拒绝。均为 mock connection；未连接真实 PostgreSQL，未验证真实 RLS 策略。
- `tests/test_s6_runtime.py::test_run_survives_store_reopen_and_redacts_personal_identifiers`：通过。验证运行记录重开与脱敏。
- `tests/test_s6_runtime.py::test_startup_marks_orphaned_work_as_interrupted`：通过。验证孤儿运行恢复。
- 合计 11 项通过，另有 5 个 query-engine 子用例通过。

## 未完成验证

- `tests/test_api_tenant_identity.py::ApiTenantIdentityTests::test_authenticated_tokens_route_concurrent_requests_without_header_override`：未通过也未失败。Windows Python 3.14 的栈转储显示卡在 `TestClient.__enter__` 初始化 AnyIO Proactor self-pipe，尚未进入测试断言。
- 幂等、请求超时取消及完整 S6 API 测试：未完成运行。Python 3.11.15 环境缺少 FastAPI；未安装依赖。
- 未做真实 PostgreSQL/RLS、多进程或 live 并发验证。

## Python 3.11 待运行

在装齐项目依赖的 Python 3.11 环境运行：

```powershell
python -m pytest tests/test_api_tenant_identity.py::ApiTenantIdentityTests::test_authenticated_tokens_route_concurrent_requests_without_header_override -vv
python -m pytest tests/test_s6_runtime.py::S6RuntimeTests::test_api_idempotency_replays_result_and_rejects_changed_question -vv
python -m pytest tests/test_s6_runtime.py -q
```
