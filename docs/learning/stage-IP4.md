# IP-4：跨业务域冻结评测与双后端回归

## 交付

- 冻结 12 道全新合成题：退款 6 道、营销 ROI 6 道；可回答题与拒答/澄清题各 6 道。
- `evals/ip4-blind-v1.jsonl` 只含题目 ID 与问题文本。评测 Agent 只收到 blind 问题；gold 在独立评分器中加载，不进入 planner 或 Agent 请求。
- `evals/ip4-gold-v1.jsonl` 由独立 Python 源行/事件账本生成。所有数值结果都与编译后的只读 SQLite SQL 逐组交叉核对。
- manifest 固定数据版本、seed、快照、数据 fingerprint、blind/gold SHA-256，并记录与历史 eval 的规范化问题重叠数为 0。
- `insightpilot.ip4_evaluate` 对同一冻结集运行 SQLite + `RulePlanner`，输出总分、业务域分数、Wilson 95% 区间、题目 bootstrap 区间、题级失败及拒答指标。
- PostgreSQL 实测脚本在 PostgreSQL 16 上导入两个租户的完整合成数据，按 SQLGlot 生成的 PostgreSQL SQL 执行 6 道可回答题；检查两个租户的结果、无 tenant 上下文 fail-closed、DML 拒绝和 statement timeout 后恢复。

## 离线 SQLite 结果

运行日期：2026-10-23。没有调用模型；账本未改动，模型调用 0，估算费用 $0。

| 业务域 | 通过 | 题数 | 严格准确率 | Wilson 95% 区间 |
| --- | ---: | ---: | ---: | --- |
| 退款 | 6 | 6 | 100% | 60.97%–100% |
| 营销 ROI | 6 | 6 | 100% | 60.97%–100% |
| 合计 | 12 | 12 | 100% | 75.75%–100% |

规则基线可回答题通过 6/6。三道预期拒答题全部正确拒答；可回答题错误拒答 0；拒答题被错误接受 0；不支持的营销地区请求被接受 0。所有数值题的独立核验通过。

题目经过业务域分层抽样，数量小且是合成固定集。区间仅说明本集的不确定性，不能推断生产准确率。规则基线不是模型评测，不能替代之后的真实模型盲测。

## PostgreSQL 实测

本机 Docker CLI 初始受 workspace 沙箱限制，访问 `docker_engine` 命名管道返回 permission denied。授权的只读检查通过后确认 Docker 29.1.3 可用，已有 `postgres:16-alpine` 镜像；以 tmpfs 启动一次性 PostgreSQL 16.15 容器完成测试。

应用层接好后，`scripts/verify_ip4_postgres.py` 经 FastAPI → InsightPilot → psycopg `PostgresQueryEngine` 执行完整 12 题。tenant-a 和 tenant-b 各 12/12 通过；每租户退款 6/6、营销 ROI 6/6。R03 退款归因 JOIN 与 M01 ROI JOIN 在两租户都匹配独立 Python gold 和 SQLite verifier。PG/SQLite data version 与 fingerprint 一致。三题预期拒答全部正确；错误拒答 0、错误接受 0、不支持地区被接受 0。

另实测缺 tenant 被拒、应用只读角色 DELETE 被拒、50 ms `statement_timeout` 后可继续查询，以及 max_size=1 连接池以 200 ms checkout timeout 拒绝超额借用、释放连接后恢复。完整结果见 `reports/ip4-postgres-verification.json`。此前 psql-only 结果保存在 `reports/ip4-postgres-psql-verification.json`。

## 成本上界预核算

现有模型成本账本 `reports/model-experiment-cost-ledger.json` 显示 $4.00 总上限尚余 $3.72695595；本阶段未发模型请求。若未来对 12 题各做最多 2 次请求，按每次最多 2,000 输入 token、1,024 输出 token，并采用账本的未缓存输入 $0.30/M、输出 $1.20/M 峰值价，预留上界为 $0.0438912。既有已计估算与该预留合计 $0.31693525，低于 $4.00；这只是上界核算，不是本阶段花费或授权调用。

## 复现

```powershell
.\.venv\Scripts\python.exe -m data.build_ip4_holdout
.\.venv\Scripts\python.exe -m insightpilot.ip4_evaluate
.\.venv\Scripts\python.exe -m unittest tests.test_ip4_holdout -v
```

PostgreSQL 16.15 双租户 API 实测（需先准备专用临时数据库）：

```powershell
$env:INSIGHTPILOT_IP4_ADMIN_PASSWORD = "<临时管理员密码>"
docker compose -f docker-compose.ip4.yml up -d --wait
# 设置 INSIGHTPILOT_IP4_ADMIN_DSN、INSIGHTPILOT_IP4_APP_ROLE、INSIGHTPILOT_IP4_APP_PASSWORD
.\.venv\Scripts\python.exe scripts\prepare_ip4_postgres.py
# 设置 INSIGHTPILOT_IP4_APP_DSN
.\.venv\Scripts\python.exe scripts\verify_ip4_postgres.py
docker compose -f docker-compose.ip4.yml down
```

驱动脚本通过 FastAPI → InsightPilot → `PostgresQueryEngine` 跑完整盲集。tenant-a、tenant-b 各 12/12；R03 与 M01 JOIN 均通过；预期拒答 3/3、错误拒答 0、误接受 0。无 tenant 上下文被拒，DML 被数据库拒绝；50 ms statement timeout 与 max_size=1 pool 耗尽后均恢复。报告写入 `reports/ip4-postgres-verification.json`。测试库在 Compose tmpfs 中，准备脚本会重建专用库 public schema；仅用于一次性测试容器。

费用账本未变，本阶段没有模型请求。
## 剩余边界

IP4 离线盲集、独立 SQL/Python gold、SQLite 规则基线、真实 PostgreSQL API 双租户查询和连接池恢复均已实测。tenant ID 是每个 API 实例的静态部署配置；同一进程按用户动态选择租户，仍需经过认证主体映射后再扩展，当前不会读取调用方可伪造的 tenant header。
