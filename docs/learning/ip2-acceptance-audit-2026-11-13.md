# IP2 验收映射审计（2026-11-13）

验证命令：`.venv\Scripts\python.exe -m unittest tests.test_multidomain tests.test_s5_semantics`，21/21 通过。全程使用合成 SQLite fixture；未调用模型或启动容器。

| 计划项 | 代码/测试证据 | 状态与边界 |
| --- | --- | --- |
| 指标版本、生效日、分子/分母、维度、来源、时区、freshness、血缘 | `insightpilot/metrics.py`：`Metric` 与 `METRICS`；`tests/test_s5_semantics.py` | 目录契约已测；不是外部语义治理系统。 |
| 第二业务域营销净 ROI | `data/multidomain_seed.py`、`insightpilot/marketing_oracle.py`、`insightpilot/sql.py`；`tests/test_multidomain.py` | 合成订单、退款、归因与 spend 多表 JOIN；SQLite 查询与独立 Python oracle 复核。 |
| 多触点归因、费用按 business_event 去重及修订/void | 同上；`tests/test_multidomain.py::test_recorded_refund_reversal_increases_net_revenue` 与重复费用/修订断言 | 固定合成 seed 覆盖；不代表生产分布。 |
| 迟到事件、快照后退款撤销 | `data/multidomain_seed.py`、`insightpilot/marketing_oracle.py`；`tests/test_multidomain.py::test_refund_reversal_recorded_after_snapshot_is_excluded` | 仅 SQLite 合成快照语义。 |
| UTC 左闭右开边界 | `insightpilot/sql.py`；`tests/test_multidomain.py::test_utc_end_is_exclusive_for_spend_events` | 已在合成数据验证。 |
| 无归因、零费用与 NULL ROI | `insightpilot/marketing_oracle.py`、`insightpilot/sql.py`；`tests/test_multidomain.py` 中未归因/无费用断言 | NULL 表示不可计算，不当作零。 |
| 指标歧义澄清 | `insightpilot/planner.py`；`tests/test_multidomain.py::test_ambiguous_refund_rate_requires_clarification` | 断言模糊退款率在规划器调用前澄清。 |
| 冻结数据与独立 gold | `data/build_marketing_roi_holdout.py`、`evals/marketing-roi-manifest-v1.json` | 小型合成集用于契约回归；不估计总体准确率。 |

跨 PostgreSQL/RLS 的历史复验记录见 `reports/ip1-ip2-postgres-acceptance-v1.json` 与 `docs/learning/stage-IP1.md`。本次未启动真实服务；该报告不等于本轮重跑 PostgreSQL，也不证明生产部署。

## IP3 最关键剩余项

当前主要待验项是 Docker Compose/PostgreSQL 宿主实跑及持续服务边界；IP3 的 API/MCP scope parity、幂等、取消/限流和本地 trace 已有合成测试。Collector 生产部署/保留策略未验证。不得把 SQLite 或单次本机 smoke 外推成生产负载结论。
