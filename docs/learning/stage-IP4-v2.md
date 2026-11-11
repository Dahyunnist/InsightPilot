# IP-4 v2：独立跨域契约回归

## 范围

新增 4 道合成题，退款与营销 ROI 各 2 道。每域包含 1 道可答题和 1 道歧义/范围澄清负例。v1 盲集、gold、manifest、评分器与模型报告均未修改。v2 不用于估计模型能力或生产准确率。

## Gold 与冻结

退款答案由独立订单/退款源行账本生成；ROI 答案由独立营销事件账本生成。两者都与编译后的只读 SQLite SQL 结果逐行比较。可答题从 agent 输入之外的 gold 文件评分。冻结 manifest 记录固定 multidomain 数据版本、fingerprint、seed、快照、blind/gold SHA-256 和历史精确题重叠数。

当前冻结值见 `evals/ip4-v2-manifest.json`；盲题和 gold 分别见 `evals/ip4-v2-blind.jsonl`、`evals/ip4-v2-gold.jsonl`。题重叠数为 0。所有数据均为固定合成数据。

## 验证与限制

`tests/test_ip4_v2_holdout.py` 验证可重复重建、blind 不含 gold、负例要求澄清、gold 重建稳定，并用 `RulePlanner` 跑完整集。模型调用 0，费用 0。小样本只作语义/回归检查；IP-4 仍部分完成，模型盲测与更广独立集未完成。

初次验证记录：宿主 Python 3.14.5 上，完整收集 116 项；101 项不依赖 `TestClient`/`asyncio.run()` 的测试通过。另 15 项因 Windows asyncio `socket.socketpair()` 初始化阻塞未运行。临时目录夹具指向 `.venv/test-tmp`，路径和清理均已核对。

后续容器复验：复用本机已有 `insightpilot-api-probe:latest`（Python 3.12.15，镜像 ID `e42bdb34efa5`），Docker 29.1.3。测试源码副本与临时 SQLite 文件放在容器 tmpfs，禁用网络；未挂载 `.env`，仅设置本次运行的 dummy `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 以供模块导入。未调用真实模型或外部 API。tmpfs 下先前因 Windows 挂载盘 I/O 超时的两项测试均通过；新增 v3 后完整套件 117/117 通过，用时 16.935 秒（容器内测试计时）。容器用 `--rm` 清理，未启动 PostgreSQL，因为测试套件不依赖外部 PG 服务。完整命令、首次挂载盘失败和宿主/容器差异见 `reports/ip4-v3-verification.json`。
