# IP-4 离线失败诊断增量（2026-11-15）

## 路线复核与选择

对照 2026-10-23 的阶段验收路线和当前 IP-1～IP-4 记录：IP-1 的租户隔离、只读和超时已有真实 PostgreSQL 报告；IP-2 的第二业务域有独立 Python oracle 与 SQL 交叉核对，但仍限于合成数据；IP-3 的 HTTP/MCP 生命周期已有离线与宿主证据，供应商 SDK 在途取消、生产 Collector 和持续负载仍需外部环境；IP-4 v14 仍待所有者审阅，模型评测不能在本轮执行。

选择 IP-4 的离线失败归因作为本轮缺口。历史记录指出 R01/R02 只保存 query 阶段，未保留可复核的脱敏错误分类；旧报告不能据现有代码补写。改进未来评分器的逐题输出，让之后的失败至少保留阶段、稳定 reason code 和 allowlist failure detail。

## 数据流与边界

分析结果 → IP-4 scorer → 严格分数 + `actual_stage` + `actual_reason` + `actual_failure_detail`。

诊断只允许稳定字段：layer、category、exception class、backend、tenant-context 状态、SQL SHA-256 和参数数量。SQL、绑定参数、tenant ID、错误正文、问题与答案行都不会进入该诊断对象。自由文本 reason/stage 会被丢弃。

## 关键实现

- `insightpilot/ip4_evaluate.py`：清洗诊断字段；IP-4 v13/v14 共用该 scorer。
- `insightpilot/enterprise_evaluate.py`：把同一诊断接入 v13 模型 runner 实际使用的比较器。
- `tests/test_ip4_failure_diagnostics.py`：覆盖安全字段保留、敏感值剔除和非法字段拒绝。
- 历史盲测、冻结题、gold、manifest 与报告均未重写。

## 验证

运行：

```text
.venv/Scripts/python.exe -m pytest -p no:cacheprovider tests/test_ip4_failure_diagnostics.py tests/test_ip4_v14_evaluate.py tests/test_ip4_v13_deterministic_comparison.py tests/test_ip4_v13_blind_payload_contract.py -q
```

结果：`10 passed, 22 subtests passed`。本阶段没有模型请求、网络访问、Docker/PostgreSQL 实测，也未运行冻结 22 题评测。测试结果只证明 scorer 字段投影、既有离线比较器和 SQLite/test-double 契约；不能当作真实 PostgreSQL 或模型效果证据。

## 失败路径

1. 底层异常 detail 缺失或字段不在 allowlist：诊断为空或只保留可识别字段，评分仍照常进行。
2. reason/stage 是自由文本或 SQL：正则拒绝该值，不复制到报告。

## 手动实验

1. 在测试输入的 `failure_detail` 中加入 `sql`、`parameters` 和 `tenant_id`，运行诊断测试，确认输出不含这些键或值。
2. 给 `sql_sha256` 传入 64 位小写十六进制，观察哈希被保留；换成原始 SQL 后，字段应被丢弃。
3. 检查 IP-4 模型请求是否为零；本轮没有执行任何评测命令。

## 面试追问

**问：失败报告为什么只存 SQL 哈希，不存 SQL？**

答：哈希可用于同形查询归并和定位代码路径；原 SQL 与参数可能含业务信息，不应进入此诊断报告。

**问：这些字段能解释旧 R01/R02 吗？**

答：不能。旧运行没有保存这些字段；新代码只能改善后续报告，不能重建历史异常。

**问：测试通过是否证明 PostgreSQL 或模型行为正确？**

答：不证明。本轮只测试纯函数和现有离线 SQLite/test-double 路径；真实 PostgreSQL 与供应商模型仍须各自实测。

## 待验证项

未来 IP-4 运行会生成可归因的安全诊断；其实际诊断分布仍需新获准评测。v14 候选仍待所有者审阅和预算/实时余额预检。IP-3 真实供应商请求取消及生产级运行验证也未完成。
