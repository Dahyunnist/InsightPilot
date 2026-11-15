# IP-1 至 IP-4 运行证据统一审计

## 本阶段问题

共享 manifest 文档定义了字段，但此前没有真实地把 IP-1/2/3/4 报告投影到同一结构。缺失值容易被误读为零或被后续流程猜补；派生 trace ID 也可能被误当成 OpenTelemetry trace。

## 数据流

已提交的阶段报告 → `project_record_evidence()` 只读投影 → 最低字段覆盖统计与请求/响应/费用状态 → `reports/ip1-ip4-run-evidence-audit.json`。

投影器不连接网络、不调用模型、不重写源报告。每个最低字段总是出现；证据缺失时保留 `null`。费用另有 `cost_status`，请求尝试、供应商响应和送达状态分开记录。

## 关键实现

- `insightpilot/run_evidence.py`：五种来源记录的纯离线投影及覆盖统计。
- `scripts/audit_ip1_ip4_run_evidence.py`：从仓库报告重建审计 JSON。
- `scripts/verify_ip3_otel_collector.py`：今后保存 API 返回的 `run_id`；历史报告仍保持原样。
- `tests/test_run_evidence.py`：最低字段、真实 ID、费用确定性与冻结源不变测试。

IP-1/IP-2/IP-3 宿主验收是一条合并运行记录，不能拆成三个虚构 run。IP-2 边界清单是开发集 manifest，不是运行记录。IP-3 历史 OTel 报告有真实 trace 与 API/Agent span，但没保留 API `run_id`。IP-4 RulePlanner 的旧 `trace_id` 是源码按提交和时间派生的值；投影将实际 `trace_id` 置空，并单列旧值。R5 记录 15 次供应商请求尝试、0 个响应；送达和实际费用未知，费用保持 `null`，峰值价格估算单独保留。

## 验证

运行：

```text
.venv/Scripts/python.exe -m unittest tests.test_run_evidence -v
.venv/Scripts/python.exe scripts/audit_ip1_ip4_run_evidence.py
```

定向测试 `3/3` 通过。报告有 5 条记录；五项最低字段全部覆盖，运行环境摘要、模型 token 上限和 prompt/tool schema 哈希覆盖不足。只有历史 IP-3 OTel 记录含真实 trace；R5 费用未确认。冻结题、gold 和 manifest 哈希前后不变。

## 失败路径

1. 来源文件缺失或 JSON 损坏：离线读取直接报错，不生成半份审计。
2. 数据源把未知费用写作 0：测试会因 R5 的预期 `null` 与 `unconfirmed` 不符而失败；峰值估算不能替代实际费用。

## 手动实验

1. 打开审计 JSON，比较 `provider_requests_attempted`、`provider_responses_received` 与 `delivery_status`。
2. 检查 IP-4 RulePlanner 的 `trace_id` 和 `legacy_source_derived_trace_id`，确认旧派生 ID 未冒充真实 trace。
3. 重跑审计脚本，比较 `minimum_field_coverage`；审计只读取来源并重写派生产物。

## 面试追问

**问：为什么缺失值不用 `unknown` 字符串？**

答：`null` 保持机器可读；缺失状态和费用状态单独描述，避免把缺证据误算成零。

**问：有 trace_id 就代表有完整链路吗？**

答：不代表。这里只把 Collector 收到的 trace/span 视为真实遥测；源码派生 ID 单独标注，历史 API run_id 缺失也不补造。

**问：R5 有 0 个响应，为什么费用不是 0？**

答：请求可能已送达供应商，但响应丢失。没有账单回执就无法确认实际费用；峰值单价估算不是实际费用。

## 边界

最低字段覆盖仍不完整，尤其 prompt/tool schema 哈希、IP-3 运行 ID 与若干数据/运行时哈希。补采只应来自后续真实 runner 记录；历史报告不做猜测性回填。冻结评测数据保持不变。

## 后续 IP-3 记录与历史费用旁证

后续 OTel runner 会从容器内实际生成的 SQLite fixture 读取 SHA-256，从本地 image inspection 和容器 Python/platform 获取运行信息，并保存 API 返回的 `run_id` 与 Collector 的 API root `trace_id`/`span_id`。runner/probe/seed/compose/依赖和 API/planner 源文件各自记录哈希。该固定 planner 无 prompt、tool schema 或 scorer；对应字段仍是 `null`，并标为 `not_applicable`。若来源不可读，字段保留 `null` 并写明原因。旧的 2026-11-11 报告不会被回写。

用户提供的历史账单仅作人民币用量旁证：InsightPilot 别名历史 538 次、¥1.84931692；全量导出 4,402 次、¥10.91277486，金额合计一致。另一个别名 1,471 次无法归属。它们不分摊到具体 run、不与美元预算相加，也不表示当前余额。原始 CSV 与身份/密钥字段未复制到仓库。

本次离线增量验证 `8/8` 通过，未运行 22 题评测。新 OTel 报告使用带时间戳的独立文件名，投影器会发现并纳入这些新记录；原有 `ip3-otel-collector.json` 保持不变。
