# IP-4 v2：独立跨域契约回归

## 范围

新增 4 道合成题，退款与营销 ROI 各 2 道。每域包含 1 道可答题和 1 道歧义/范围澄清负例。v1 盲集、gold、manifest、评分器与模型报告均未修改。v2 不用于估计模型能力或生产准确率。

## Gold 与冻结

退款答案由独立订单/退款源行账本生成；ROI 答案由独立营销事件账本生成。两者都与编译后的只读 SQLite SQL 结果逐行比较。可答题从 agent 输入之外的 gold 文件评分。冻结 manifest 记录固定 multidomain 数据版本、fingerprint、seed、快照、blind/gold SHA-256 和历史精确题重叠数。

当前冻结值见 `evals/ip4-v2-manifest.json`；盲题和 gold 分别见 `evals/ip4-v2-blind.jsonl`、`evals/ip4-v2-gold.jsonl`。题重叠数为 0。所有数据均为固定合成数据。

## 验证与限制

`tests/test_ip4_v2_holdout.py` 验证可重复重建、blind 不含 gold、负例要求澄清、gold 重建稳定，并用 `RulePlanner` 跑完整集。模型调用 0，费用 0。小样本只作语义/回归检查；IP-4 仍部分完成，模型盲测与更广独立集未完成。

验证记录：Python 3.14.5 下，`python -m compileall -q data insightpilot tests` 通过；`python -m unittest tests.test_ip4_v2_holdout -v` 通过（1 项）。相关 IP-4 v1 与 IP-2 回归也通过（11 项）。全套测试未完成：Windows Python 3.14 在 `test_authenticated_tokens_route_concurrent_requests_without_header_override` 进入 `TestClient` 时，阻塞于 asyncio 事件循环 `socket.socketpair()` 初始化，尚未进入请求处理。该阻塞在临时目录创建后出现；仓库当前 README 声明 Python 3.11+，未设置上限。本轮不把它记作产品回归，也不宣称全套通过。
