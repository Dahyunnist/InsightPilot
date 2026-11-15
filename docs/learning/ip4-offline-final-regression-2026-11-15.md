# IP-4 最终离线回归状态审计（2026-11-15）

## 已确认

- `python -m pytest -q -p no:cacheprovider tests/test_ip4_v13_offline_audit.py`：3 passed，0.78s，exit 0。
- `python -m pytest -q -p no:cacheprovider tests/test_ip4_v13_blind_payload_contract.py`：2 passed、22 subtests passed，0.06s，exit 0。
- `python -m pytest -vv -x -p no:cacheprovider --maxfail=1 --collect-only`：213 tests collected，1.19s，exit 0。此为收集，不是执行。

## 未通过或未完成

- 全套 `python -m pytest -q -p no:cacheprovider --basetemp=.pytest-final-offline`：约 40 秒无输出后中断，exit 1；原因未知，不得记为通过。
- 较早跨文件安全子集运行输出 15 个点后，工具未提供 pytest 汇总或退出码；结果未知，不计入通过数。
- 本轮没有执行全量前 20 项；因此没有定位卡点用例。没有触网、启动 Docker 或调用模型。

已有 pytest 临时目录未访问或修改。以上结果仅记录可核验的命令输出。

## 2026-11-15 前 20 项挂起定位

`pytest --collect-only` 确认全套共 213 项。按收集顺序运行前 20 项：30 秒到期，无 pytest 终止摘要；最后停在 `tests/test_api_tenant_identity.py::ApiTenantIdentityTests::test_authenticated_tokens_route_concurrent_requests_without_header_override`。

单独复跑该测试并设置 `-o faulthandler_timeout=10`：10 秒堆栈显示 `socket.accept` → `socket._fallback_socketpair` → `asyncio.proactor_events._make_self_pipe`，线程位于 AnyIO `run_blocking_portal`，等待事件循环启动。此证据支持 Windows asyncio socketpair 环境挂起，不支持测试断言失败结论。命令在 15 秒执行窗口内只产生超时堆栈，未获最终 pytest 退出摘要；不计通过。

此前两份离线测试分别通过：`tests/test_ip4_v13_offline_audit.py` 3 passed（0.78s，exit 0）；`tests/test_ip4_v13_blind_payload_contract.py` 2 passed、22 subtests passed（0.06s，exit 0）。全量依旧中断，原因以单测堆栈证据记录；未改生产代码，未触网、未运行 Docker/模型。

## 2026-11-15 容器内 API 并发诊断

使用本机 `insightpilot-ip3-compose-api-probe:latest`，仓库只读挂载、`--network none`、`--rm`；`INSIGHTPILOT_DATABASE_PATH` 指向容器 `/tmp`。执行 `scripts/load_api.py --delay-ms 25`，exit 0，0 模型调用。结果：并发 1 为 1/1 HTTP 200；并发 5 为 5/5 HTTP 200；并发 20 为 8 HTTP 200、12 HTTP 429。该脚本是固定 planner + 合成 SQLite 的 in-process HTTP load probe，不是 pytest，也不证明真实 PostgreSQL、MCP 或完整服务部署行为。第一次只读文件系统探针未设可写 DB 路径，失败于 SQLite 文件创建；本次临时 DB 路径修正后通过。
