# IP-3 学习记录：HTTP 服务与运行边界

## 2026-11-14 请求超时的持久取消标记

API 请求等待超过 `request_timeout_seconds` 时此前只设置内存取消事件，`/runs/{id}` 的持久记录仍显示 `cancel_requested=false`。现在 504 返回前先尝试写入 RunStore 取消标记，再设置协作事件；若存储暂时不可用，仍发送内存取消信号。新增不启动 ASGI/asyncio 的同步 endpoint 回归：确认 504 后记录为 `running` 且 `cancel_requested=true`，释放固定阻塞 planner 后最终落为 `cancelled`；同一幂等键随后重放返回原 run 的 409，不会二次规划。真实供应商 SDK 是否响应取消信号仍需独立宿主验证；此改动只覆盖 API 与 RunStore 的协作取消语义。

## MCP 请求取消信号桥接

MCP 工具此前同步调用 `core.analyze()`，没有传递取消事件。现工具改为异步入口，把分析放入 AnyIO worker thread；MCP 调度器取消工具任务时，入口设置 `threading.Event`，让分析核心在后续阶段检查并停止。离线单元测试直接驱动工具协程与 worker，覆盖正常返回和取消事件传递；API timeout、取消登记竞态及并发同键三项回归一并通过。本轮未执行完整 Streamable HTTP MCP 传输回归，因为当前 Windows Python 3.14 的 asyncio loop 初始化会卡住。取消不能中断已经在进行中的供应商 HTTP 请求；底层网络取消仍需供应商/宿主验证。

## 幂等重放与限流次序

分析接口现在先认证、校验幂等键并查重，再对新工作计入每分钟限额。相同主体、幂等键与问题的重放返回原 run，不消耗新工作额度；不同问题仍返回 409。限流仍保护新建分析，超限返回 429。回归覆盖限额为每分钟 1 次时首请求和幂等重放成功、另一新请求被限流。取消、超时、容量信号量与 PostgreSQL 连接池保持原有测试边界；这些离线测试不能替代宿主 Postgres 集成。

## 本机 HTTP 基线（非 Compose）

固定规划器处理合成 SQLite 数据，规划延迟 150 ms。真实 Uvicorn HTTP 服务按并发 1、5、20 各发一轮同步 `/analysis` 请求。没有模型调用。p50/p95 含 429 快速拒绝，因此不等于成功分析的独立延迟分布；请求同步等待分析结果，也不是异步任务吞吐。

| 并发 | 请求 | 成功 | 429 | 503 | p50 | p95 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 1 | 0 | 0 | 209.027 ms | 209.027 ms |
| 5 | 5 | 5 | 0 | 0 | 224.398 ms | 234.743 ms |
| 20 | 20 | 8 | 12 | 0 | 13.390 ms | 261.613 ms |

20 并发时 8 个分析占满工作槽，12 个请求收到 429；没有 503。较低 p50 来自快速拒绝。

幂等重放返回相同 run ID 和 `Idempotency-Replay: true`；相同键换问题返回 409。现有运行时回归测试 25 项通过，覆盖速率限制、504 超时、协作取消、工作槽恢复、取消端点和 RunStore 生命周期。PostgreSQL 单连接池耗尽后恢复、statement timeout 后恢复见 `reports/ip1-ip2-postgres-acceptance-v1.json`。

## Compose 容器复验

`docker-compose.ip3.yml` 和 `Dockerfile.ip3` 只装载合成数据与固定规划器。2026-11-12 用户提供本机 Compose 成功运行结果：容器 healthy 后清理；并发 1 为 1/1 HTTP 200，p95 194.475ms；并发 5 为 5/5 HTTP 200，p95 210.985ms；并发 20 为 8/20 HTTP 200、12 个 429、0 个 503，p95 225.932ms（包含被拒绝请求）。幂等首请求与重放均 200、run_id 相同、重放头为 true；同 key 不同问题返回 409。使用 synthetic planner 与 SQLite，0 模型调用；不覆盖 PostgreSQL/RLS 或生产负载。证据见 `reports/ip3-docker-smoke-20261112.json`。

本机 Compose 验收结果：

在仓库根目录复跑：`.\scripts\run_ip3_compose.ps1`。脚本先检查 Docker Engine、Compose v2、Python venv、缓存的 `python:3.12-slim` 和当前项目容器；镜像缺失时不自动 pull。若端口 58080 被其他服务占用，Compose 启动会失败并进入 finally 清理本项目资源；手动停止命令为 `docker compose -f .\docker-compose.ip3.yml down`。不要添加 `-v`。

2026-11-13 离线宿主检查中，Docker CLI 存在，但 daemon pipe `//./pipe/dockerDesktopLinuxEngine` 返回 `Access is denied`。因此本轮无法读取 `python:3.12-slim` 镜像是否仍在本地，也无法确认当前 Compose 容器状态；没有启动或清理任何容器。此前 2026-11-12 用户提供的 healthy/HTTP smoke 记录仍是最近可用运行证据，不据此推断当前 Docker 状态。

| 检查 | 结果 |
| --- | --- |
| 幂等首请求 / 重放 | 200 / 200，同一 `run_id` |
| 幂等冲突 | 409 |
| 并发 1 / 5 | 全部 200 |
| 并发 20 | 8 个 200，12 个 429，0 个 503 |
| 后端 / 规划器 / 模型调用 | 合成 SQLite / 固定规划器 / 0 |
| 容器与网络 | 验收后清理 |

以上数字来自用户提供的本机运行结果。历史本机 HTTP 延迟数据仍见 `reports/ip3-http-acceptance-v1.json`，不作为 Compose 延迟证据；Compose 输出本次未归档延迟字段。

复现命令：

```powershell
.\scripts\run_ip3_compose.ps1
```

本次仅覆盖合成 SQLite 与固定规划器的一次 Compose 验收。结果不代表真实业务数据、模型能力、生产表现或一般性能提升。Docker 不可用时可运行 `scripts/smoke_local.py` 验证本机健康检查与只读查询；该冒烟不替代容器测量。

## Compose 强制进程终止与恢复（2026-11-11）

路线 IP-3 还要求主动终止运行中的服务进程。本轮新增 `docker-compose.ip3-restart.yml` 与 `scripts/verify_ip3_process_restart.py`，复用本机 `insightpilot-api-probe:latest`（Python 3.12.15，image ID 前缀 `e42bdb34efa5`），未构建或拉取镜像。环境只挂载源码目录，不挂载 `.env`；规划器固定等待 4 秒，数据为合成 SQLite，无模型或外部 API 调用。请求经 localhost 真实 HTTP 端口映射进入 FastAPI。

运行命令：

```powershell
.\.venv\Scripts\python.exe scripts\verify_ip3_process_restart.py
```

结果：运行中的幂等请求先以 202 重放并取得同一 `run_id`；对 API 容器发送 SIGKILL 后，客户端收到 `RemoteDisconnected`。Compose 启回同一个容器 ID，`RunStore` 恢复后 `/runs/{id}` 返回 `interrupted` 且无结果；同一幂等键重试返回 409 `result_unavailable`，没有重复执行。验收用时 6.688 秒；容器和专用网络清理成功。完整报告见 `reports/ip3-process-restart-acceptance.json`。

该验收只覆盖固定规划器、合成 SQLite 和一次强制进程终止；不证明 PostgreSQL 场景、模型请求中断、OpenTelemetry、持续负载或生产恢复能力。IP-3 仍部分完成。

## SQLite 故障注入与 PostgreSQL 连接池恢复（2026-11-11）

新增 `tests/test_ip3_database_recovery.py` 与 `scripts/verify_ip3_postgres_disconnect.py`。SQLite 回归用合成数据库、固定规划器和一次性故障注入器：首次查询阶段断连返回并持久化 `failed`；规划调用数为 1，证明无自动重试；相同幂等键重放原失败且不再规划；同键换问题返回 409；新键重试成功。另一项并发 API 回归在固定规划器执行期间请求取消，验证结果和持久运行状态均为 `cancelled`、`cancel_requested=true`，幂等重放不重复执行。Python 3.12 容器定向测试 2/2 通过。

PostgreSQL 回归在一次性 tmpfs PostgreSQL 16 容器中装入合成双租户 fixture，由管理员定向 `pg_terminate_backend` 断开应用连接池会话。池丢弃坏连接并重新连库，后续只读查询成功；报告为 `reports/ip3-postgres-disconnect.json`。脚本不会自动重试业务查询。

此轮没有在 PostgreSQL 整个服务停机期间经过 FastAPI 请求，因此 PostgreSQL 故障时 API `RunStore` 状态与幂等重放尚未端到端验证；也未测模型请求中断、OpenTelemetry、持续负载或生产恢复。仅验证 PostgreSQL 客户端会话被强制断开后的连接池恢复。没有使用项目 `.env`、客户数据或真实模型调用。

## PostgreSQL 服务停机、恢复与连接池重建（2026-11-11）

新增 `docker-compose.ip3-db-outage.yml`、`scripts/ip3_postgres_probe_app.py` 与 `scripts/verify_ip3_postgres_service_outage.py`。真实 HTTP API 使用固定规划器和合成退款数据；模型端点为不可达 dummy 地址，规划器计数器确认没有外部模型调用。PostgreSQL 16 使用隔离容器自身可写层；没有宿主机数据卷或 `.env`。数据库停机后对同一 PostgreSQL 容器执行 `start`，确保角色和 fixture 与原服务相同。

首次端到端重启检查失败：pool 在服务重新启动后持续尝试旧连接，且发现 tmpfs 会在容器停启时清空数据库目录。`PostgresQueryEngine` 现仅对自有池发生 psycopg `OperationalError` / `InterfaceError` 时，在当前失败请求返回后废弃旧池；下一请求惰性创建新池，不重放失败 SQL。调用方注入的池不由引擎关闭；SQL statement timeout (`57014`) 不触发池重建。新增回归覆盖连接错误重建、非连接错误与 statement timeout 保留池。

完整验收报告：`reports/ip3-postgres-service-outage.json`。步骤：基线查询 HTTP 200、planner 次数 1；`docker compose stop postgres` 后 API health 仍 200；查询以 HTTP 502 `failed` / `dataset` / `db_execution` / `database_execution` 持久化，规划器次数不变且模型调用数 0；同幂等键返回相同 `run_id` 和 replay 标记；`docker compose start postgres` 后直接连接探针成功，新分析 HTTP 200，planner 总次数 2，池可用连接由 0 回到 1。Compose `down --remove-orphans` 成功，无残留容器。定向 Python 3.12 回归 18/18 通过。

该数据库验收覆盖一次本机合成 PostgreSQL 服务停机和同容器恢复，不代表生产负载或 SLA。当时模型请求中断、OpenTelemetry 和持续负载尚未验收；离线 trace 验收见下节。IP-3 保持部分完成。未读取 `.env`，无客户数据及真实模型调用。

## OpenTelemetry 离线 trace 脱敏验收（2026-11-11）

新增可选 tracing seam：设置 `OTEL_EXPORTER_OTLP_ENDPOINT` 时，API 启动 OTLP/HTTP exporter 和 `BatchSpanProcessor`，API `analysis` span 包住 Agent `run` 子 span；关闭时执行 provider shutdown/flush。`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 可覆盖 traces URL；否则从 endpoint 添加 `/v1/traces`。未设置 endpoint 时 API tracing 无操作。SDK 与 exporter 不加入默认依赖，需额外安装 `requirements-otel.txt`。span 只写 `run.id`、结果状态、失败阶段、固定错误分类与层级；不记录问题文本、异常文本、SQL、SQL 参数、tenant ID，也不自动记录 exception event。失败 status description 只用错误分类/阶段。Exporter 出错不会影响分析请求。

`tests/test_ip3_tracing.py` 用注入式内存 span exporter 对真实 FastAPI 测成功和数据库身份读取失败：均生成 API 父 span 与 Agent 子 span；成功标记 OK，失败标记 ERROR 并带 `database_execution` / `db_execution` 分类。Fake provider 验证 endpoint、批处理器挂载和 shutdown；endpoint 缺失时确认无操作。Exporter endpoint 使用 `.invalid`，没有真实 Collector 网络请求。断言覆盖问题标记、租户标记、日期 SQL 参数与地区参数均不出现在 span。Python 3.12 离线容器、源码只读挂载、网络关闭，trace、IP-3 故障恢复和查询引擎定向测试 15/15 通过；无真实模型调用。

该步骤先验证可选 OTLP 导出配置与脱敏契约；当时未连真实 Collector。实际 Collector 联通见下节。模型请求中断、采样/保留策略和生产导出链路仍未验收。

## 本机 OTel Collector 联通验收（2026-11-11）

本机已有官方 `otel/opentelemetry-collector-contrib:0.138.0` 镜像。新增 `docker-compose.ip3-otel-local.yml` 的 `otel-acceptance` profile、Collector 最小 OTLP/HTTP receiver + batch + debug exporter 配置，以及 `scripts/ip3_otel_probe_app.py` / `scripts/verify_ip3_otel_collector.py`。profile 使用 Compose `internal: true` 网络，无 host ports；API 和 Collector 不连外网。脚本固定使用 dummy LLM 配置和固定 planner，不挂载 `.env`，只装合成退款库。

可复现流程：将 `requirements-otel.txt` wheel 下载至仓库临时目录 `.tmp-ip3-otel-wheels`，再用 `--network none` 安装至 `.tmp-ip3-otel-packages`；运行验收脚本后删除两个目录。脚本只使用本机已有 Collector 镜像，镜像 tag 加 `pull_policy: never`；profile teardown 显式带 profile，检查 API/Collector 容器和内部网络均已清理。命令：

```powershell
New-Item -ItemType Directory -Force .tmp-ip3-otel-wheels, .tmp-ip3-otel-packages | Out-Null
docker run --rm --network bridge -v "${PWD}/requirements-otel.txt:/requirements-otel.txt:ro" -v "${PWD}/.tmp-ip3-otel-wheels:/wheels" insightpilot-api-probe:latest python -m pip download --dest /wheels -r /requirements-otel.txt
docker run --rm --network none -v "${PWD}:/workspace:ro" -v "${PWD}/.tmp-ip3-otel-wheels:/wheels:ro" -v "${PWD}/.tmp-ip3-otel-packages:/packages" insightpilot-api-probe:latest python -m pip install --no-index --find-links=/wheels --target=/packages -r /workspace/requirements-otel.txt
.\.venv\Scripts\python.exe scripts\verify_ip3_otel_collector.py
Remove-Item -Recurse -Force .tmp-ip3-otel-wheels, .tmp-ip3-otel-packages
```

实测 HTTP 分析 200 / `answered`，模型调用数 0。Collector 收到 1 条 trace、2 个 span：API root `insightpilot.api.analysis` 与 Agent child `insightpilot.agent.run`，同 trace ID，状态均为 answered。问题标记、测试 bearer token、租户标记、日期与地区参数均不在 Collector 输出。完整证据见 `reports/ip3-otel-collector.json`。临时 profile 及网络已移除；OpsPilot 容器 9 个，验收前后相同，未触碰。

这是本机官方 Collector 单次联通验收，不代表生产 Collector 部署、采样/保留策略或持续负载。模型请求中断仍未验收；IP-3 仍部分完成。

## 慢规划器超时与失败重放（2026-11-11）

新增 `test_model_timeout_persists_failure_and_idempotent_retry_does_not_replan`。可控 fake planner 进入等待后，API 请求超时返回 504；同键重放仍为运行中 202，没有启动第二次规划。释放 fake planner 后，它返回合成 `PlanError`；RunStore 持久化 `failed` / `planning`，结果不含 plan 或 rows。终态同键重试返回同一 `run_id` 的 502 已存失败，planner 调用数仍为 1。新幂等键发起后续请求则成功并持久化 `answered`，planner 调用数增至 2。测试没有真实模型调用，也没有伪答案。

同文件既有取消回归验证显式取消持久化 `cancelled`、同键重放不重复规划；进程重启验收 `reports/ip3-process-restart-acceptance.json` 验证遗留 `running` 变为 `interrupted`，同键重试返回 `result_unavailable` 而不重执行。Python 3.12 禁网容器中，IP-3 数据库恢复、S6 运行边界、S12 恢复回归共 23/23 通过。假 planner 只覆盖 API/RunStore 中断语义，不模拟供应商 SDK 的底层网络取消。IP-3 仍部分完成。

## 2026-11-13 Compose runner 修复与冒烟尝试

Compose runner 固定独立项目名，先检查配置和遗留容器，再用 `build --pull` 构建基础镜像；启动后等待健康状态，退出时只清理该项目容器/网络，不删卷。UTF-8 BOM 保留，兼容 Windows PowerShell 5.1。Docker Hub token 请求超时，构建未完成、容器未启动；详见 `reports/ip3-docker-smoke-20261113.json`。PowerShell parser/runner 行为测试覆盖脚本格式、构建顺序、隔离与清理。IP-3 live smoke 仍 pending，未调用模型。

## 2026-11-13 取消与事件登记竞态

审查确认幂等键以 principal scope 后哈希入库；SQLite `BEGIN IMMEDIATE` 与唯一索引保证同键同问题只建一个 run；不同问题复用同键返回冲突。超时先返回 HTTP 504 并置取消事件，worker 终结后持久化失败/取消结果；进程重启把遗留 `running` 标为 `interrupted`。

修复一个取消窗口：run 已持久化为 `running`、内存取消事件尚未登记时，取消请求会保存 `cancel_requested=1`，但旧代码丢失事件通知。事件登记后现在重读持久标记并补设事件。新增 Event barrier 纯进程回归：在该窗口请求取消，worker 观察取消并持久化终态；同键重放仍指向原 run，planner 只执行一次，查询执行数为零。该定向回归通过。

尝试运行现有 TestClient API 回归时，Windows Python 3.14 在 `asyncio.new_event_loop()` 的本地 socketpair 初始化处挂起，测试未进入断言；Selector loop 也无法初始化。未启 Docker、模型或外部服务。PostgreSQL/RLS 与并发负载仍待宿主联调。

## 2026-11-13 宿主 Compose、API 与 RLS 验收

PowerShell Parser 与 UTF-8 BOM 检查通过。首次 runner 强制刷新基础镜像时，Docker Hub token 请求超时；本机随后确认 `python:3.12-slim` 已缓存，runner 改为缓存存在时 `build --pull=false`、缺失时才拉取。第二次宿主运行构建通过（pip 层命中缓存），Compose health 为 healthy，真实 HTTP API smoke 通过，0 模型调用。幂等首次/重放均 200 且 run ID 一致，冲突 409；并发 1/5/20 分别返回 1/1、5/5、8/20 个 200，20 并发另有预期 12 个 429、0 个 503。脚本 down 已移除专用容器与网络。明细见 `reports/ip3-compose-http-20261113.json`。

独立 PostgreSQL/RLS runner 也已通过：PostgreSQL 经 FastAPI/InsightPilot/PostgresQueryEngine 跑冻结 synthetic cases，tenant-a、tenant-b 各 12/12；缺失 tenant 被拒，DML 被拒，statement timeout 与连接池耗尽/恢复均符合预期，数值经独立 SQLite gold 核验。runner stop 发出警告；随后只读检查确认本次专用容器已 `Exited (0)`，再移除该容器，未删卷。RLS 详情见 `reports/ip4-postgres-verification.json`。

本次 `tests.test_ip3_database_recovery` 5/5 通过，含取消事件登记竞态、并发同键只保留一个可执行 run、取消/超时重放和数据库失败恢复。Compose 负载仅为合成 SQLite 上最多 20 个短 burst，不是持续并发验收；PostgreSQL RLS corpus 固定为每租户 12 题、顺序执行。

首次 registry token 超时与宿主权限边界、Compose HTTP、RLS、清理及负载限制汇总见 `reports/ip3-host-integration-20261113.json`。此前“TestClient 无法初始化”的默认沙箱尝试已由宿主权限 API 回归通过覆盖。
