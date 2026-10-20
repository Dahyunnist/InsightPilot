# Enterprise 合成数据契约

版本：`insightpilot-enterprise-v1`。固定 seed：`20261020`。默认规模：50,000 orders。数据 fingerprint 位于 `data_meta`，用于识别重建内容。金额为人民币分，时间为 UTC ISO-8601。

| 表 | 粒度与主键 | 关键字段 | 用途/约束 |
|---|---|---|---|
| `orders` | 一行一个订单；`order_id` | `customer_id`, `product_id`, `region`, `channel`, `ordered_at`, `status`, `paid_amount_cents` | cohort、地区/渠道/品类/月分组；取消单排除 |
| `refunds` | 一行一笔申请/部分退款；`refund_id` | `order_id`, `refunded_at`, `recorded_at`, `amount_cents`, `status` | event time 与系统入库时间分开；状态受 CHECK 限制 |
| `products` | 一行一个商品；`product_id` | `category` | 品类维度 |
| `customers` | 一行一个合成客户；`customer_id` | `segment`, `email`, `phone` | 仅用于隐私边界测试；整张表不在 SQL allowlist |
| `campaigns` | 一行一个活动；`campaign_id` | `campaign_name` | 多触点归因名称 |
| `campaign_attributions` | 一行一个订单-活动 touch；复合键 | `order_id`, `campaign_id`, `weight` | 权重在 (0,1]；完整归因订单权重和为 1 |
| `data_meta` | 一行一个元数据键 | `data_version`, `timezone`, `seed`, `snapshot_at` | 数据复现和口径标识 |

默认实跑计数与质量项见 [enterprise 质量报告](../reports/enterprise-data-quality.json)。498 单无 campaign attribution，金额进入“未归因”；9,757 笔退款比发生时刻迟超过一天入库。当前查询按业务 event/cohort 时间，不支持重建某历史 `recorded_at` 快照。

数据库含 `customers.email` 和 `customers.phone` 作为纯合成 decoy。SQL allowlist 拒绝 `customers` 表及 PII 列；拒绝请求仍由 preflight 在模型调用前拦截。此 fixture 不含真实个人数据。
