# S7 学习记录：扩展数据、归因与盲测

## 阶段目标

保留 S0 作为稳定的微型 gold，另建 50,000 单确定性企业风格夹具；加入跨表营销归因、月/地区分组、迟到数据质量与独立 holdout。扩展结果只描述合成集。

## 原理与数据流

`data.enterprise_seed` 用固定随机种子生成订单、退款、客户、商品、campaign 和 1–2 个加权 touch。批准退款按订单 cohort 聚合，再乘 campaign weight 分摊；归因缺失金额保留在“未归因”。权重应逐订单和为 1。只读 SQL 经过同一 AST/authorizer，并按 enterprise data version 提升有限 VM budget，仍有 2 秒时间预算和 100 行上限。

planner 只收到盲测问题和候选语义定义。`build_enterprise_gold.py` 另读固定表行，用 Python 字典聚合出三个 gold；评测器读取 gold，但不把它送给模型。主臂、无语义检索、无独立复核各写独立逐例 JSONL。图表规范由经复核后的聚合行确定性生成，不由模型决定轴值。

数据版本与 fingerprint 同时写入 `data_meta`、gold 与 run 结果；可区分“版本名相同但内容不同”的本地数据库。

关键文件：

- `data/enterprise_seed.py`：固定 seed、schema、索引、迟到入库与缺失归因样本、质量扫描。
- `docs/enterprise-data-contract.md`：表粒度、字段、PII decoy 和质量解释。
- `insightpilot/metrics.py`、`insightpilot/sql.py`：版本化 campaign 指标和安全模板。
- `insightpilot/verification.py`：带权 campaign 独立分组核对；类别/地区/月逐组复核。
- `evals/enterprise-blind-v1.jsonl`：holdout 问题。
- `evals/enterprise-gold-v1.jsonl`：独立 Python gold；不注入模型提示。
- `insightpilot/enterprise_evaluate.py`：主策略与两个消融运行。
- `reports/enterprise-evaluation.md`：样本边界、结果和复现命令。

## 设计取舍

- 用并行 enterprise fixture，不迁移/改写 S0 数据，避免旧 gold 被扩展数据悄悄改变。
- 只加实用分析面：地区、月份和多触点 campaign 归因。源数据中 campaign 权重可重叠；贡献值按权重分摊，而非重复计全额。
- 有意保留约 1% 缺失 attribution 和晚到记录。缺失计入“未归因”；晚到先报告数量。历史 as-of 指标需先确定入库时刻语义和可见性快照，当前不假装支持。
- SQL 报告金额仍为整数分；带权 campaign 分摊允许小数分。gold 使用 1e-6 容差。
- VM budget 在 enterprise 版本乘 20，最高 5,000,000 steps；仍由 2 秒 query timeout 控制。S0 保持 250,000 steps。

## 两条失败路径

1. campaign 指标在 S0 查询 → data version guard 返回 dataset clarify，不编译 SQL。
2. 模型给 event amount 选择 category 或任意 SQL 指令 → metric dimension contract/AST 拦截，不会连接客户个人字段。
3. 加权归因重复/漏金额 → 独立 oracle 对照每个 campaign 金额、全体总额与 share sum；不通过则不返回回答。

## 复现与真实结果

```text
.\.venv\Scripts\python.exe -m data.enterprise_seed
.\.venv\Scripts\python.exe -m data.build_enterprise_gold
.\.venv\Scripts\python.exe -m insightpilot.enterprise_evaluate --database data/insightpilot-enterprise.sqlite --output reports
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

实际基准含 50,000 单、10,454 退款、60,429 touch。质量扫描发现 9,757 笔退款在事件超过 1 天后入库，498 单缺少归因；其余所列引用/权重/超额退款检查为 0。归因覆盖率 99.004%。

盲测主臂 3/3 全面正确；无语义检索 1/3；无独立复核 2/3 数值正确，复核通过自然为 0/3。主臂 p50 4.14 秒、p95 9.48 秒，3 条回答平均 1,138.33 tokens。查询服务单独在 50k 上一次 21 组归因运行 519 ms。单模型、n=3、单机计时，不代表 SLA/生产效果。

## 亲手实验（约 18 分钟）

1. 重建 enterprise 数据，比较 `data_meta.data_version` 与 `reports/enterprise-data-quality.json`（4 分钟）。
2. 对一笔两触点订单求权重和，手算批准退款拆分金额（4 分钟）。
3. 运行 B03，只读原始 SQL、21 行结果和独立 verifier control（4 分钟）。
4. 对比 B01–B03 无检索和无复核原始记录，判断失效点是 metric、维度、数值还是 verification（4 分钟）。
5. 读 prompt-blind 问题，再单独查看 gold 文件；确认 gold 没进入 planner payload（2 分钟）。

## 面试追问

**问：为什么归因后金额会出现小数分？**

答：多触点按浮点权重拆分金额，单 campaign 部分可能小于一分。原订单金额仍以整数分存储；汇总贡献留精度并给容差，展示端可选择按规则舍入。

**问：迟到退款为何不直接改归属日期？**

答：事件时间与记录可见时间是两个概念。趋势分析需决定使用事件时间还是当时可见快照；当前只报告延迟分布，没有声称历史 as-of 回放。

**问：消融能证明检索/复核有效吗？**

答：不能证明普遍因果。n=3 只暴露小样本中退化：主 3/3、无检索 1/3、无复核数值 2/3。要做可靠结论需扩充冻结 holdout 和多次模型种子。

## 停止点

S7 完成。进入系统性并发/故障评测前，固定上述 holdout，不用当前 gold 反复调参。
