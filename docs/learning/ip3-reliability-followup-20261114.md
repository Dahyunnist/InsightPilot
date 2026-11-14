# IP-1 至 IP-4 状态与 IP-3 可靠性补充（2026-11-14）

## 阶段状态

- IP-1：代码和测试覆盖 PostgreSQL tenant context、只读策略、连接池恢复与超时；宿主 RLS 验收记录为通过，见 `reports/ip1-rls-attempt-20261113.json` 与 `docs/learning/stage-IP1.md`。
- IP-2：目录 v3、退款与营销域、库存离线切片及目录审计已提交；样本为合成开发集，不代表模型或生产总体表现。
- IP-3：API/MCP、持久 run store、幂等、取消、限流、超时和 Compose/PostgreSQL 有既有测试。此补充把幂等重放从新工作限流计数中排除；仅在本机 TestClient 离线回归通过。该结果不替代当前宿主 PostgreSQL/Compose 重跑。
- IP-4：r5 transport-blocked；15 次 planning 请求均为 `URLError`，无响应；可答题 0/14 产出。冻结题仅为 development-only。禁止重发这些题。

## IP-3 幂等重放

认证与幂等键验证后，服务先检查是否已有相同主体、键和问题的运行，再对新工作执行速率限制。这样同一工作重放不会消耗新工作配额；冲突仍返回 409。新的分析仍计入每分钟限额。

复现：`.venv\Scripts\python.exe -m unittest tests.test_s6_runtime`。在 Windows 受限执行环境须使用可启动本机 TestClient socket 的宿主测试环境。本轮 14 项通过，包括每分钟 1 次时首请求与幂等重放均成功、下一条新请求返回 429。

## run_manifest 历史缺口

共享 public manifest schema 已声明运行身份、数据/代码哈希、预算、状态、请求数、模型输入标志和事件覆盖；本轮未发现与 IP-3 修复直接相关的必需字段缺失。

独立发现旧 r3 blocked-attempt normalizer 依赖 cost ledger 中 r2 预留 SHA。r5 执行后 active reservation 已更新，历史 reservation 中 SHA 为 `9c88623226a6b8bc12f15e8113c9a2535e27d4b0839434b14e4eb32ce6c56e6d`，而 r3 attempt 引用 `4f4a4e405fe087b3fbdd5f3ac1897b9cab986b572fe23`。题目、gold、r2 lock 文件自身哈希仍匹配。为保护历史预留证据，本轮撤销 normalizer 的放宽尝试；遗留视图因此无法从已更新账本重建。需另立迁移方案，不得伪造或改写旧记录。

完整测试本轮未完成；受限 Windows TestClient 初始化会卡住，部分定向组合中 run_manifest 两项因上述历史 SHA 不匹配失败。未运行 Docker/PostgreSQL，也未调用模型。`.tmp/` 保持未跟踪且未提交。
