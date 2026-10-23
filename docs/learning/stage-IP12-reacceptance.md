# IP-1/IP-2 PostgreSQL 权限与服务复验

日期：2026-10-23。只使用仓库内固定 seed 的合成数据；未读取或上传私人/客户数据，也未调用模型。

## 验收入口

在项目根目录执行：

```powershell
.\scripts\verify_final_acceptance.ps1
```

脚本启动 PostgreSQL 16.15 的一次性 Compose 容器，将 50,000 单合成数据分别复制到 tenant-a 和 tenant-b。它运行真实数据库权限负例、R01/R02 开发副本、refunds/marketing ROI 规则集，再校验旧 IP4 冻结哈希和保存的盲评报告。临时容器使用 tmpfs，脚本结束即移除。详细负例结果：`reports/ip1-ip2-postgres-acceptance-v1.json`。

费用账本已先检查：当前总上限 $4，历史执行后可用 $3.69709005。本阶段请求数为 0，费用 $0；账本没有改写，也无需模型调用预留。

## 数据库强制权限

bootstrap 把 reader 设为 `NOLOGIN/NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOINHERIT/NOBYPASSRLS`。应用登录角色不是超级用户或 RLS bypass 角色，只能显式 `SET LOCAL ROLE insightpilot_reader`。

八张租户数据表均启用并强制 RLS：orders、refunds、products、customers、campaign_attributions、campaigns、campaign_spend_events、refund_reversals。策略使用事务本地 `app.tenant_id`；应用不能从请求参数自行选租户。reader 仅获得分析 SQL 所需列的 SELECT，不含 `orders.customer_id`、`customers.email` 或 `customers.phone`。customers 表没有 SELECT 授权。`data_meta` 是唯一例外：它是全局数据版本元数据，不含租户事实，只授予 `key,value` 读取。

以真实 PostgreSQL reader role 验证：

- 两个租户各自只能读到自己的 50,000 行；直接指定对方 tenant 和跨租户 orders/refunds JOIN 都返回零行。
- 缺 tenant 上下文时租户表返回零行。
- 数据库拒绝敏感列 SELECT、持久表 DELETE、`CREATE TABLE`；只读事务也拒绝临时表 DDL。
- 注入样例作为绑定参数处理；SQL 文本不包含 payload，PG 与 SQLite 均返回相同空结果。
- 同一连接池依次运行两个 tenant 后，事务本地 tenant GUC 清空。单连接池耗尽后可恢复；50 ms statement timeout 后合法查询可恢复。
- 注入的外部连接池由调用方持有。关闭查询引擎不再关闭该池，并有单元回归验证。

## IP-2 指标、边界与双后端

沿用版本化 `insightpilot-semantic-catalog-v2` 和营销净 ROI 第二业务域。固定领域验证集每域 6 题：4 可答、2 guard；SQLite、PostgreSQL tenant-a、tenant-b 六臂全部 6/6。数据来自合成 seed，不足以估计客户总体能力。

新增离线回归覆盖退款率歧义必须澄清，且调用模型前停止；新增快照后入库的退款撤销不影响快照结果。PostgreSQL 实测 10 月 31 日有边界费用事件，日期右端采用不含端点的口径；全月与截止至 10 月 31 日零点的费用不同。合法营销 ROI 查询在 PostgreSQL 与 SQLite 聚合结果一致，并匹配独立校验。

全套 unittest：97/97 通过。分域集和旧 IP4 冻结评测文件均未改。旧模型 IP4 仍保留原结果：SQLite 12/12、tenant-b 12/12、tenant-a 10/12；IP4-R01/R02 的失败阶段仍为 query。

新诊断证明当前临时 PG fixture 和确定性规则路径通过，不证明旧模型失败已修复或解释了根因。旧报告没有保存失败 SQL 与脱敏数据库错误，故因果关系未知；冻结结果保持不变。

## 范围限制

这是单版本容器、两个重复装载的合成租户与固定指标题集。它验证本地 RLS、列授权、查询隔离和应用路径，不代表生产部署审计、跨版本兼容、负载能力或客户数据泛化。
