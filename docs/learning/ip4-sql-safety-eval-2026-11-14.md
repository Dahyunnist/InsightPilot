# IP-4 危险 SQL 离线验收

## 目标与边界

S4 要求报告危险查询拦截率。新增 10 个固定合成危险 SQL 用例和 1 个安全查询对照。评测直接调用 `insightpilot.sql_policy.validate_sql`；安全对照再经 `execute_readonly` 只读执行。

危险 SQL 在 AST 检查后即停止，**不会送入 SQLite 执行**。评测无模型请求，也不使用业务库、用户数据或网络。结果只表示这 10 个预设类别被当前策略拒绝，不表示对抗攻击覆盖率或生产安全率。

## 可复现

```powershell
Set-Location E:\job\insightpilot
.\.venv\Scripts\python.exe -m scripts.evaluate_sql_safety
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_sql_safety_eval.py tests/test_s2_policy.py
```

结果：11/11 用例符合预期；危险 SQL 10/10 被拒绝，安全只读查询 1/1 通过；7 项测试和 9 个子测试通过。模型调用 0。

新增后的完整测试套件重跑超过三分钟没有输出，随后中断；该次全套结果未确认。上一轮完整套件在本评测加入前为 206 项通过。新增代码的验证范围以本页的定向测试为准。

## 当前评测证据一致性

另有只读审计 `scripts/audit_current_benchmark_evidence.py`，核对当前 30 题原始模型结果中的 14 个 answered case。定义来源、来源标题、公式、版本、SQL 策略和 verifier 均匹配 14/14；lineage 与现行指标目录精确匹配 0/14，因此完整证据包为 0/14，报告状态为 partial。历史结果中的 lineage 不应被静默覆盖；需确认生成时使用的目录版本或在同一冻结集重新生成证据后再重评完整性。该审计不调用模型。

冻结题集：`evals/sql-safety-v1.jsonl`。逐例结果和题集 SHA-256：`reports/sql-safety-v1.json`。评测代码：`scripts/evaluate_sql_safety.py`。修改题集须使用新版本号，不能覆盖 v1 报告。

## 失败路径

危险 SQL 被策略接受时，评测记为 `unexpectedly_allowed_by_ast`，不执行该查询，整轮失败。安全对照被拒绝或只读执行失败时，同样记失败，便于发现过度拦截。

此测试复用 AST 安全边界。SQLite authorizer、只读 URI、结果行数和超时由既有 S2/S3 测试覆盖；本评测不单独量化各底层控制的召回率。
