# IP-4 v13 历史锁执行门槛（2026-11-15）

## 结论

本轮未发起模型请求，未创建隔离 worktree。历史提交搜索没有找到同时匹配 v13 r5 预注册锁全部 21 项非账本工件哈希的提交，因此不得运行冻结评测。

项目架构属于 Plan-and-Solve 风格的结构化 Agent：规划器输出受约束的查询计划，系统编译 SQL、只读执行，再由独立 verifier 核验。模型不生成或执行 SQL；这不是经典 ReAct 的“思考—行动—观察”循环。说明见 `docs/final-delivery-one-pager.md`。

## 核验范围

- 锁文件：`reports/ip4-v13-model-preregistration-20261114-r5.json`，冻结提交 `f314a0e681672472e8beb743fecc18b71c36c229`。
- 比对 21 项非账本工件：17 项源代码/依赖哈希，以及 blind、gold、manifest、gold seal 四项冻结数据工件。
- 对规划器、Agent 和查询引擎等变更文件做历史哈希交集，得到 7 个候选提交：`16a46f2`、`36697b2`、`46941c2`、`af34dfe`、`3e7af12`、`dbdabbf`、`8cf1b1e`。
- 7 个候选均有同样 3 项不匹配：`rule_baseline_sha256`、`manifest_sha256`、`gold_seal_sha256`。因此没有候选满足完整锁。
- blind 与 gold 哈希在这 7 个候选中匹配；这不抵消 manifest 与 seal 的差异，也不能据此放行。

## 安全与费用边界

- 没有调用模型 API，没有消耗本轮评测预算。
- 没有读取或复制 `.env`，没有输出凭据。
- 没有读取或改动 `.pytest-temp-ip4-full/`、`.pytest-temp-ip4-host/`、`.tmp/`。
- 没有更改 blind、gold、SQL、结果文件，也没有使用 v14 替代 v13。

## 后续放行条件

只有找到同时匹配锁内全部工件的历史提交，并在隔离 worktree 完成原锁 dry preflight、payload allowlist、预算与最新余额检查后，才可执行已授权的 v13 评测。不得通过重封锁或重生成题集绕过该门槛。
