# IP-2 授权地区与指标版本绑定

## 数据流

规划器返回 `QueryPlan`，其中包含 `metric_id` 和 `metric_version`。服务端应用授权地区时会重建计划；重建必须保留指标版本，再由目录检查版本是否存在、当前 SQL 编译器是否支持该版本。

## 设计取舍

授权地区只覆盖地区过滤，不得隐式升级指标定义。历史版本目前没有独立 SQL，因此即使版本存在，也应返回 `metric_version_not_executable`，不能把旧口径静默改成当前公式。

## 失败路径

1. 问题请求地区超出调用方授权地区：返回 `region_scope_denied`。
2. 授权地区重建计划时携带不支持的历史版本：保留版本号并 fail closed，不执行 SQL。

## 复现

```powershell
Set-Location (git rev-parse --show-toplevel)
.\.venv\Scripts\python.exe -m pytest -q tests/test_s6_runtime.py -k authorized_region
```

测试只覆盖合成 SQLite 数据。它不证明 PostgreSQL RLS 或真实租户隔离。

## 亲手实验

把 `HistoricalRefundPlanner` 返回的 `metric_version="0.9.0"` 改成当前目录版本，运行定向测试，观察地区约束保留、结果允许继续；再改回历史版本，确认查询在 SQL 执行前被拒绝。

## 面试要点

- 为什么授权范围不能改变业务指标口径？
- 为什么“目录能查到历史版本”不等于“当前 SQL 可以执行历史版本”？
- 如何把版本、SQL 模板与数据库迁移绑定，避免元数据与执行语义分离？
