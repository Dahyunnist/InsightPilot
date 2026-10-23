# IP-1：查询引擎与 PostgreSQL 适配

## 本阶段交付

- `QueryEngine` 协议定义统一的只读查询接口。
- `SQLiteQueryEngine` 包装原有只读 URI、authorizer、VM 指令和结果行数限制。
- `PostgresQueryEngine` 只接收经过 SQLGlot AST 白名单的编译查询；把 SQLite 参数占位符转为 PostgreSQL 占位符，继续绑定参数。
- PostgreSQL 请求通过 psycopg 连接池执行只读事务，并在事务内设定 `statement_timeout`、切换到 `insightpilot_reader`、绑定受信任的 tenant id。
- `docs/postgresql-rls-bootstrap.sql` 为五张批准表启用并强制 RLS。每表均要求已有 `tenant_id`；迁移不会替现有数据猜租户归属。

## 本地验证

运行 `python -m unittest tests.test_query_engine tests.test_s2_policy -v` 验证查询适配契约、参数绑定、只读事务设定、tenant 上下文、超时映射、行数预算，以及 SQLite 原有策略。

这些测试使用 fake psycopg pool，证明 adapter 按预期发出约束；不证明 PostgreSQL 服务已执行 RLS。当前环境没有 psycopg 包，Docker daemon 也无法连接。因此本阶段未完成 PostgreSQL 实例验收、SQLite/PostgreSQL 数值一致性、跨租户 JOIN、真实角色权限、DDL/DML 拒绝和数据库超时实测。

## 边界与下一步

当前 `InsightPilot` 默认路径仍是 SQLite；PostgreSQL adapter 已实现，但尚未接入 API、数据版本读取和独立 verifier。不能把 adapter 单测称为端到端多数据库支持。

部署前，DB owner 必须为合成数据补齐并核对 `tenant_id`，执行 bootstrap SQL，再把应用登录角色加入 `insightpilot_reader`。tenant id 必须来自认证上下文，不能接受模型或用户自由指定。接入 API 前需补 PostgreSQL 集成 fixture，实测跨租户 JOIN、无租户上下文、只读角色、超时和 SQLite/PostgreSQL 数值一致性。

本阶段不调用模型，不产生模型费用。账本未变。

## IP4 PostgreSQL 验证补记（2026-10-23）

使用 PostgreSQL 16.15 一次性容器与 `scripts/verify_ip4_postgres_psql.py` 直接执行 SQLGlot 转换后的查询。退款订单率、退款金额率、退款活动归因、营销 ROI 三种查询，在 tenant-a 和 tenant-b 各 6/6 匹配独立 Python gold；数据重复导入到两个租户，避免空租户造成的假通过。无 tenant 上下文 fail-closed，应用角色 DML 被拒，statement timeout 被触发且后续查询成功。

这补过真实 PostgreSQL/RLS SQL 验证，但没有运行 Python `PostgresQueryEngine` 的 psycopg 连接池。当前虚拟环境缺 psycopg，PyPI 安装超时；SQLite `InsightPilot` 端到端与 PostgreSQL adapter pool exhaustion 仍未验收。
