# IP4 v13 冻结锁离线审计（2026-11-12）

独立核验：冻结 seal 涉及的 7 个文件均匹配；题集、gold、seal、manifest、runner、scorer 与 prompt 锁未变。当前预注册锁不可用于运行：planner、agent、metrics 和成本账本 4 项哈希已过期；其余 18 项匹配。成本账本锁过期意味着此前余额与预算关系不能作为当前授权依据。

状态保持 `blocked_by_review`。供应商是否收到未确认，请求实际费用未确认，分别记作 `provider_received: null` 与 `cost_usd: null`。没有运行余额查询或模型调用。不得手工改锁哈希、使用历史余额、编辑冻结题集/gold，或将评测表述为已完成。

来源为离线文件哈希审计；组合测试曾报 5 passed、1 failed、14 subtests，唯一失败是过期 artifact hashes。MCP parity 单测 1 passed。IP1 PostgreSQL/RLS 宿主验证仍待用户运行 `scripts/run_ip1_rls.ps1`。
