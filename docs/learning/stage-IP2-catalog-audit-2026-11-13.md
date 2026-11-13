# IP-2：指标目录与开发边界审计

## 结果

审计覆盖 11 个指标的定义版本、生效日、IANA 时区、定义来源、来源表、字段血缘、freshness 和适用数据版本。目录升至 `insightpilot-semantic-catalog-v3`。修正退款金额率缺少订单实付金额血缘、扩展指标来源不精确、退款指标未声明数据版本等问题。当前 freshness 明确为固定数据契约，不承诺生产实时性。

数据契约不匹配时，服务在查询前澄清。退款目录指标绑定退款数据版本；库存周转率仅绑定 `insightpilot-inventory-v1`。库存 fixture 指纹现在同时覆盖基础 seed 与库存扩展，重复构建得到同一指纹。

库存 Python 独立账本曾漏掉“区间内观察到 NULL 快照、但没有有效库存日和 COGS”的品类行；SQL 会保留该分组。oracle 已对齐 SQL，保留该组并输出空分母、空比率。

## 冻结样本与边界

`reports/ip2-boundary-development-manifest-v1.json` 汇总三个合成开发来源：退款固定案例、营销 ROI 开发集和新增库存周转开发集。所有来源均标记 `development_only`，总清单标记 `is_blind=false`、`model_calls=0`；不得把它们当盲测或模型泛化证据。

库存题集位于 `evals/ip2-inventory-turnover-dev-v1/`，共 3 题。manifest 冻结数据版本 `insightpilot-inventory-v1`、指纹 `4ec11a55a1acfa7269760bcdeb0f09c9a6c691aed497cae28396cc4129cf2e3d`、题集 SHA-256 `0103046cd36655aba863506e0619063474bff517a81bc06491b20fe708d6a018`、gold SHA-256 `9c0210d0495f68d9774c43d7cb00fe21eecee33abdfdd2df413217c06a0375d2`。覆盖上海本地日期、UTC 16:00 午夜边界、左闭右开、NULL 快照、日库存先聚合、零分母。gold 由独立 Python 账本生成，并逐题与编译后的只读 SQL 对照。

跨域清单还引用退款部分退款、UTC 边界、NULL 分母、cohort/事件日差异和歧义澄清；营销 ROI 开发样本覆盖重复业务事件、revision/void、UTC 月界、退款撤销快照及零费用 NULL ROI。目录审计探测“退款率”歧义，确认在零模型调用下先澄清。

## 复现与验收

从仓库根目录执行：

```powershell
.venv\Scripts\python.exe -m data.build_ip2_inventory_dev
.venv\Scripts\python.exe -m scripts.audit_ip2_catalog
.venv\Scripts\python.exe -m unittest tests.test_metric_versions tests.test_ip2_inventory_turnover tests.test_ip2_catalog_audit tests.test_s5_semantics tests.test_multidomain
.venv\Scripts\python.exe -m unittest discover -s tests
git diff --check
```

两个生成器不调用模型；审计输出 `reports/ip2-metric-catalog-audit-v1.json`，记录 11 个指标、3 个开发来源、0 次模型调用。定向测试 38 项通过；全套测试 181 项通过。构建与测试未读取、写入或暂存 `.tmp/`。

## 限制

库存域当前只在合成 SQLite fixture 验证。开发题集很小且非盲，证明的是口径和代码路径回归，不是模型准确率或真实业务适用性。IP-4 仍阻塞：DeepSeek live balance endpoint 尚未成功返回余额；不得用模型评测声称 IP-4 完成。
