# IP-4 v14 离线完善记录（2026-11-15）

## 结果

v14 v3 原目录保持不变。生成 v5 到仓库外的临时候选目录。v5 的 blind、gold、SQLite 数据库逐字节匹配 v3；只新增 planner、评分器和 PostgreSQL 身份/一致性证据。v5 仍为 `candidate_pending_owner_review`；PostgreSQL fixture 状态为 `provisioned_and_parity_passed`。

没有修改 v13 或 v14 的题文、gold，没有模型请求，也没有复制 `.env`。

## 修复

- 指标检索增加营销“投资回报/净回报率”与退款“处理状态”语义；退款状态分数高于事件日期提示。v14 14 道可答题现均能检索到候选 manifest 指定指标。
- `NeedsClarification` 支持稳定 reason code。v14 的模糊指标、多指标、缺少时间、未支持地区维度和多套 ROI 定义 guard 均有明确 reason；Agent 将 reason 放入本地结果。
- 新增 `insightpilot/ip4_v14_evaluate.py`。它只用 `RulePlanner`，验证盲题/gold 哈希、题目 ID、数据 fingerprint，并在内存映射 v14 `start/end_exclusive/rows` 到共享评分器字段，不改 gold 文件。
- 新增 `scripts/evaluate_ip4_v14_all_backends.py`。它跑 SQLite 和 PostgreSQL 两个 tenant，并逐题比较评分结果。
- 新增 opt-in PostgreSQL 集成回归。它只接受专用 `insightpilot_ip4` 测试数据库；只有显式设置 `INSIGHTPILOT_RUN_IP4_V14_POSTGRES_INTEGRATION=1` 才会重建该专用库并执行。

## 候选身份

v5 manifest SHA-256（PostgreSQL 通过后）：`060cbdf6e17563d3873b934819c79538b0d74d1c88a01bc58ce905136e02835d`。

| 工件 | SHA-256 |
|---|---|
| blind | `aecba9db0a9c977fa7f95378815965218707b53b68b705530673ad3aeed4e080` |
| gold | `8c01a8cb71f38c4c1885cb8ef59cb4ce7defaa01848d4c9efb09b6cf6e9558c3` |
| SQLite 数据库 | `50ae1d417a72c20699ab2c53775b3e530010f7e7f51a2cf66ef6ffcebd041b34` |
| planner | `78f3d980309ab7048b2c2b1fcfa7a4a4fd11a530f494119b5baa8bb14678aecc` |
| v14 scorer | `e1cb44704c3c0b6a46e8a594490da8030cef2688b8544b3457c97cab05fe9eb8` |

PostgreSQL image：`postgres:16-alpine`；server：`PostgreSQL 16.15`。独立 Compose project 使用随机回环端口与 tmpfs，无持久 volume；执行后只停止本次容器。原始 parity 报告保留在 v5 候选目录，未纳入仓库；原件 SHA-256：`f3777d3407a0f470734b6226c128830e625453e6b60b8a5af88dd390b373812b`。原始容器日志 SHA-256：`d15e3f6c5b09fae09164df553959d1866d5029fc32c6259dc55c6a1bea22c052`；日志本身未复制。

脱敏汇总：[ip4-v14-postgres-parity-summary-2026-11-15.json](../../reports/ip4-v14-postgres-parity-summary-2026-11-15.json)。副本 SHA-256：`6b990eec41698c0ca4d2c9ef55cfd38bda85ca6701d4031eb19b186fdb3b5492`。摘要只含合成评测总数、基线汇总、双库一致性和工件哈希；不含题文、逐题结果、tenant ID、数据库、日志正文、容器名或本机端口。

22 题分属退款与营销 ROI 两域，各 11 题；每域 7 道可答、4 道 guard。合成订单 50,000 条，model_calls 为 0。blind、gold、数据库哈希均与 v3 相同。

## 验证

- 全部 `tests/test_ip4*.py`：44 passed、1 skipped、41 subtests passed。
- `git diff --check` 通过。
- 14 道可答题的 planner 离线验证使用本地 stub 响应，覆盖指标检索、日期和维度校验；这不是模型质量结果。
- RulePlanner 基线在三个后端均为 8/22，逐题评分一致。它不具备 v14 全部指标解析能力；8/22 是离线规则基线诊断，不代表模型评测成绩。
- PostgreSQL 实测通过：临时隔离容器装载 v5 合成 SQLite 副本；SQLite 与 PostgreSQL `tenant-a`、`tenant-b` 的 22 题评分逐题一致，model_calls 为 0。临时 Compose 配置移除了仓库 Compose 的固定容器名和固定端口；本次容器已停止，未删除容器或卷。
- 通用 pytest 中 opt-in PostgreSQL 测试跳过 1 项；同一装载器和双租户评分路径已通过上述隔离容器运行。

复现时为 Compose 设新项目名，并用临时副本移除 `docker-compose.ip4.yml` 的固定 `container_name`，将端口改为 `127.0.0.1::5432`；保留 tmpfs，不挂卷。进程内设置临时 admin/app DSN 后运行：

```powershell
docker compose -p <唯一项目名> -f <临时隔离 compose 文件> up -d --wait postgres
.venv\Scripts\python.exe scripts\prepare_ip4_postgres.py --database "$env:V14_CANDIDATE\synthetic.sqlite"
.venv\Scripts\python.exe scripts\evaluate_ip4_v14_all_backends.py --candidate "$env:V14_CANDIDATE" --output "$env:V14_CANDIDATE\postgres-parity.json"
docker compose -p <唯一项目名> -f <临时隔离 compose 文件> stop postgres
```

不得运行 `compose down`。原始 v5 parity JSON 的 SHA-256 记录在脱敏摘要内；日志仅核哈希，不进版本库。

## 后续门槛

1. 所有者审阅 v5 22 道题、gold 口径、两套 ROI oracle、PostgreSQL parity 报告、请求预算和最终哈希后，再决定是否授权模型评测。
2. v5 当前预留 22 次请求 `$0.0468336`；若保留每题一次结构化响应重试，上限和 reserve 需改为 44 次、`$0.0936672` 并重新生成候选 manifest。
