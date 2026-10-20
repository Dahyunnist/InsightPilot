# 简历要点（合成原型范围）

- 构建只读退款分析 Agent：结构化指标规划、参数化 SQL、SQLGlot AST、SQLite authorizer/只读连接，以及独立结果 verifier；提供 FastAPI 与 MCP Streamable HTTP。
- 构建 50,000 单确定性合成数据集和冻结 v4 评测：30 个新案例、Python 源行 gold、同题规则基线；真实模型一轮严格通过 26/30，其中 16 道数值题通过 12/16，13 个 guard 与 10 道预期澄清、4 道拒绝全部通过。
- 根据 v4 暴露的地区切片和指标检索缺陷修复实现；四道已公开失败题定向复核 4/4。该 smoke 结果不并入 30 题主分数。
- 增加 Bearer scope、地区授权、脱敏 run 审计、并发控制、取消及重启恢复；64 项自动化测试通过。

不要把 guard 结果算成模型回答；不要把小规模合成题描述为生产或泛化验证。旧 S4 的 16/16 是历史固定集总通过，不是 16 次模型回答。完整来源和限制见 [当前基准](../reports/current-benchmark/evaluation.md)。
