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

使用 `insightpilot_ip4_app` 登录并切到 `insightpilot_reader`：tenant-a 与 tenant-b 各 6/6 个退款/ROI 查询均匹配 Python gold。无 tenant 上下文返回 0 行；DML 被拒；50 ms statement timeout 被触发，后续查询成功。查询包含退款活动归因 JOIN 与营销 ROI 事件/费用处理，因此两业务域都在 PostgreSQL 实测。

主机虚拟环境缺少 `psycopg` 和 `psycopg_pool`。安装项目已声明的驱动时 PyPI 请求超时。因此本次 PostgreSQL 执行通过 psql 直接运行由 SQLGlot 生成的 PostgreSQL SQL；未执行 Python `PostgresQueryEngine` 的真实连接池路径，也未测量连接池耗尽/恢复。不能把这项实测描述成完整应用端到端 PostgreSQL 验收。

## 成本上界预核算

现有模型成本账本 `reports/model-experiment-cost-ledger.json` 显示 $4.00 总上限尚余 $3.72695595；本阶段未发模型请求。若未来对 12 题各做最多 2 次请求，按每次最多 2,000 输入 token、1,024 输出 token，并采用账本的未缓存输入 $0.30/M、输出 $1.20/M 峰值价，预留上界为 $0.0438912。既有已计估算与该预留合计 $0.31693525，低于 $4.00；这只是上界核算，不是本阶段花费或授权调用。

## 复现

```powershell
.\.venv\Scripts\python.exe -m data.build_ip4_holdout
.\.venv\Scripts\python.exe -m insightpilot.ip4_evaluate
.\.venv\Scripts\python.exe -m unittest tests.test_ip4_holdout -v
```

PostgreSQL 双租户实测：

```powershell
$env:INSIGHTPILOT_IP4_ADMIN_PASSWORD = "local-test-only-change-me"
docker compose -f docker-compose.ip4.yml up -d --wait
.\.venv\Scripts\python.exe scripts\verify_ip4_postgres_psql.py --container insightpilot-ip4-postgres
docker compose -f docker-compose.ip4.yml down
Remove-Item Env:INSIGHTPILOT_IP4_ADMIN_PASSWORD
```

Docker Compose 使用临时 tmpfs 数据盘。`verify_ip4_postgres_psql.py` 会重建专用 `insightpilot_ip4` 数据库的 public schema；只对一次性测试容器运行。当前实测还未覆盖 psycopg pool exhaustion；安装驱动后运行 `scripts/verify_ip4_postgres.py` 补测。

驱动版准备/连接池验证使用同一个专用测试库：设置 `INSIGHTPILOT_IP4_ADMIN_DSN` 为该 PostgreSQL 管理连接，设置 `INSIGHTPILOT_IP4_APP_ROLE=insightpilot_ip4_app` 和一次性 `INSIGHTPILOT_IP4_APP_PASSWORD`，运行 `scripts/prepare_ip4_postgres.py`；再设置 `INSIGHTPILOT_IP4_APP_DSN` 为应用角色连接并运行 `scripts/verify_ip4_postgres.py`。准备脚本会重建该测试库的 `public` schema。

## 剩余边界

IP4 的离线盲集、独立 SQL/Python gold、SQLite 规则基线和 PostgreSQL 双租户查询均已实测。应用内 Python PostgreSQL adapter/连接池的端到端集成仍待 psycopg 可用后补验；本记录不把它记为已通过。
