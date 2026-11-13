# IP1 PostgreSQL RLS 宿主验收

`scripts/run_ip1_rls.ps1` 只使用合成 PostgreSQL Compose 服务。缺省密码由本次 PowerShell 进程临时生成，不读取 `.env`；结束时只停止 `postgres` 服务，不删除卷。Docker daemon 不可用时脚本非零退出并提示启动 Docker Desktop。

静态回归核对 runner 清理范围及 RLS SQL 的 ENABLE/FORCE、tenant context 和只读角色。此处不代表宿主 PostgreSQL/RLS 已通过；需用户运行 `Set-Location E:\job\insightpilot; .\scripts\run_ip1_rls.ps1` 并检查退出码与验收报告。

## 宿主操作与清理

默认运行 `.\scripts\run_ip1_rls.ps1` 只做 dry-run：校验 Compose 并确认目标绑定 `127.0.0.1:55432`，不启动服务。真实执行须显式传入 `-Execute -ConfirmDatabaseCreation`。脚本拒绝非本机端口映射；测试数据位于容器 tmpfs。完成后 `finally` 只停止 `postgres`，不删除卷、不运行 `down -v`。验收证据应包含退出码、两租户隔离、无 tenant 拒绝、只读 DDL/DML 拒绝、statement timeout 与连接池恢复结果；不要粘贴密码或连接串。
