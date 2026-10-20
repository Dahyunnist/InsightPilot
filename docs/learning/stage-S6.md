# S6 学习记录：持久审计、授权与取消

## 阶段目标

让 API run 在重启后可追溯；收紧 HTTP 访问和地区范围；给 SQL 执行提供协作式取消。MCP/API 继续调用同一个核心服务。

## 原理与数据流

请求先过 Bearer/scope 校验与并发闸门。API 创建 UUID run，SQLite store 先写 `running`；核心分析运行时持有同一 run ID 和 `threading.Event`。分析完成后保存结构化结果。`GET /runs/{id}` 从 SQLite 读取，不依赖进程内缓存；取消接口标记持久状态并设进程内 event，SQL progress handler 在下一检查点中止。进程启动时遗留的 running 记录转成 interrupted。

记录保存 SHA-256 问题指纹、常见手机号/邮箱/大陆身份证脱敏文本和执行结果。执行结果含指标/定义版本、SQL、参数、数据版本、run ID 和独立复核证据，可在相同数据版本重放查询。原始自然语言不保存；SHA-256 不是匿名化或加密。

配置：

- `INSIGHTPILOT_API_TOKEN`：API 和 MCP HTTP 共用 bearer token。
- `INSIGHTPILOT_API_SCOPES=refund:read,refund:cancel`：逗号分隔 scopes。
- `INSIGHTPILOT_API_REGION=华东`：固定行范围；模型不能扩大或改写它。
- `INSIGHTPILOT_RUN_RETENTION_DAYS=90`：run 保留天数。
- `LLM_TIMEOUT_SECONDS=30`：模型请求超时；运行取消会在模型调用返回后生效。

关键文件：

- `insightpilot/run_store.py`：WAL SQLite 运行记录、脱敏、保留和恢复状态。
- `insightpilot/api.py`：Bearer scope、持久 run、并发上限、取消端点和结构化错误。
- `insightpilot/auth.py`：API/MCP 共用 HTTP bearer gate。
- `insightpilot/runner.py`：数据库执行前及 progress callback 响应取消信号。
- `insightpilot/mcp_server.py`：MCP `structuredContent`、`isError` 与共用 bearer/region。
- `tests/test_s6_runtime.py`：权限、持久化、重启、地区约束和取消边界。

## 取舍与失败路径

- Run store 用 SQLite，不加 Redis/任务队列。单机/单实例足够复现；多副本生产部署需共享的事务型状态库和分布式取消机制。
- 默认最大并发 8，超出返回 429。SQL 有 2 秒/250,000 VM steps 限制；模型网络等待仍由每请求超时控制。
- Bearer token 是单一静态凭证，不是完整 OAuth、轮转或每用户身份系统。无 token 时仅作为本机开发模式，启动器监听 loopback。
- 常见标识符会脱敏，但规则无法覆盖任意自由文本 PII。原始自然语言不写入数据库；部署仍需限制数据库文件访问权限。

失败路径：

1. API token 缺失/错误或 scope 不足 → 401/403，返回结构化 reason 与 `X-Request-ID`，不会调用分析核心。
2. 进程在模型/查询运行中退出 → SQLite 留下 running；下次启动标 interrupted。不会把半成品当 completed。
3. 取消在 SQL 运行时到达 → progress handler 中断查询并返回 cancelled；取消在模型调用时到达则等待模型请求超时/返回后检查。

## 复现与测试

```text
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

S6 覆盖 store 重开和脱敏、启动恢复、API Bearer/scopes、地区收窄、取消记录、pre-cancel SQL gate、MCP HTTP bearer 和结构化错误。当前全套 29 项测试通过。

本机配置 `.env` 时加入自己的 `INSIGHTPILOT_API_TOKEN`；不要把 token 写入仓库。启动 API 后，未带 bearer 请求分析应收 401；带 token 可创建 run，然后重启进程再读 run ID，记录仍在。

## 亲手实验（约 15 分钟）

1. 配一个随机 bearer token 和 `refund:read`，观察无 token 的 API 请求返回 401（3 分钟）。
2. 增加 `INSIGHTPILOT_API_REGION=华东`，问华南问题，确认返回 `region_scope_denied`（3 分钟）。
3. 成功调用后重启服务，用原 run ID 取回结果和复核证据（4 分钟）。
4. 在分析运行中调用 cancel 接口，检查状态最终为 cancelled；慢模型阶段的取消要等到请求超时检查点（3 分钟）。
5. 查询 SQLite 中 redacted prompt，确认没有原始问题；核对保留期限设置（2 分钟）。

## 面试追问

**问：为什么不用内存缓存保存 run？**

答：内存缓存重启即丢。SQLite 持久保存 plan/SQL/params、数据版本和复核证据；生产多实例要换共享持久层。

**问：取消能否杀死模型请求？**

答：不能强杀 stdlib urllib 的阻塞调用。取消事件立即阻止后续 SQL，正在等待模型时最多等待配置 timeout；需可取消 async HTTP client 才能更快中断网络等待。

**问：静态 token 是企业认证吗？**

答：不是。它证明调用方持有配置凭证，并提供粗粒度 scopes 与固定地区范围；生产需 OAuth/OIDC、用户审计和密钥轮转。

## 停止点

S6 完成。单实例持久化和静态 bearer 已可复现；分布式 worker、OAuth 与运行中模型请求强取消留待真实部署要求出现后再做。
