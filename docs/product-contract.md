# InsightPilot 产品契约（S0）

版本：`insightpilot-s0-v1`。范围：电商退款经营分析的离线、合成数据基准。本文与 `evals/cases.jsonl` 共同冻结口径；评测答案由固定数据人工核算，不由模型生成。

## 场景与用户承诺

运营经理要查指定时间段、地区的退款表现，比较品类/渠道贡献。系统应先说明采用的指标定义，再给数字、分子分母、时间范围和可复核 SQL。时间或指标定义不完整时先问清楚。S0 只定义业务和验证基准，不实现 Agent、模型、SQL 执行服务或生产权限系统。

## 数据模型

| 表 | 粒度与主键 | 字段 | 业务约束 |
|---|---|---|---|
| `orders` | 一行一个订单；`order_id` | `customer_id`, `product_id`, `region`, `channel`, `ordered_at`, `status`, `paid_amount_cents` | 状态为 `paid` / `cancelled`；时间为 UTC ISO-8601；金额为非负整数分 |
| `refunds` | 一行一次退款申请/退款部分；`refund_id` | `order_id`, `refunded_at`, `amount_cents`, `status` | 一单可多行；状态为 `approved` / `pending` / `rejected`；金额为非负整数分 |
| `products` | 一行一个商品；`product_id` | `category` | 订单关联商品以确定品类 |
| `data_meta` | 一行一个元数据键 | `key`, `value` | 固定数据版本及时间基准 |

固定夹具包含 30 单、32 条退款记录、3 个品类。刻意包含同单分笔退款、待处理/驳回退款、取消单、零金额订单、退款/下单日期跨日，以及周界时间。数据为确定性手写夹具；无随机生成步骤，重建结果逐行稳定。`data/seed.py` 通过标准库 SQLite 重建数据库：`python data/seed.py`。

## 指标字典与口径

所有区间均为 UTC 左闭右开 `[start, end)`；“上周”必须先绑定明确日期和时区。地区和维度来自订单。除特别说明外，取消订单不进入分母。

| 指标 ID | 公式/时间归属 | 退款状态与边界 |
|---|---|---|
| `approved_refund_order_rate` | 有至少一笔批准退款的去重已支付订单数 / 已支付订单数；按下单时间归属 | 仅 `approved`；同单多条退款不能重复计订单 |
| `approved_refund_amount_rate` | 批准退款金额 / 对应已支付订单金额；按下单时间归属 | 只累计批准部分退款；订单金额先按订单聚合，防止退款连接放大分母 |
| `refund_event_rate` | 需业务方指定分子定义及订单/退款事件时间 | 未定义前不得自行选择 |
| `approved_refund_amount` | 批准退款金额之和 | 按查询明示的订单 cohort 或退款事件时间过滤 |
| `approved_refund_amount_contribution` | 分组批准退款金额 / 同切片全部批准退款金额 | 分母为零时返回 null/不可计算 |
| `approved_refund_campaign_attribution` | campaign touch 归因权重 × 订单批准退款金额；缺少 touch 的退款放入“未归因” | 仅 enterprise v1 扩展数据集；campaign 多触点权重和为 1 |

语义目录版本为 `insightpilot-semantic-catalog-v2`。每个已支持指标同时发布 `definition_version`、分子/分母、时区、freshness、grain、源表、字段 lineage、合法维度、访问 scope、契约来源和示例问法。规划只注入候选指标定义。MCP 目录资源与 CLI/API 使用相同对象。`refund_event_rate` 仍禁用，直到产品负责人冻结公式、时间归属、分母与生效版本。

扩展基准 `insightpilot-enterprise-v1` 由 `data/enterprise_seed.py` 确定性生成。含 50,000 单、退款多行、多触点 campaign、区域/渠道/品类、synthetic customer PII decoy、recorded_at 迟到退款。该库不是 S0 的替代，统计结果只用于压力/语义回归。campaign 归因的未归因订单有显式 bucket；单条订单多 touch 的 weights 应和为 1。质量报告记录归因缺失率和 >1 天入库延迟；没有做历史 as-of point-in-time 查询。

第二业务域 `marketing_net_roi` 需要独立合成版本 `insightpilot-multidomain-v1`。定义为 `(触点权重分摊订单净收入 - 去重活动费用) / 去重活动费用`。订单时间和 spend 时间均按 UTC 左闭右开筛选；批准退款扣除快照前已记录的撤销。Spend 按 `business_event_id` 取 `revision`、`recorded_at`、`event_id` 排序后的最新事件；`void` 修订排除。缺 attribution 汇入“未归因”；没有 spend 的组 ROI 返回 null。费用没有地区分摊，所以 ROI 地区筛选必须澄清。

金额用整数分计算，展示层才转元。比例用未舍入值比较，黄金集允许误差见每题 `tolerance`；零分母返回 `null`，不得写作 0%。`N01` 的“订单退款率”、`N02` 的“金额退款率”说明两种含义；`A01`、`A02` 覆盖口径/范围缺失。至少存在三种可区分口径：退款订单率、退款金额率、退款事件率。

## 固定评测集

`evals/cases.jsonl` 含 12 个正常问题和 4 个拒绝/澄清问题。每个正常案例含区间、过滤、分组、预期分子/分母或结果、误差上限和 SQL 语义；拒绝/歧义案例规定不可执行状态与原因。语义正确性不以 SQL 字符串相同判断。

| 案例 | 覆盖点 |
|---|---|
| N01–N02 | 两种退款率口径、分子分母 |
| N03–N06 | 品类金额、品类退款率/贡献、渠道比较 |
| N07 | 退款事件时间与 UTC 日粒度 |
| N08 | 周期对比与左闭右开边界 |
| N09–N10 | 去重订单、退款状态分层 |
| N11 | 零分母返回 null |
| N12 | 订单 cohort 与退款时间区分、多笔退款去重 |
| A01–A02 | 口径或时间范围不明时澄清 |
| R01–R02 | 写请求、敏感个人信息请求拒绝 |

## 错误 SQL 的业务危害

- `JOIN orders × refunds` 后直接 `SUM(paid_amount_cents)`：一单多笔退款会重复放大分母，低估退款金额率。
- 将退款事件时间误当订单 cohort 时间：跨周退款归错周，改变趋势与归因。
- `COUNT(*)` 代替去重订单数：同单分笔会虚增退款订单率。
- 纳入 pending/rejected：把未发生或失败的退款算作损失，错误触发品类整改或预算调整。
- 零分母强行返回 0：把“无收入可比”误报成“退款表现为零”。
- 忽略 UTC/区间边界：跨日或跨周订单重复/遗漏，造成运营团队误判升降。

## 权限与产品边界

项目目标为只读分析原型。即使后续允许生成 SQL，也必须在应用层校验单条只读查询，并使用数据库只读连接；AST 策略与数据库权限是两道边界。不得访问个人姓名、手机号、地址；S0 数据不含这些字段。SQLite 是单机基准，不能据此声称生产隔离或并发能力。
