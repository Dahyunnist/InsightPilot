# IP1 PostgreSQL RLS 宿主验收

`scripts/run_ip1_rls.ps1` 只使用合成 PostgreSQL Compose 服务。缺省密码由本次 PowerShell 进程临时生成，不读取 `.env`；结束时只停止 `postgres` 服务，不删除卷。Docker daemon 不可用时脚本非零退出并提示启动 Docker Desktop。

静态回归核对 runner 清理范围及 RLS SQL 的 ENABLE/FORCE、tenant context 和只读角色。此处不代表宿主 PostgreSQL/RLS 已通过；需在仓库根目录运行 `.\scripts\run_ip1_rls.ps1` 并检查退出码与验收报告。

## 宿主操作与清理

在仓库根目录运行 `.\scripts\run_ip1_rls.ps1` 默认只做 dry-run。宿主验收命令：`.\scripts\run_ip1_rls.ps1 -Execute -ConfirmDatabaseCreation`。Runner 先拒绝同名容器，再设置进程级临时凭据；创建合成库后先运行 `prepare_ip4_postgres.py`，再运行 RLS verifier。finally 只停止本次目标服务，不删除卷。即使 up 返回失败，也会尝试 stop；stop 失败只输出通用警告，不覆盖原错误。静态测试命令：`.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_ip1_rls_runner.py'`。静态测试核顺序、环境变量恢复路径与清理边界；未运行 Docker 宿主集成。验收证据需含两租户隔离、无 tenant 拒绝、DDL/DML 拒绝、超时与池恢复；不要粘贴密码或 DSN。

本机可独立运行 `tests/test_ip1_rls_config_guard.ps1`，覆盖 loopback 正确绑定、非回环地址、错误端口和无效 JSON；该测试不启动容器。
