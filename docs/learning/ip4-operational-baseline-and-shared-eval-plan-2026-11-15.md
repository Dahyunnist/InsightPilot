# IP-4 运行基线与共享评测计划（2026-11-15）

## 当前结论

- 当前代码可作为本地合成数据演示与离线验证基线；不据此宣称生产部署就绪。
- SQLite 是默认本地评测后端。PostgreSQL 通过独立 Compose 临时服务验证，数据目录使用 tmpfs。生产部署还需独立运维环境、密钥管理、持久化策略、备份恢复和负载/SLO 验收。
- IP-4 v13 冻结题集、gold、manifest 与 r5 锁定哈希一致；执行代码锁不一致。当前 planner 会在模型调用前澄清 M09，旧 RulePlanner 基线却误接受 M09。因此旧版模型授权不能直接用于当前 revision。
- IP-4 v14 是待审候选。现有 SQLite 规则基线与 PostgreSQL 双租户 parity 是离线证据，不是模型评测。没有所有者审题与新授权前，不调用模型。

## 已核验运行基线

| 项目 | 基线 | 证据与边界 |
| --- | --- | --- |
| Python 与依赖 | 仓库 `.venv`；`requirements.txt` 固定 `sqlglot==30.23.0`，其余依赖有版本范围 | 当前专用测试环境可导入依赖；锁文件仍需补齐，部署镜像须固定基础镜像 digest |
| 本地数据 | 固定 seed 的 synthetic SQLite；只读查询引擎 | 不含客户数据；生产接入需独立数据授权和隔离验收 |
| API/MCP | FastAPI 与 MCP Streamable HTTP；运行记录有保留策略、限流/并发、Bearer scopes、取消和恢复 | `docs/quickstart-evaluation.md` 可复现本地启动；当前不等于互联网暴露配置 |
| 查询安全 | 固定 SQL 模板、SQL AST 白名单、只读连接、authorizer/`query_only`、超时、行数与虚拟机指令预算 | 见项目 README 与 SQL 安全测试；部署验收仍应核对目标数据库角色和网络边界 |
| PostgreSQL | PostgreSQL 16 临时容器，tmpfs 数据，双租户 RLS 验收 | v14 parity 报告显示 22 题两个租户 score vector 与 SQLite 一致；报告归档在 `reports/ip4-v14-postgres-parity-summary-2026-11-15.json`。v13 同哈希 PG 证据仍缺 |
| 模型请求 | 仅评测流程可调用；`current_benchmark --mode model` 会发真实请求 | 一般开发/部署 smoke test 使用 RulePlanner 或 mock；不能以启动服务为由触发模型请求 |
| 发布状态 | 本地演示/研究原型 | 未做生产镜像、编排、密钥轮换、备份恢复、容量与 SLO 验收；不得标记 production-ready |

## v13 冻结版授权核对

执行前应核对 `reports/ip4-v13-model-preregistration-20261114-r5.json`、gold seal、blind/gold/manifest 哈希、当前账本和 runner payload。审计得到：

- Frozen blind/gold/manifest 哈希和题目 ID/schema 均匹配；22 题由退款 12、营销 ROI 10 构成，14 答题、5 澄清、3 拒答。
- 执行锁不匹配 `planner.py`、`model_runner`、`agent.py`、`metrics.py`、`query_engine.py`。尤其 M09 路径变化影响有效调用题集与 baseline 对照。
- Runner 的请求 allowlist 是 `question`、`candidate_metrics`、`allowed_metric_ids`；明确排除 gold、SQL、查询结果、数据库行及私人数据。
- R5 锁仅登记单臂 22 次计划调用、最多 44 次请求、预留 `$0.0936672`。旧双臂授权摘要另有最多 88 次、预留 `$0.1873344`，不能静默当作新 revision 或新运行锁。
- 历史 r5 记录 15 次尝试、0 次 provider response；费用回执未知。不能重试旧 run 或把未知费用写成零。
- 账本历史快照显示 `$5` cap 和剩余额度；这不是实时余额证明。余额端点和新预留须在下一次获准运行前重新检查。

因此，本次只做离线审计。恢复 v13 模型评测前，由所有者审阅当前 planner/M09 行为与新 RulePlanner baseline，选定单臂或双臂设计、模型/题集/revision、请求上限与预留；随后针对该精确 revision 新建版本化锁并跑 dry-run。哈希、payload、余额、预留、cap 或授权任一不一致即停止。授权需覆盖该锁定 revision 与请求设计。

## v14 候选门槛

1. 所有者抽查仓库外候选目录中的 blind；确认自然日期、业务口径与拒答题。
2. 确认候选 manifest、blind/gold/SQLite 哈希，以及 SQLite 与两个 PostgreSQL tenant 的逐题 parity 报告。
3. 基于审阅后的不可变候选创建独立模型预登记、预算预留和 dry-run。
4. 取得针对该 v14 revision 的明确模型授权，再执行单轮；gold、SQL、查询结果、数据库行不进入请求。
5. 评分、成本对账和报告审阅完成后才决定是否冻结 v14。v13 与 v14 结果分开报告，不合并题数。

## 共享 manifest、trace 与 eval 计划

现有 `insightpilot.run_manifest` v1 是离线规则运行合同；公开归一视图 v2 已定义 identity、dataset/split/scorer/runtime、预算与费用确定性、模型尝试/响应数、状态、显式缺失字段以及事件覆盖。保留旧格式兼容，不改写历史源记录。

后续所有 IP-4 runs 采用以下顺序：

1. **身份与冻结**：创建唯一 `run_id`、`trace_id`、`evaluation_id`；记录 commit、数据集/manifest/data fingerprint、blind/gold/scorer/prompt/runtime 哈希和 split。gold 哈希只写本地 manifest，不放入 agent payload。
2. **预登记与预算**：记录实际模型与计划模型分离、模型调用尝试数、provider 响应数、input 是否已到 provider、预算 cap/预留、cost certainty。未知实际费用必须为 `null`，并标 `unconfirmed`。
3. **Trace 事件**：统一 run 起止、阶段、重试、超时、预算拦截、取消、安全拦截、checkpoint/resume。记录计数与 covered/not-covered；没有事件不得推断“事件未发生”。现有 IP trace (`analysis.started/completed/failed`) 与公共词汇仍需显式映射，不能伪报覆盖。
4. **评分隔离**：agent 只读 blind question 与允许的静态 metadata；独立 scorer 在运行结束后读 gold。逐题保存脱敏状态、usage、延迟、失败分类和 score；禁止原始 SQL、参数、结果行及敏感文本进入公共 manifest。
5. **发布与复现**：先跑锁哈希、schema/payload、offline baseline 和 no-model tests；需要真实模型时才执行预登记 dry-run 与授权 gate。完成后写不可变结果、manifest 与 trace 覆盖摘要，做费用账本对账，再评审是否冻结。

事件覆盖保留两组字段：公共标准事件词汇与 InsightPilot 原生事件词汇。映射应在 validator 中版本化并由测试覆盖；改动仅追加兼容字段，不回写旧的 v1/v2 证据。

IP-1/2/3/4 已有报告的真实字段覆盖、run/trace/span 关联及费用确定性，见[离线运行证据审计](ip1-ip4-run-evidence-audit-2026-11-15.md)和机器可读[审计结果](../../reports/ip1-ip4-run-evidence-audit.json)。该投影不重写历史报告；旧的派生 trace ID 不计作实际遥测。

## 复现入口

- 本地启动、API/MCP 演示和当前 benchmark：`docs/quickstart-evaluation.md`。
- 共享 manifest v2 合同与跨项目字段：`docs/learning/shared-run-manifest-v2.md`。
- v13 尝试安全和旧锁阻断：`docs/learning/ip4-v13-run-safety-2026-11-15.md`、`reports/ip4-v13-acceptance-audit-2026-11-15.json`。
- v14 离线候选与 PostgreSQL parity：`docs/learning/ip4-v14-independent-candidate-2026-11-15.md`、`reports/ip4-v14-postgres-parity-summary-2026-11-15.json`。

本阶段没有模型调用、没有访问实时余额、没有读取/上传私人数据，也没有修改冻结题集或 gold。
