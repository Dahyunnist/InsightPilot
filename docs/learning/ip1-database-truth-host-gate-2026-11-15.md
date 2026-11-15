# IP-1 PostgreSQL 真实性验收入口（2026-11-15）

## 缺口

`read_data_identity()` 最近把 `statement_timeout` 加到 PostgreSQL `data_meta` 读取。Fake connection 回归通过，但这不能证明真实 PostgreSQL 锁等待会超时，也不能证明连接池/事务随后恢复。

## 路线复核

IP-1/2 的 2026-10-23 路线要求 PostgreSQL 权限、双租户 RLS 与故障恢复；这些已有历史真实报告。当前 `data_meta` 超时改动需要按当前代码复验。IP-3 供应商请求取消与 IP-4 新模型评测依赖外部条件，本轮不做。

## 宿主命令

在 `E:\job\insightpilot` 的 PowerShell 运行：

```powershell
.\scripts\run_ip1_database_truth.ps1
```

入口调用现有 `run_ip1_rls.ps1`，使用 PostgreSQL Compose 的 `127.0.0.1` 端口与 `tmpfs` 数据目录。Compose `pull_policy: never`，只使用本地已有镜像，不拉取镜像。它只装载仓库合成数据，runner 为本次进程生成临时凭据，不读 `.env`。固定容器名冲突时会拒绝启动。结束清理只针对专属 Compose project，不带 `-v`；Compose 未声明数据卷。

## 验收门

- PostgreSQL 真实连接与两租户业务查询均通过。
- tenant-a、tenant-b、同一池复用后的 tenant-a 各只看见自身 canary marker；缺失/未知 tenant 见 0 行。
- 应用角色 DML/DDL 被拒绝，缺失 tenant 上下文被拒绝。
- 锁住 `data_meta` 时身份查询以 SQLSTATE 对应的 `statement_timeout` 失败；释放锁后身份读取恢复。
- 模型调用为 0。wrapper 只输出脱敏摘要；详细报告与 runner 日志写入新的 `.tmp/ip1-database-truth-*` 目录。

预期摘要字段：`status=passed`、`postgresql_connection=passed`、`tenant_rls_positive` 显示两个租户隔离、`tenant_rls_negative` 显示 0 行、`data_meta_statement_timeout=timed_out`、`identity_recovery=recovered`、`model_calls=0`。

## 已完成检查与边界

新增静态测试检查唯一入口调用、报告 gate、loopback 绑定、tmpfs、无 volume 删除和 PowerShell 语法。运行 `.venv/Scripts/python.exe -m unittest tests.test_ip1_database_truth_runner tests.test_ip1_rls_runner -v`：`7 tests` 通过；测试不启动 Docker。只有用户执行上面的宿主命令后，才能报告真实 PostgreSQL 结果。失败时检查 `.tmp` 下报告与日志；不要复制凭据、DSN 或原始启动环境到聊天。
