# IP-1：查询引擎与 PostgreSQL 适配

## 本阶段交付

- `QueryEngine` 协议定义统一的只读查询接口。
- `SQLiteQueryEngine` 包装原有只读 URI、authorizer、VM 指令和结果行数限制。
- `PostgresQueryEngine` 只接收经过 SQLGlot AST 白名单的编译查询；把 SQLite 参数占位符转为 PostgreSQL 占位符，继续绑定参数。
- PostgreSQL 请求通过 psycopg 连接池执行只读事务，并在事务内设定 `statement_timeout`、切换到 `insightpilot_reader`、绑定受信任的 tenant id。
- `docs/postgresql-rls-bootstrap.sql` 为八张租户表启用并强制 RLS，给 reader 角色逐列授予查询所需字段。PII 表无 SELECT 授权；全局 `data_meta` 只开放口径/版本键值。迁移不会替现有数据猜租户归属。

## 验证

`python -m unittest tests.test_query_engine tests.test_s2_policy -v` 覆盖 adapter 契约、参数绑定、只读事务、tenant 上下文、超时映射、行数预算和 SQLite 策略。应用集成后，相关定向测试 24 项通过；全套结果见最终验收记录。

PostgreSQL 16.15 经真实 FastAPI → InsightPilot → `PostgresQueryEngine` 路径验证。双租户各 12/12；PG 与独立 SQLite verifier 的版本/fingerprint 一致；RLS JOIN、无 tenant 拒绝、DML 拒绝、statement timeout、连接池耗尽与恢复均通过。完整报告：`reports/ip4-postgres-verification.json`。

## 边界与部署

默认配置仍使用 SQLite。设置 `INSIGHTPILOT_DATABASE_URL` 与 `INSIGHTPILOT_API_TENANT_TOKENS` 后，FastAPI 使用 PostgreSQL。后者是服务端 JSON 映射，将 bearer credential 映射到 tenant ID；请求体、问题和 `X-Tenant-ID` 均不能覆盖。未知或缺失 credential 返回 401，PostgreSQL 模式缺少映射时拒绝启动。每个请求把认证映射所得 tenant 显式传入只读查询，不修改共享核心或连接池状态。PG 数据身份必须与 SQLite verifier 一致，否则请求失败。

部署前，DB owner 必须为数据补齐并核对 `tenant_id`，执行 bootstrap SQL，再把应用登录角色加入 `insightpilot_reader`。tenant id 必须来自可信认证上下文。默认 SQLite 及其只读限制保持兼容。

本阶段不调用模型，不产生模型费用。账本未变。

## IP4 PostgreSQL 验证补记（2026-10-23）

使用 PostgreSQL 16.15 一次性容器与 `scripts/verify_ip4_postgres_psql.py` 直接执行 SQLGlot 转换后的查询。退款订单率、退款金额率、退款活动归因、营销 ROI 三种查询，在 tenant-a 和 tenant-b 各 6/6 匹配独立 Python gold；数据重复导入到两个租户，避免空租户造成的假通过。无 tenant 上下文 fail-closed，应用角色 DML 被拒，statement timeout 被触发且后续查询成功。

随后完成应用层集成：`InsightPilot` 接受 `QueryEngine`，默认仍用 SQLite。设置 `INSIGHTPILOT_DATABASE_URL` 与服务端 `INSIGHTPILOT_API_TENANT_TOKENS` 后，FastAPI 使用 PostgreSQL。认证 token 映射到可信 tenant ID；每次 PG 查询在独立只读事务中绑定 RLS tenant，不从 HTTP 请求字段取租户。PostgreSQL `data_meta` 必须与 SQLite verifier 的版本/fingerprint 完全一致。API shutdown 关闭连接池。

检查所有本地 venv/cache 没找到 psycopg wheel；按审批联网安装 `psycopg[binary,pool]>=3.2,<4` 成功，版本为 psycopg 3.3.6、binary 3.3.6、pool 3.3.3。随后运行 `scripts/verify_ip4_postgres.py`，经过 FastAPI → InsightPilot → PostgresQueryEngine 实测：同一 FastAPI app 映射两个 tenant，各 12/12 题通过；退款 6/6、营销 ROI 6/6；R03 退款归因 JOIN 与 M01 ROI JOIN 在两租户下都匹配 gold，证明没有跨租户 JOIN 泄漏；PG/SQLite 数据版本和 fingerprint 相同；独立 SQLite verifier 全部通过。预期拒答 3/3，错误拒答 0，拒答问题被接受 0，不支持地区被接受 0。

同一驱动测试还确认缺 tenant 上下文被拒、应用只读角色的 DELETE 被数据库拒绝、50 ms `statement_timeout` 生效且后续查询恢复、max_size=1 pool 耗尽按 200 ms timeout 失败并在释放连接后恢复。完整应用层 PostgreSQL/RLS 查询路径现已实测。Docker 只在提权后可访问 daemon；测试库使用 Compose 的 tmpfs 临时盘，已停止，不留数据卷。

## IP-1/IP-2 数据库权限复验（2026-10-23）

新报告 `reports/ip1-ip2-postgres-acceptance-v1.json` 在 PostgreSQL 临时库上直接以非超级用户应用角色执行：`insightpilot_reader` 是 `NOLOGIN/NOSUPERUSER/NOBYPASSRLS`；八张含租户数据的表均开启并 `FORCE ROW LEVEL SECURITY`；`orders.customer_id`、`customers.email`、`customers.phone` 没有列级授权，reader 直接读取均被 PostgreSQL 拒绝。`data_meta` 是全局版本元数据，不含租户事实；它保留仅 `key,value` 的 SELECT 授权。

部署影响：bootstrap 会撤销这些表授予 `PUBLIC` 的权限，也会撤销 `public` schema 对 `PUBLIC` 的 CREATE。依赖这些宽泛默认权限的其他应用需改用独立 schema 或显式授权。

两个 tenant 均只能看到自己的 50,000 个订单；显式读取对方 tenant 和 JOIN 对方退款均返回零行。缺租户上下文返回零行；应用层不接受缺失主体。reader 的 DELETE、CREATE TABLE 和敏感列 SELECT 均由数据库拒绝。参数注入字符串只作为绑定值，PostgreSQL 与 SQLite 返回一致结果。

同一营销 ROI 查询在 SQLite 与 PostgreSQL 数值一致。UTC 截止日右开、10 月 31 日费用边界以及快照后入库的退款撤销均有回归；同一单连接池连续切换 tenant 后，事务本地 tenant GUC 已清空。50 ms statement timeout 后查询可恢复；单连接池耗尽后也能恢复。传入的连接池由调用方拥有，关闭共享查询引擎不会关闭它。

测试数据为合成固定 seed，每租户 50,000 单；不是独立客户样本。分域规则回归每域 6 题（4 可答、2 guard），SQLite 和两个 PostgreSQL tenant 共六臂全通过。没有模型请求，费用账本保持不变。

## 认证主体隔离补记（2026-10-23）

移除部署级全局 tenant 选择。`INSIGHTPILOT_API_TENANT_TOKENS` 配置 bearer token 到 tenant 的服务端映射；PG 模式无映射时拒绝启动。缺失/未知 token 均返回 401，伪造 `X-Tenant-ID` 不影响映射。tenant 随分析任务传入 `read_data_identity` 与 `execute_readonly`，每次 transaction-local RLS 设置独立。并发 API 测试在共享核心下同时发两个 tenant 请求，确认两次查询收到各自映射；RunStore 读取与取消也按 principal 隔离。IP4 PostgreSQL 脚本现由同一 API app、同一核心和连接池依次跑完两租户盲集。

后续并发回归扩到 12 个同时交错请求、两 token 各 6 个。每个请求带相反的伪造 `X-Tenant-ID`；核心收到的 tenant 仍与 bearer 映射逐项一致。API 测试也核对跨租户 run 读取为 404、幂等键彼此隔离。数据库错误响应附脱敏 `failure_detail`：层、类别、异常类、后端、tenant context 是否绑定、SQL SHA-256 和参数数量；不含 SQL、参数、tenant ID、DSN 或底层异常文本。
