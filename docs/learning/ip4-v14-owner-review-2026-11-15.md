# IP-4 v14 离线负责人审阅包（2026-11-15）

## 审阅结论

v14 v5 是独立合成候选，不是正式冻结评测集。候选清单、题目、gold、合成 SQLite、生成器、planner 和 scorer 的 SHA-256 已与候选 manifest 及脱敏 PostgreSQL 汇总交叉核对。22 个题目 ID 与 gold 一一对应，退款与营销 ROI 各 11 题；14 道可答题、8 道 guard。两套 PostgreSQL tenant 的逐题评分向量与 SQLite 相同，但规则基线仅 8/22，不能作为答题能力达标证据。模型调用为 0。

**授权范围并非已证明完全相同。** 先前存档的授权 manifest 是 v13，绑定 blind SHA-256 `c7c8ecdd498cdaae44e7744457b54a2ade850c47e0340f5d3acb66c4f33bfa66`，记录为 22 道合成题、每题一次、不重试。v14 使用不同 blind/gold 哈希，退款与营销题数也变为 11/11。两个评测都符合“22 道合成题”和相同的发送字段约束；更宽的最多 88 次请求授权是否涵盖这个新 hash，现有存档无法证明。不得据此直接发送 v14 题目。

## 候选身份与复核哈希

候选目录位于仓库外。候选目录内的 blind 和 gold 是两个独立文件，但同处一个目录；当前证据未显示文件系统 ACL 隔离。请求边界靠 runner/planner 字段筛选，不是 gold 文件的访问控制。blind/gold 未复制进仓库，仓库只保存以下哈希和脱敏摘要。

| 工件 | SHA-256 | 复核 |
|---|---|---|
| manifest | `060cbdf6e17563d3873b934819c79538b0d74d1c88a01bc58ce905136e02835d` | 与候选目录 manifest 一致 |
| blind.jsonl | `aecba9db0a9c977fa7f95378815965218707b53b68b705530673ad3aeed4e080` | 22 条；与 gold ID 集一致 |
| gold.jsonl | `8c01a8cb71f38c4c1885cb8ef59cb4ce7defaa01848d4c9efb09b6cf6e9558c3` | 未发送；与 blind ID 集一致 |
| synthetic.sqlite | `50ae1d417a72c20699ab2c53775b3e530010f7e7f51a2cf66ef6ffcebd041b34` | 与候选 manifest 一致 |
| candidate builder | `2f6f29ad352440ed5fa530b2584be1bbc4176f3da2f92d703fadaa1b1b3b2142` | 与当前生成器一致；seed `20261115`、retail seed `20261116`、50,000 orders |
| planner（含 system prompt 和请求构造） | `78f3d980309ab7048b2c2b1fcfa7a4a4fd11a530f494119b5baa8bb14678aecc` | 与候选 manifest 一致；没有单独 prompt SHA |
| v14 scorer | `e1cb44704c3c0b6a46e8a594490da8030cef2688b8544b3457c97cab05fe9eb8` | 与候选 manifest 一致 |
| v14 PostgreSQL parity 原报告 | `f3777d3407a0f470734b6226c128830e625453e6b60b8a5af88dd390b373812b` | 与外部原件一致 |
| PostgreSQL 容器日志 | `d15e3f6c5b09fae09164df553959d1866d5029fc32c6259dc55c6a1bea22c052` | 只核哈希，未纳入仓库 |

脱敏汇总自身 SHA-256 为 `6b990eec41698c0ca4d2c9ef55cfd38bda85ca6701d4031eb19b186fdb3b5492`。它声明 RulePlanner 8/22（refunds 3/11、marketing ROI 5/11），PostgreSQL 16.15 双租户 parity 通过，逐题 score vectors 全匹配 SQLite。parity 只证明后端一致性。

## 授权与外发字段对照

候选 manifest 的上限为 22 次请求、预留 `$0.0468336`，每请求 `$0.0021288`。此前用户给出的总上限为最多 88 次。22 次落在 88 次数值上限内，但 manifest 没有授权 88 次，也不含重试；若增加重试或调用上限，必须先审阅新预算与运行锁。

当前 `OpenAICompatiblePlanner._complete` 的 user JSON 字段是 `question`、`candidate_metrics`、`allowed_metric_ids`；system prompt 禁止 SQL 输出。题目 ID、gold、SQL、查询结果、数据库行和私人数据不在 planner user JSON 中。静态测试覆盖候选 payload helper；源代码检查确认 planner 当前请求字段相同。该 payload helper 本身未被 v14 模型 runner 调用，因为仓库没有 v14 模型执行 runner。

| 项目 | 先前授权/协议 | v14 v5 | 结论 |
|---|---|---|---|
| 题数与数据 | 22 道冻结合成题 | 22 道新候选合成题 | 数量相同，题集 hash 不同 |
| blind hash | v13 `c7c8…3bfa66` | v14 `aecb…e4e080` | 不同；授权不能自动移用 |
| gold hash | v13 `f109…335df2` | v14 `8c01…e9558c3` | 不同 |
| 发送字段 | question、静态 candidate metrics、allowed metric IDs | 当前 planner 同样只序列化这三项 | 字段一致；需新 v14 runner 锁定后才能端到端证明 |
| 请求上限 | 最多 88 次的较宽授权；v13 存档另记每题一次、不重试 | manifest 22 次，无重试 | v14 当前预算更窄；授权覆盖边界需负责人确认 |
| 外部副作用 | 模型请求须有授权和执行时余额预检 | 候选标记 `owner_review_required_before_external_call=true` | 仍未批准运行 |

执行协议目前**不能端到端复现**：离线候选构建有 seed、builder hash 和测试；PostgreSQL parity 有命令、独立候选目录和源报告 hash；但 v14 没有专用模型 runner、无 runner SHA、无独立 prompt SHA，也没有把授权 hash、payload、调用上限和 scorer 锁在同一运行 manifest 中。不可复用绑定 v13 的 runner/锁来填补这一缺口。

离线回归 `tests/test_ip4_v14_candidate.py tests/test_ip4_v14_report_summary.py`：7/7 通过。覆盖候选构造、22 题结构、静态 payload allowlist、候选哈希及脱敏报告；没有模型调用。

## 负责人需决定

1. 是否审阅并接受 v14 manifest `060cbdf6e17563d3873b934819c79538b0d74d1c88a01bc58ce905136e02835d`、blind/gold 哈希及 22 道题的业务口径。当前 8/22 规则基线和两域失败项需一并接受或要求修订；候选不能因此自动变成正式冻结集。
2. 是否将先前最多 88 次的授权明确扩展到这组 v14 blind hash。若批准，确定实际调用上限、是否允许重试及最高费用；当前候选 manifest 仅预留 22 次、`$0.0468336`。
3. 是否接受将 gold 与 blind 同目录存放。若要求访问隔离，先建立并复核独立 gold/scorer 权限边界。
4. 在任何外发之前，先建立 v14 专用执行 runner 和运行 manifest，锁定 prompt、planner、runner、scorer、blind/gold、允许字段、调用数与预算；获准后再做执行时只读余额预检。

上述决定未作出前，不调用模型、不修改候选或 gold、不创建新冻结锁。

## IP-1 宿主复验旁证

只核对 InsightPilot 自己的 `reports/ip1-database-truth-host-acceptance-2026-11-15.json` 和其三份 `.tmp` 源 JSON。报告及源 SHA-256 全部匹配：PostgreSQL 连接通过；tenant-a、tenant-b 各 12/12；缺失/未知租户 0 行并拒绝缺上下文；DML/DDL 被拒；`data_meta` 100 ms statement timeout 实测 126.34 ms，随后恢复。模型调用 0，仅固定合成数据。该结果闭合本机 `data_meta` 超时改动的宿主复验；不代表生产数据或生产 SLA。

`verify-bad-release` 属于 OpsPilot，不属于 InsightPilot；未作为本次 IP-1 证据或阶段状态使用。
