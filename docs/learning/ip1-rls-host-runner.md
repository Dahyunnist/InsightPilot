# IP1 PostgreSQL RLS 宿主验收

`scripts/run_ip1_rls.ps1` 只使用合成 PostgreSQL Compose 服务。每次生成随机项目名、容器名和临时凭据；canary marker 也随机生成。Compose 只绑定 `127.0.0.1:55432`，数据库使用 tmpfs，不挂持久卷。脚本不读取 `.env`；结束时只停止本次随机项目，不删除卷或其他容器。Docker daemon 不可用时脚本非零退出并提示启动 Docker Desktop。

`tests/test_ip1_postgres_rls_integration.py` 是显式 opt-in 的真实 PostgreSQL 测试；runner 在合成库装载后执行它。覆盖 tenant-a/tenant-b 同租户读取成功、跨租户行不可见与 join 无跨租户结果、reader 写入和 DDL 拒绝、只读事务超时。未设置 opt-in 时 pytest 会 skip；静态测试不等于宿主数据库证据。

## 宿主操作与清理

在仓库根目录运行 `.\scripts\run_ip1_rls.ps1` 默认只做 dry-run，不启动 Docker。宿主验收命令：`.\scripts\run_ip1_rls.ps1 -Execute -ConfirmDatabaseCreation`。Runner 只接受专用 Compose 测试 DB，创建合成库后运行双租户集成测试、canary 和已有验证器。finally 只停止本次随机项目，不删除卷；即使 up 失败也只对该项目执行清理。静态测试命令：`.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_ip1_rls_runner.py'`。需宿主实际执行后才能声称 PostgreSQL/RLS 通过；不要粘贴密码或 DSN。

证据解释：SQL allowlist 是应用层 guard，拒绝危险查询语法；RLS 是数据库层隔离，允许查询但按 transaction-local `app.tenant_id` 过滤行。跨租户 join 测试期待结果集为空，不代表数据库拒绝 SQL 语句。读写测试另验证 `SET TRANSACTION READ ONLY` 与 reader 权限。

本机可独立运行 `tests/test_ip1_rls_config_guard.ps1`，覆盖 loopback 正确绑定、非回环地址、错误端口和无效 JSON；该测试不启动容器。
