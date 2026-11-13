# IP2 指标版本绑定

`QueryPlan.metric_version` 可显式指定口径版本。旧调用省略版本时映射目录当前版本；`get_metric` 不会对未知版本回退。评估器解析失败返回 `unknown_metric_version`。`METRIC_VERSIONS` 按 `(metric_id, definition_version)` 登记，重复键拒绝覆盖。

成功回答的 `plan.metric_version` 与 `metric.definition_version` 一致，记录实际采用口径。该字段不代表历史版本 SQL 可执行；未登记 SQL 的历史版本仍 fail closed。

回归覆盖当前/缺省选择、未知版本拒绝和合成历史版本重放，确保新版本不覆盖历史定义。历史版本仍需通过注册函数追加；当前尚无持久化迁移表。PostgreSQL/RLS 实测不在本次范围。
