# API 与 MCP 的问题级授权一致性

## 为什么要验证

InsightPilot 的 FastAPI 与 MCP 都能调用同一分析核心，但请求入口不同。营销问题需要 `marketing:read`；若只有 `refund:read`，两个入口都必须在规划器和数据库执行前拒绝。

## 测试做了什么

测试通过真实 `MCPServer.streamable_http_app` 和 MCP Python SDK `ClientSession` 完成初始化及工具调用；API 使用实际 `/analysis` 路由。两边提交同一营销分析问题，并比较 HTTP 状态、拒绝原因和所需 scope。测试还断言规划器未被调用，拒绝体没有 SQL 字段。

该测试只证明合成数据库与本地 bearer 配置下的授权路径一致，不证明真实身份提供方、生产网络或部署环境已通过安全审计。

## 复现

```powershell
python -m pytest tests/test_s3_mcp_api_parity.py -q
```

## 失败边界

- scope 不足：API 与 MCP middleware 都应拒绝，不能触发规划器。
- MCP session 初始化/传输失败：测试失败，不得退回到手写 Starlette 假端点并宣称完成 MCP 联调。
