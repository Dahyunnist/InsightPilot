# IP-3 本地追踪与阶段 Span

## 数据流

`InsightPilot.analyze` 在运行开始和结束时尝试写固定字段 JSONL 事件。设置 `INSIGHTPILOT_TRACE_JSONL` 才启用；目录不存在时会按需创建。OpenTelemetry 可用时，Agent 另建 planning、SQL compile、query、verification 子 Span，并继承 API/Agent 父 Span。

## 隐私边界

JSONL 只写时间、事件名、run/trace/span ID、阶段、状态和枚举化失败类别。不写问题、地区、租户、SQL、参数或结果。失败类别采用白名单，其余归为 `other`。日志路径不可写时返回失败状态，不中断分析请求。

## 失败路径

1. 没有设置 JSONL 路径：本地事件日志关闭；OTel 仍由已有可选 endpoint 控制。
2. 文件系统拒绝写入：不抛出异常，分析继续；此时对应 JSONL 事件会缺失。
3. 指标规划、SQL 编译、数据库查询或独立复核失败：阶段 Span 仍结束；外层结果保留现有错误分类。

## 复现

```powershell
Set-Location E:\job\insightpilot
.\.venv\Scripts\python.exe -m pytest -q tests/test_ip3_tracing.py
```

当前定向测试覆盖 opt-in JSONL、字段白名单、写入失败不阻断请求、阶段父子 Span 和敏感数据不导出。未连接真实 OTel Collector；没有实现或伪造 retry、cancel 事件。

## 亲手实验

在临时目录设置 `INSIGHTPILOT_TRACE_JSONL` 并调用本地合成问题，检查 JSONL。再把路径设为已有普通文件下的子路径，确认分析仍返回、但 sink 报告未写入。删除环境变量后重跑，确认日志文件不增长。

## 面试要点

- 为什么 JSONL 事件采用固定 Schema，而不序列化完整请求对象？
- OTel exporter 不可用时，哪些路径继续工作？JSONL 写失败又如何处理？
- 如何验证阶段 Span 的父子关系与跨进程 trace context？

## 验证范围

Windows 项目环境下 `tests/test_ip3_tracing.py`：7 passed。测试使用内存 tracer 和合成 SQLite 数据；不能代表生产采集器、PostgreSQL 负载或线上可观测性 SLO。
