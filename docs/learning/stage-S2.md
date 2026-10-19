# S2 学习记录：SQL 边界与数字复核

## 本阶段问题与业务影响

只读提示词不能限制数据库。S2 在执行路径加入 SQLGlot AST 策略、SQLite authorizer、只读 URI、行数/时间/虚拟机指令预算，并独立复算核心指标。任何策略拒绝或复核冲突都不返回经营结论。

## 数据与状态流

结构化计划 → 固定 SQL 模板 → SQLGlot 按 SQLite 方言解析 → 检查单条 SELECT、CTE、表列/函数、数据库引用和 LIMIT → SQLite 只读 URI + `query_only` + authorizer → 2 秒与 250,000 条虚拟机指令预算 → 最多 100 行 → 用独立标量子查询复算分子/分母 → 证据一致才进入 `answered`。

拒绝分三层：AST 不合法时在数据库连接前拒绝；SQLite authorizer 限制实际读表/列/函数；只读文件模式和 `query_only` 拦截写操作。VM 指令预算是本地资源上限，不等于物理扫描字节计量。

关键文件：

- `insightpilot/sql_policy.py`：SQLGlot AST allowlist。
- `insightpilot/runner.py`：SQLite 权限回调、行数限制、超时和 VM 指令预算。
- `insightpilot/verification.py`：独立标量聚合及匹配检查。
- `insightpilot/agent.py`：复核失败时停止答案输出。
- `tests/test_s2_policy.py`：危险 SQL 拒绝、超时/预算、只读和复核测试。
- `requirements.txt`：固定 SQLGlot 版本 `30.23.0`。

## 设计取舍

- SQL 仍由固定模板构造；AST 策略作为纵深检查，不把任意 SQL 生成能力交给模型。
- 只允许 `orders`、`refunds`、`products` 和业务所需列；禁止跨库、未授权 CTE、星号投影和未批准函数。
- SQLite `mode=ro`、`PRAGMA query_only` 与 authorizer 都保留。AST 可能漏掉语法边角，数据库仍须拒绝写入。
- 独立校验用 `EXISTS` 或原始退款行聚合重算总分子/分母，并与分组聚合对照；贡献度再核对分组占比和为 1。
- 超时通过 SQLite progress handler 中断；执行预算按虚拟机指令近似，而非底层 I/O 字节。

## 两条失败路径

1. 多条 SQL、`DELETE`、跨库表、客户标识列、危险函数、超大 LIMIT 或不受支持 CTE → SQLGlot 策略拒绝 → 不执行查询。测试用例逐项覆盖这些输入。
2. 分组查询和独立总计的分子/分母不一致 → `verification.passed=false` → Agent 返回 `failed`，不呈现结论。正常路径测试同时确认订单率、金额率、贡献度三类 SQL 均通过独立复核。

另有两个资源失败状态：progress handler 可触发查询超时，VM 指令超过 250,000 时返回预算失败；测试通过缩小预算确定性复现。

## 测试复现与真实结果

首次建立环境后执行：

```text
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m insightpilot.cli "2026-10-05 到 2026-10-11 华东金额退款率是多少？"
```

本次实跑：`Ran 15 tests ... OK`。真实 LLM 闭环返回 50,000 分 / 90,000 分 = 55.5556%；独立标量复核三项检查全为 `true`。危险 SQL 用例均在策略层拒绝，数据库写操作另由只读连接阻止。

## 亲手实验（约 15 分钟）

1. 用 `sql_policy.py` 的测试例逐个运行恶意查询，观察拒绝原因（4 分钟）。
2. 临时将 `LIMIT 100` 改成 `LIMIT 101`，运行测试，观察 AST 预算拒绝；恢复代码（3 分钟）。
3. 在测试中篡改一行分组分子，调用 `verify_result`，观察 `passed=false`，再恢复数据（4 分钟）。
4. 把 progress handler 测试预算改为 0，运行单项测试，观察超时或 VM 预算状态（2 分钟）。
5. 解释 SQLite 文件只读与 AST 检查分别保护哪一层（2 分钟）。

检查点：能说清楚“只读提示词”不是安全边界；能手动追踪一次策略拒绝和一次独立数字复核。

## 面试追问与回答

**问：为什么 AST 校验后还要只读连接？**
答：解析器策略只是应用层判断，方言差异或校验遗漏仍可能存在。数据库权限提供执行时的第二道拒绝边界。

**问：独立复核如何减少静默错数？**
答：主查询按维度聚合；校验查询用不同 SQL 结构单独算整体分子/分母。分组汇总对不上时保留失败状态，不输出结论。

**问：250,000 VM 指令等于扫描预算吗？**
答：不等于字节扫描量。它限制 SQLite 虚拟机工作量，适合本地原型；生产数据库需使用原生超时、资源组或扫描成本估计。

## 风险与停止点

拒绝个人信息仍靠关键词前置拦截；本项目固定合成数据也没有真实多租户 ACL。SQLGlot 版本锁定在当前测试版本。独立复核能发现聚合不一致，不能证明口径本身必然正确。S2 已完成；按最新授权继续 S3，不再等待阶段确认。
