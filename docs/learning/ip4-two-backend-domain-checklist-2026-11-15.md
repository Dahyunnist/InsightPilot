# IP-4 双数据库、双业务域验收清单（2026-11-15）

## 范围

只核对 synthetic IP-4 refunds 与 marketing ROI。区分 v13 冻结集与 v14 独立候选；不把规则基线写成模型成绩，也不把 v13/v14 合并计分。

| 版本 / 域 | SQLite | PostgreSQL tenant-a | PostgreSQL tenant-b | 结论 |
| --- | --- | --- | --- | --- |
| v13 refunds（12 题） | Python oracle/只读 SQL 与规则基线有离线证据；baseline 15/22 总分的一部分 | 缺 v13 同哈希逐题报告 | 缺 v13 同哈希逐题报告 | 不完整 |
| v13 marketing ROI（10 题） | 独立 Python event oracle、SQL 交叉核对与规则基线有离线证据 | 缺 v13 同哈希逐题报告 | 缺 v13 同哈希逐题报告 | 不完整 |
| v14 refunds（11 题） | Python oracle、SQL 交叉核对；RulePlanner 3/11 | 已归档报告显示 22 题评分向量与 SQLite 一致 | 已归档报告显示 22 题评分向量与 SQLite 一致 | parity 报告通过；候选待审 |
| v14 marketing ROI（11 题） | 两套 Python oracle、SQL 交叉核对；RulePlanner 5/11 | 已归档报告显示 22 题评分向量与 SQLite 一致 | 已归档报告显示 22 题评分向量与 SQLite 一致 | parity 报告通过；候选待审 |

## 证据审阅

- v13 manifest/blind/gold 哈希已与 r5 锁核对；离线 acceptance audit 因执行代码锁失配和缺 v13 PostgreSQL 同哈希证据，状态为 `blocked`。旧 12 题 PostgreSQL 报告不覆盖 v13 的 22 题。
- v14 摘要 `reports/ip4-v14-postgres-parity-summary-2026-11-15.json` 校验源 parity 报告 SHA；摘要标记 PostgreSQL 16.15、两个 tenant、22 题 score vector 与 SQLite 全匹配。原始逐题 parity 文件位于候选目录之外，没有纳入仓库；本次没有重新启动 PostgreSQL。
- v14 SQLite gold 由独立 oracle 与只读 SQL 交叉核验。v14 PostgreSQL parity 证明规则评分向量一致，不证明规则基线高准确率：总分 8/22，refunds 3/11，marketing ROI 5/11。
- `tests/test_ip4_v14_postgres_integration.py` 是显式 opt-in，默认跳过；不得把 mock tenant adapter 测试当作 live PostgreSQL。
- IP-1 的一般 RLS/只读/连接池证据另见 `reports/host-validation-ip1-ip3-2026-11-15/` 与 `reports/ip1-ip2-postgres-acceptance-v1.json`。这些证据不替代 v13 固定 hash parity。

## 本轮验证

使用项目 `.venv` 运行 IP-4 holdout、离线 bundle、v14 candidate/evaluator/parity-summary、PostgreSQL opt-in gate、IP-1 RLS canary/runner、QueryEngine 和多域测试：**56 passed、1 skipped、24 subtests passed**。跳过项是需要显式 opt-in、会准备专用 PostgreSQL 测试库的 live v14 integration。未启动 Docker、未查询余额、未调用模型；没有改题目、gold 或评分语义。

## 未完成项

1. v13：在当前审核的代码 revision 上，需补同一 v13 frozen hash 的 SQLite、PostgreSQL tenant-a、tenant-b 逐题 parity；旧 r5 执行代码锁也须重新审阅。模型评测仍受锁和审批门槛约束。
2. v14：所有者仍需审核题目与 gold 后，才可决定是否把候选正式冻结。现存 PostgreSQL 摘要不能替代该审核。
3. 生产数据、外部客户准确率、持续负载和 SLA 不在本清单证据范围内。

因此，IP-4 双后端验收整体仍未完成；只有 v14 候选有已归档双租户 parity 证据。
