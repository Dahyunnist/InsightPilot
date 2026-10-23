# IP-2：指标目录 v2 与第二业务域

## 交付

- 指标目录升至 `insightpilot-semantic-catalog-v2`，每项公开业务域、版本、生效日、分子、分母、时区、freshness、来源表、字段血缘、合法维度和 access scope。
- 新增营销净 ROI：`(按触点权重分摊的订单净收入 - 去重活动费用) / 去重活动费用`。
- 费用表按 `business_event_id` 去重，选择最新 `revision`、`recorded_at`、`event_id`；`void` 修订撤销事件。
- 批准退款从净收入中扣除；只计数据快照前入库的退款撤销。订单与费用使用 UTC 左闭右开日期。
- 缺失 campaign touch 进入“未归因”；没有费用的组返回 `null` ROI。费用未按地区分摊，地区 ROI 请求澄清。
- API 与 MCP 根据检索到的指标要求 `refund:read` 或 `marketing:read`；写作用例确保模型调用前就拒绝无权限请求。

## 合成数据与冻结集

`data/multidomain_seed.py` 生成 `insightpilot-multidomain-v1`，底层订单 seed 固定 `20261020`，营销扩展 seed 固定 `20261023`，快照为 `2027-03-01T00:00:00Z`。数据包含重复投递、后续修订、void、迟到入库、部分退款撤销、UTC 月末边界和未归因组。默认 50,000 单。未使用任何真实企业数据。

开发集 3 题，测试集 4 题，其中 2 题需 ROI 数值，2 题确定性澄清。题目与 gold 对历史 eval 做精确文本重叠检查，结果为 0。两份独立 Python 账本聚合与编译 SQL 执行逐组交叉核对。冻结 manifest 指纹：`7205a30cb9a6559654b70d8cb93491120f1b572c5d06af4e90f940e68d864580`；题集/gold SHA-256 见 `evals/marketing-roi-manifest-v1.json`。

运行：`python -m data.multidomain_seed` 生成本机数据；`python -m data.build_marketing_roi_holdout` 重建并验证冻结文件。当前只完成离线冻结，不包含真实模型评测。小题集用于契约回归，不估计模型稳定性或业务总体准确率。

## 验收与边界

定向测试覆盖多表 JOIN、重复/修订费用、撤销对净收入影响、UTC 右开边界、NULL、seed 重建、gold 哈希、目录检索、API/MCP scope 和地区澄清。全套 unittest：84/84 通过；`git diff --check` 通过。

IP-1 的 PostgreSQL 适配仍未集成到 `InsightPilot`。新合成表还没有 `tenant_id`，所以 PostgreSQL RLS、跨租户 JOIN、双后端数值一致性和真实数据库权限均未验收。IP-2 的 SQLite 结果不能替代该集成验收。
