# S5 学习记录：版本化指标语义目录

## 阶段目标

让“指标”成为有版本、生效时间、粒度、血缘、适用维度与权限标签的语义对象。模型按检索结果获取必要元数据，SQL 执行仍走原模板、AST 白名单、只读连接和独立复核。

## 原理与数据流

业务问题 → 确定性指标候选检索 → 候选 Metric 元数据注入结构化计划请求 → 校验指标 ID 与合法维度 → 固定 SQL 模板 → 多层只读执行 → 独立数值复核。MCP 目录资源调用同一 `catalog_document()`，无需复制第二份指标内容。

Metric 元数据含公式、UTC 时间基础、refund status、定义版本、生效日、grain、源表、字段 lineage、valid dimensions、access scopes、契约来源和示例问题。`refund_event_rate` 仍明确列在排除定义中，因为产品契约未冻结分子/分母。

关键文件：

- `insightpilot/metrics.py`：单一语义目录和确定性候选检索。
- `insightpilot/planner.py`：将检索定义作为模型上下文，并拒绝不适配该指标的维度。
- `insightpilot/agent.py`：结果记录指标版本、粒度、定义来源和 lineage。
- `insightpilot/mcp_server.py`：MCP 资源发布目录文档。
- `tests/test_s5_semantics.py`：目录完整性、模糊指标、维度边界。

## 取舍与失败路径

- 当前目录以 Python 常量为 source of truth，避免 JSON 文件与代码双写漂移。后续治理界面或数据库只有在有多团队编辑需求时再引入。
- `access_scopes` 当前是元数据标签，不能单独构成授权；S6 在 API 边界执行 token scope 和行范围约束。
- 语义元数据由项目维护者编写，不接收用户问题中的任意 SQL/说明作为可信系统指令。SQL 示例来源需引用受控契约，未来检索也只从已审核目录取内容。

失败路径：

1. 模糊“退款率”同时召回订单率和金额率 → planner 澄清，不把候选排名当业务授权。
2. 模型把退款事件日指标搭配品类维度 → 语义契约校验报 planning error，SQL 不会运行。

## 复现与结果

```text
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

S5 新增 3 项测试。目录可 JSON 序列化，定义包含版本/血缘/来源；含混退款率保留两个候选；不兼容维度在计划阶段拦截。项目全部 22 项测试通过。

## 亲手实验（约 12 分钟）

1. 查看 `catalog_document()` 中金额率元数据，追踪 `orders.paid_amount_cents` 与 `refunds.amount_cents`（3 分钟）。
2. 查询 MCP 资源 `metrics://refund-catalog`，确认同一 `catalog_version` 和 metric version（3 分钟）。
3. 把测试计划维度改成 `category` 并选 `approved_refund_event_amount`，观察 SQL 执行前失败（3 分钟）。
4. 阅读 S0 的 `refund_event_rate` 澄清案例，说明为何缺失定义不能由模型填空（3 分钟）。

## 面试追问

**问：版本字段如何避免历史报表换口径？**

答：每次 run 固化 definition version、effective date、contract/data version 和计划；改公式需新版本并显式选择生效边界。当前代码已经记录指标版本，但尚未提供多版本查询回放。

**问：语义检索会不会变成 prompt injection？**

答：只从维护者审阅的目录注入字段化定义；输出再按 metric ID、scope 与合法维度校验。检索文本从不获得 SQL 执行权限。

**问：为什么不直接接向量数据库？**

答：现有 8 个指标适合确定性关键词召回。向量检索增加运维和来源漂移风险，尚无规模证据证明必要。

## 停止点

S5 完成。当前适用对象仍为冻结的 S0 契约；更大的场景基准与多租户行级权限留到后续阶段。
