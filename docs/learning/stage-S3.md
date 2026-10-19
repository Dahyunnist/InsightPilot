# S3 学习记录：API 与 MCP 共用受控服务

## 本阶段问题与业务影响

CLI 适合本地演示，却不能直接供服务端客户端或 MCP host 调用。S3 增加 FastAPI 接口和标准 MCP 工具/资源；所有入口都调用同一个 `InsightPilot.analyze()`，因此不会另开任意 SQL 通道。

## 数据与状态流

HTTP `POST /analysis` 或 MCP `analyze_refund(question)` → 共用 `InsightPilot` → 指标检索/计划/SQL AST/只读查询/独立复核 → 相同结构化结果返回。API 把最近 100 个 run 暂存在进程内，`GET /runs/{id}` 取回运行结果。MCP `metrics://refund-catalog` 提供指标定义，`docs://product-contract` 提供业务契约；它们是读取资源，不执行查询。

关键文件：

- `insightpilot/api.py`：`POST /analysis`、`GET /runs/{id}`、`GET /health`、结构化输入错误。
- `insightpilot/api_server.py`：loopback API 启动器。
- `insightpilot/mcp_server.py`：`analyze_refund` 工具、指标与契约资源、Streamable HTTP 入口。
- `tests/test_s3_interfaces.py`：FastAPI TestClient、MCP SDK in-process client 和 HTTP transport 联调。
- `requirements.txt`：锁定 FastAPI、Uvicorn、HTTPX、MCP SDK 与 SQLGlot。

## 设计取舍

- API 监听 `127.0.0.1:8000`，MCP 监听 `127.0.0.1:8001/mcp`。分开端口减少部署耦合；两边仍复用同一个服务对象和查询策略。
- MCP 只暴露一个分析工具，不提供 SQL 参数或连接对象。资源负责发布口径和产品契约；工具负责执行受策略保护的分析。
- API 的 run store 是进程内、最多 100 条。原型重启后历史消失；S3 不引入数据库或外部日志系统。
- 入口错误带 `request_id`、状态和阶段；写请求/个人信息仍由核心状态机拒绝。
- 使用 MCP Python SDK 的 Streamable HTTP，不自造协议消息格式。

## 两条失败路径

1. API 请求缺少问题或问题超过 2,000 字 → FastAPI 请求校验返回 HTTP 422 → 响应包含 `request_id`、`stage=validation`，不会进入模型或 SQL。测试验证响应字段和请求头 ID 相同。
2. MCP host 传入分析工具 → 工具仍只能传 `question` → 共用服务拒绝写操作或越权字段，并使用 SQLGlot 和只读数据库执行正常请求。测试检查工具 schema 没有 SQL 参数，并用标准 SDK Client 联调 Streamable HTTP。

另一个 API 路径：run id 不存在时返回 HTTP 404 和 `run_not_found`；测试覆盖该状态。

## 测试复现与真实结果

```text
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

本次实跑：`Ran 19 tests ... OK`。FastAPI TestClient 验证分析、request ID、run 查询和校验错误；MCP SDK Client 分别通过 in-process server 与 Streamable HTTP 成功列工具、调用分析工具、读取指标和契约资源。MCP 工具结果包含相同数据版本和独立复核状态。

本机启动：

```text
.\.venv\Scripts\python.exe -m insightpilot.api_server
.\.venv\Scripts\python.exe -m insightpilot.mcp_server
```

需在两个终端分别运行。API 文档位于 `http://127.0.0.1:8000/docs`，MCP 地址为 `http://127.0.0.1:8001/mcp`。

## 亲手实验（约 15 分钟）

1. 在两个终端启动 API 和 MCP（2 分钟）。
2. 打开 API `/docs`，提交带明确日期的退款金额率问题，检查 `run_id`、SQL 和 `verification`（4 分钟）。
3. 用 `GET /runs/{run_id}` 取回同一结果，再请求随机 run id，解释 404（3 分钟）。
4. 用 MCP Inspector 或 SDK Client 列出工具与资源，读取 `metrics://refund-catalog`（4 分钟）。
5. 从 MCP 调用 `analyze_refund`，确认输出无 SQL 输入字段，且有同一数据版本（2 分钟）。

检查点：能描述 API 与 MCP 的入口差异，并指出两者共同经过的策略层。

## 面试追问与回答

**问：工具和资源如何区分？**
答：工具由调用方请求执行分析；资源是按 URI 读取的口径或契约内容。资源不运行 SQL。

**问：为什么 API 和 MCP 没有各写一套业务逻辑？**
答：分叉会令一个入口遗漏权限、指标口径或复核。两者都注入同一个 `InsightPilot` 对象，执行路径只有一条。

**问：API 的 GET run 是否是持久审计？**
答：不是。它只查询进程内最近 100 条运行记录；进程重启就清空。持久审计仍需有授权的数据存储与脱敏设计。

## 风险与停止点

API 和 MCP 当前无身份认证，只绑定 loopback；不得直接暴露到公网或共享网络。MCP 依赖版本锁定为 `2.0.0`，需随官方 SDK 更新策略重新验证。run store 不是持久审计。S3 已完成；按最新授权继续 S4。
