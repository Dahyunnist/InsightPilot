# IP-3 Windows asyncio 冒烟阻塞记录（2026-11-15）

## 环境核对

仓库 `.venv` 可导入 smoke 所需依赖：FastAPI 0.146.0、Uvicorn 0.54.0、SQLGlot 30.23.0、HTTPX 0.28.1。解释器是 CPython 3.14.5；`py -0p` 只列出该 Python 3.14 安装。本轮不安装依赖，也没有可用的 Python 3.12 环境。

## 运行结果与边界

本轮不重复运行 quickstart：先前的 `scripts/smoke_local.py` 已在同一 `.venv` 下失败，Uvicorn 没能在 5 秒内启动本机 API，随后报告服务线程未能正常关闭。取消/恢复测试在该 Windows asyncio 环境也曾卡在首项；本轮限时复跑 MCP 取消测试仍未输出结果，随后停止。未将这些尝试计作通过。

`smoke_local.py` 的清理已有界限：设置 `server.should_exit`，首次 `join(timeout=5)`；线程仍存活时设置 `server.force_exit`，再 `join(timeout=5)`；最后关闭 listener 并抛出明确错误。最坏等待约 10 秒，不存在无界线程 join。`run_ip1_ip3_host_validation.ps1` 的 Docker daemon 检查也已有 15 秒等待和最多 5 秒进程收尾。当前证据指向 Windows Python 3.14 asyncio 启动/退出兼容问题；本轮不改分析或服务功能语义。

## 下一步

在已有 Python 3.12 Linux/容器环境，或 Python 3.14 asyncio 问题修复后的环境，重跑 `tests/test_ip3_database_recovery.py`、`tests/test_ip3_mcp_cancellation.py` 和 `scripts/smoke_local.py`。不需要真实模型、私人数据或新增依赖。通过前，IP-3 quickstart 本机 HTTP smoke 保持未验收。
