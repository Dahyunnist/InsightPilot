# IP-4 v14 校验器离线复核（2026-11-16）

## 修复

- 候选加载器现逐字节核对 manifest 声明的 blind、gold、SQLite SHA-256；评分器也核对这三项原始哈希。此前文本哈希会把 CRLF/LF 变化规范化，且评分器未核 SQLite 文件哈希。
- 双租户 parity 向量现包含规范化分析结果指纹。此前只比通过状态和布尔评分位，数值不同但都落在容差内时可能被判相同。
- run-manifest v2 事件覆盖条目现只接受 `count`、`coverage` 两字段，阻止未识别字段混入覆盖声明。

## 离线证据

- 现有仓库外 v14 合成候选通过内存 lock 校验：22 个 blind case、manifest 与数据哈希匹配、`model_calls=0`。Gold 文件只参与 SHA-256 计算；未解析内容或运行评分。
- `tests/test_ip4_v14_validation_contract.py` 与 opt-in PostgreSQL 集成模块：`5 passed, 1 skipped`。新增回归覆盖候选三类工件哈希、manifest 与 SQLite 哈希错配、文本换行篡改、实际结果 parity 指纹差异及 v2 事件字段。
- PostgreSQL 集成因需要专用数据库而跳过；本轮没有启动数据库、Docker、模型或网络连接。真实双租户结果仍待宿主证据。
- 未读取仓库 reports/evals 冻结内容，未改候选、gold、账本或历史报告。

## 审批门

候选仍为 `candidate_pending_owner_review`。外部模型调用前，所有者需审阅 22 道 blind 题、gold 口径与退款/ROI oracle、manifest/数据哈希、双租户 PostgreSQL parity 证据及请求预算；之后还需单独授权模型评测。本轮没有调用模型。
