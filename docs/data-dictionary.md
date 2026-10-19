# S0 数据字典

源文件：`data/seed.py`。生成数据库版本：`insightpilot-s0-v1`。时间基准：UTC。货币单位：人民币分。

| 字段 | 类型 | 定义 | 可空/枚举 |
|---|---|---|---|
| `orders.order_id` | TEXT | 订单唯一标识 | 非空 |
| `orders.customer_id` | TEXT | 合成客户标识，仅用于夹具完整性 | 非空；禁止作为对外分析维度 |
| `orders.product_id` | TEXT | 商品键 | 非空 |
| `orders.region` | TEXT | 下单地区 | 非空 |
| `orders.channel` | TEXT | web/app 渠道 | 非空 |
| `orders.ordered_at` | TEXT | 下单时间，ISO-8601 UTC | 非空 |
| `orders.status` | TEXT | 订单状态 | `paid` / `cancelled` |
| `orders.paid_amount_cents` | INTEGER | 订单实付金额，分 | 非负 |
| `refunds.refund_id` | TEXT | 退款记录唯一标识 | 非空 |
| `refunds.order_id` | TEXT | 所属订单 | 非空，引用订单 |
| `refunds.refunded_at` | TEXT | 退款事件时间，ISO-8601 UTC | 非空 |
| `refunds.amount_cents` | INTEGER | 本次退款金额，分 | 非负 |
| `refunds.status` | TEXT | 退款处理状态 | `approved` / `pending` / `rejected` |
| `products.product_id` | TEXT | 商品唯一键 | 非空 |
| `products.category` | TEXT | 商品品类 | 家电 / 服饰 / 食品 |
| `data_meta.key/value` | TEXT | 固定夹具元数据 | `data_version`, `timezone` |

退款表粒度是一次退款记录，不是订单；订单金额只能在订单粒度聚合。退款表日期用于事件分析；订单日期用于 cohort 指标。
