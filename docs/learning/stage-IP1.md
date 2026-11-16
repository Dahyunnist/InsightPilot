# IP-1：查询引擎与 PostgreSQL 适配

## 2026-11-16 离线回归复核

复核确认 PostgreSQL 宿主集成仍未在当前环境重跑；历史失败报告保持原样，不作为通过证据。本轮只增加 RLS bootstrap 静态契约检查，覆盖十张租户表的 ENABLE/FORCE RLS、可信 tenant context policy、reader SELECT 授权，并确认全局 `data_meta` 不启用 RLS。静态检查不证明 PostgreSQL 实际隔离；需要 Docker 可用宿主运行合成 canary 与查询验收。

无 gold 定向运行：查询引擎、库存周转 SQLite 契约、RLS bootstrap、canary、宿主 runner 静态检查及 IP-2 PostgreSQL integration 模块共 `27 passed, 1 skipped, 23 subtests passed`。集成测试因未设置 `INSIGHTPILOT_RUN_IP2_INVENTORY_POSTGRES_INTEGRATION=1` 跳过；未连接数据库、未启动容器。当前真实 tenant 隔离与 SQLite/PostgreSQL parity 仍未验证。

## 2026-11-14 查询数据身份超时补强

离线审查发现 PostgreSQL `read_data_identity()` 在身份查询事务内已绑定只读角色和 tenant RLS，但没有设置 `statement_timeout`；与普通查询不同，读取 `data_meta` 若被数据库锁或延迟，也可能让分析入口长时间等待。现对身份读取同样应用引擎配置的超时，并将 SQLSTATE `57014` 映射为 `QueryTimeoutError(statement_timeout)`，使运行记录准确归类。新增 75 ms 可控超时回归并核对事务设置；`tests.test_query_engine`、`tests.test_s2_policy`、`tests.test_ip1_rls_runner` 共 18 项通过。此为离线 fake connection 证据，不替代宿主 PostgreSQL 实测。

后续宿主入口增强：`scripts/run_ip1_rls.ps1` 除两租户 API/RLS 验收外，还运行 `scripts/verify_ip1_data_meta_timeout.py`。该检查用临时管理员连接锁住专用库 `public.data_meta`，用应用角色和 100 ms 引擎预算确认数据身份查询得到 `statement_timeout`，然后释放锁并确认读取恢复。3 秒 watchdog 只防止回归时脚本无限等待，watchdog 取消会判失败。报告可指定落在 `.tmp`。Runner 使用专属 Compose project，finally 仅 `down --remove-orphans`，不使用 `-v`；Compose 服务为 tmpfs，没有命名/宿主数据卷。此宿主检查尚未执行。

## 2026-11-14 双租户 RLS canary

旧 PostgreSQL 验收把相同合成源行复制给 tenant-a 与 tenant-b；逐题匹配 gold 无法发现跨租户串读。新增独立表 `public.ip1_rls_canary`，只在 `insightpilot_ip4` 专用一次性数据库中创建。管理员生成两枚随机 marker，分别写入两个租户；表启用并强制 RLS，策略使用与业务表相同的 `current_setting('app.tenant_id', true)`。应用 DSN 以只读事务切换 `insightpilot_reader`，按可信 tenant context 读 marker；同一个 max_size=1 pool 顺序复用 tenant-a、tenant-b、tenant-a，再测缺失和未知 tenant（预期零行）。同时检查 app 与 reader 都非 superuser、无 `BYPASSRLS`，且 app DSN 用户/数据库匹配。清理只 DROP 本次 canary 表；不会改指标表或查询结果。异常报告仅写异常类型，不输出 DSN、密码或 marker。

入口：`scripts/run_ip1_ip3_host_validation.ps1`；IP-1 单独入口：`scripts/run_ip1_rls.ps1 -Execute -ConfirmDatabaseCreation`。新 canary 先于 API/gold 套件运行，报告为 `ip1-tenant-rls-canary.json`。离线 7 项确定性测试覆盖 marker 可见性失败情形、缺失/未知 tenant、入口顺序与 PowerShell 解析。它们没有启动 PostgreSQL；真实 canary、角色属性和连接复用结果仍待用户在 Docker 可用宿主执行。Postgres Compose 使用专属项目与 tmpfs，退出仅清理该项目容器/网络，不删卷。

## 2026-11-13 离线前置切片

当前入口使用统一 `build_core` 构造器；`QueryEngine` 明确声明后端名与 tenant scope 能力。CLI、API、MCP 默认共享 SQLite adapter 和同一 `InsightPilot` 查询/验证路径。SQLite 仍委托既有 SQL allowlist、只读 authorizer、参数绑定、100 行上限、VM 预算、超时与取消机制。

新增 adapter contract tests 和入口装配测试：SQLite 满足 protocol、拒绝写语句/PII 列/超限查询/不支持的 tenant scope，绑定值保持在 SQL 参数外；CLI 实际走分析核心，API 与 MCP 启动也调用共同构造器。43 项定向测试通过，覆盖 QueryEngine、S1/S2、API/MCP、tenant、tracing 和 parity。没有启动 Docker 或 PostgreSQL。

**当前 PostgreSQL/RLS integration gate 仍为 pending。**下文 PostgreSQL 16.15 结果是历史运行记录，不是本次离线切片的验收证据；Docker 可用后仍需重跑当前代码的 PostgreSQL tenant/RLS 集成验证。

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

## 2026-11-13 宿主 RLS 重试

合成 PostgreSQL 容器健康检查通过，但宿主连接 `127.0.0.1:55432` 超时，RLS 验证未开始。runner 停止容器后，已确认并移除本次创建的退出容器；临时 tmpfs 未转成数据卷，也未删除卷。结果见 `reports/ip1-rls-attempt-20261113.json`。宿主 Docker 网络路径修复后仍需重跑；RLS gate 未通过也未完成。

## 2026-11-13 宿主 PostgreSQL/RLS 复验通过

后续宿主运行 `scripts/run_ip1_rls.ps1 -Execute -ConfirmDatabaseCreation` 通过；tenant-a、tenant-b 各 12/12，含 FastAPI 查询、RLS 隔离、只读约束、statement timeout、连接池恢复与 SQLite gold 核验。runner stop 发出警告，但专用测试容器状态为 `Exited (0)`，之后已精确移除；未删卷。完整汇总见 `reports/ip3-host-integration-20261113.json`，逐题记录见 `reports/ip4-postgres-verification.json`。此前 pending 记录描述的是较早失败尝试，不代表本次结果。

## 2026-11-15 当前宿主验收复核

本次运行 `scripts/run_ip1_ip3_host_validation.ps1`，结果 `status=passed`、`IP-1=passed`、`IP-3=passed`、`model_calls=0`。Docker 29.1.3。专用 Compose 测试结束后，没有残留 InsightPilot 容器。

IP-1 使用 PostgreSQL/FastAPI/`PostgresQueryEngine` 与合成数据：两个 tenant 各 12/12 用例通过；缺失租户上下文、跨租户 JOIN、DML、个人信息、prompt injection 负例均按预期拒绝或隔离；Python gold 与 SQLite 独立核验一致。独立 RLS canary 验证 reader 非 superuser 且无 `BYPASSRLS`，池连接复用时 tenant 上下文隔离；`data_meta` 100 ms statement timeout 后成功恢复。本次记录见 `reports/host-validation-ip1-ip3-2026-11-15/`，manifest SHA-256 为 `23f6b264ad119315dc68321e0cce7afa71a2380e4bddced6809fb006bab7d738`。只归档了脱敏 JSON 摘要；没有复制临时日志或绝对路径。

这是本机固定合成语料的宿主集成验收，不代表外部客户数据或生产部署。历史 pending 段落记录的是当时失败尝试；本节是 2026-11-15 当前复验。

附加本轮复现的离线回归：`tests/test_ip1_rls_canary.py tests/test_ip1_rls_runner.py` 为 7/7；`tests/test_ip1_ip3_host_entrypoint.py tests/test_ip3_compose_runner.py` 为 6/6。此次宿主 runner 本身也报告 IP-1 passed。

## 2026-11-15 PostgreSQL/RLS 宿主验收尝试

命令：` .\scripts\run_ip1_rls.ps1 -Execute -ConfirmDatabaseCreation -ArtifactDirectory 'E:\job\insightpilot-ip1-acceptance-20261115' `。Docker Engine 29.1.3；镜像 `postgres:16-alpine` digest `sha256:f4610795587492cd4fb887c2dc3de301b813278bb6f616992061141b5f15ac36`。临时凭据由 runner 生成，PostgreSQL data dir 使用 Compose tmpfs；未用生产凭据、未调用模型。

容器创建并报告 Started，但 `scripts/prepare_ip4_postgres.py` 的首次 psycopg 连接失败，runner exit 1：`connection to server at "127.0.0.1", port 55432 failed: server closed the connection unexpectedly`。因此双租户实际查询、越权、DDL/DML、超时和行预算本轮均未验收，不能称通过。Runner finally 已清理专用容器与 Compose 网络；无容器日志或校验报告生成。后续需先查 PostgreSQL 初始化/启动日志，再按相同命令重跑；本轮不自动重试。

## 2026-11-15 PostgreSQL/RLS 宿主复验

首轮容器启动后立即准备导致 psycopg 连接被服务端关闭；旧 runner 已清理容器，故启动日志无法追溯，根因未确认。runner 已改为 Compose `--wait --wait-timeout 60`、额外 `pg_isready`，失败时先保存 `ip1-postgres-startup.log` 再清理。`tests/test_ip1_rls_runner.py` 定向 5 passed。

复验命令：` .\scripts\run_ip1_rls.ps1 -Execute -ConfirmDatabaseCreation -ArtifactDirectory 'E:\job\insightpilot-ip1-acceptance-20261115-r3' `，exit 0。Docker Engine 29.1.3；PostgreSQL 16.15，镜像 digest `sha256:f4610795587492cd4fb887c2dc3de301b813278bb6f616992061141b5f15ac36`。RLS bootstrap SQL SHA-256 `b8bf95cf1e3b474d4178445226a50c879f7985c6030e3481d2c31a187296017a`。临时凭据由 runner 生成；只用合成数据与 tmpfs。

最终 `ip1-ip2-postgres-verification.json` 状态 passed：tenant-a 与 tenant-b 各 12/12；每租户 6 道可答题与 6 道 guard 全通过，PG 答案经独立 Python gold 和 SQLite 交叉核对。Canary 通过同一连接池切换 tenant-a、tenant-b、missing、unknown context；缺 tenant 在应用层拒绝，unknown/missing RLS 可见行 0。只读角色 DML 与 DDL 均拒绝；statement_timeout 实测超时，池耗尽后恢复。`ip1-data-meta-timeout.json` 100ms 超时 123.31ms，随后读取恢复。0 模型调用。

Artifacts 位于仓库外 `E:\job\insightpilot-ip1-acceptance-20261115-r3`。验收后专用容器和 Compose network 均无残留。边界：验证的是本地 PostgreSQL 16.15 与固定合成 fixture，不代表生产部署；首轮失败根因仍未知，不能归因于代码或容器竞态。

## 2026-11-15 `data_meta` 超时宿主复验

按新入口 `scripts/run_ip1_database_truth.ps1` 执行。普通权限首跑被 Docker pipe ACL 拦截，未完成数据库验收；提升权限重跑通过。脱敏汇总见 [`ip1-database-truth-host-acceptance-2026-11-15.json`](../../reports/ip1-database-truth-host-acceptance-2026-11-15.json)。

真实 PostgreSQL/FastAPI/`PostgresQueryEngine` 路径通过：tenant-a、tenant-b 查询各 12/12；独立 canary 与同一池复用的 tenant-a 只见各自 marker，missing/unknown tenant 为 0 行，缺 tenant 上下文被拒。DML 与 DDL 均被拒。`data_meta` 被排他锁占用时，100 ms `statement_timeout` 实测 126.34 ms 后超时；释放锁后身份读取恢复。模型调用 0。三个源 JSON 的 SHA-256 写入脱敏汇总；只读 `.tmp/ip1-database-truth-20261115-211203-6d6d71ea`，未改动或纳入暂存。

这次实测闭合了当前 `data_meta` 超时修复的 PostgreSQL 宿主复验项。证据仅适用于本机 PostgreSQL 和固定合成 fixture；不代表生产负载、真实租户数据或业务泛化。
