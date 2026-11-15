# IP-4 v13 deterministic 离线对照（2026-11-15）

## 冻结身份与隔离

复核 22 题冻结集：退款 12、营销 ROI 10；数据 fingerprint `9171b5a859f09882038c38516e725122383f4e252e773a302324ba1b016da0e9`。`evals/ip4-v13-manifest.json`、blind、gold SHA-256 分别为：

- Manifest：`c7b7875dcf01380fc6f9da32c7ad8593f9f19f5ca5fc38f32a3521236e349932`
- Blind：`c7c8ecdd498cdaae44e7744457b54a2ade850c47e0340f5d3acb66c4f33bfa66`
- Gold：`f10959a4be664a69f39d6ff3079b6d3d93705e310e09be5abdc6c2b0fe335df2`

三项哈希与 v13 manifest、r5 锁及离线 bundle provenance 相符。gold 仅在本机用于评分；报告只写哈希、case ID、失败检查名与状态，不含题面、gold 值、SQL、参数或结果行。

## 对照结果

| 本地 deterministic arm | 总分 | 可答题 | guard | 误拒 | 误接受 |
| --- | ---: | ---: | ---: | --- | --- |
| RulePlanner + independent verifier | 15/22 | 8/14 | 7/8 | R02、R05、R06、R07 | M09 |
| RulePlanner，无 independent verifier | 15/22 | 8/14 | 7/8 | R02、R05、R06、R07 | M09 |

两臂共同失败 7 题。R02、R05、R06、R07 为可答题被澄清；R04、M01 为维度与数值不匹配；M09 是应澄清题被回答。关闭 verifier 臂按定义不把 `sql_verified=false` 算作失败，分数规则也排除 verifier 成功项。因此相同总分不证明 verifier 等效。独立 verifier 的执行记录与结果仍在报告中标注。

检索候选覆盖为 14/14 可答题。此数字只衡量 metric candidate recall，不是回答准确率。

## 解释边界

这是固定 synthetic SQLite 数据上的 RulePlanner 与本地 verifier 策略对照，不是 LLM 评测。真实模型调用 0、请求 0、费用 0；本轮没有网络、PostgreSQL 或供应商余额请求。结果不能代表生产准确率或总体泛化，也不能替代 v13 模型评测审批。

题目级分类与哈希见 `reports/ip4-v13-deterministic-comparison-2026-11-15.json`。复现命令：

```powershell
.\.venv\Scripts\python.exe scripts\run_ip4_v13_deterministic_comparison.py --database data/insightpilot-ip4-v13.sqlite --output reports/ip4-v13-deterministic-comparison-2026-11-15.json
```

定向回归：`tests.test_ip4_v13_deterministic_comparison`、`tests.test_ip4_offline_bundle` 共 3 项通过。测试确认冻结哈希、零模型调用、失败分类和报告不泄漏题面/gold/SQL/参数/行数据。
