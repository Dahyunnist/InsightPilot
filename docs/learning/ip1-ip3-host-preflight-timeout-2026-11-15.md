# IP-1/IP-3 宿主门禁超时保护（2026-11-15）

## 缺口

统一宿主验收入口此前直接同步执行 `docker info --format '{{.ServerVersion}}'`。Docker CLI 可启动但 daemon 无响应时，脚本会一直停在前置检查，既不产出最终摘要，也不清楚说明没有进入 IP-1/IP-3 阶段。

## 修复

入口现在通过隐藏子进程运行 Docker 前置检查，等待上限 15 秒。超时后终止检查进程，最多再等 5 秒收尾；随后清理原始 stdout/stderr 文件，写入脱敏日志和 `blocked_docker_preflight_timeout` 摘要，并返回退出码 2。前置检查未通过时不会启动 Compose/PostgreSQL 阶段。daemon 快速返回失败时，继续使用 `blocked_docker_daemon_unavailable`。

改动不触碰数据库、冻结题集、评测评分、模型客户端或费用账本。

## 验证

`tests/test_ip1_ip3_host_entrypoint.py`：4 项通过，包含单次前置检查顺序、15 秒上限、超时终止分支、脱敏/隐藏窗口约束和 Windows PowerShell 解析。

本次只运行离线回归，没有启动 Docker，也没有调用模型。此前宿主报告仍按原时间和代码版本解释；此修复需随下一次 Docker 可用时的宿主验收一并实测。
