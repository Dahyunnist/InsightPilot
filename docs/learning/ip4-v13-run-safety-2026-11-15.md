# IP-4 r5 后续运行说明

当前余额未知。现在不要执行以下命令。执行前确认供应商余额可用，且按本次预注册峰值预留后全局账本仍不超过 `$5`；不得超过 88 个请求或 `$0.1873344` 总批准预算。余额检查失败、余额不足、hash 不匹配或 cap 不足时立即停止，不换通道。

在项目 PowerShell 根目录执行。脚本从 `.env` /进程环境读取凭据，不输出密钥；预检通过才创建唯一 run lock，dry-run 不发 completion 请求：

```powershell
$runId = (Get-Date -Format 'yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$prereg = "reports/ip4-v13-model-preregistration-$runId.json"
$report = "reports/ip4-v13-model-evaluation-$runId.json"

.\.venv\Scripts\python.exe scripts\preregister_ip4_v13_model.py --revision $runId
if ($LASTEXITCODE -ne 0) { throw '余额或预算预检失败；停止，未调用模型。' }

.\.venv\Scripts\python.exe scripts\run_ip4_v13_model_blind.py --preregistration $prereg --run-id $runId --report $report --dry-run
if ($LASTEXITCODE -ne 0) { throw 'dry-run 预检失败；停止，未调用模型。' }

# 仅确认 dry-run 显示唯一 run_id、22 题、请求上限和预算正确后，才执行：
.\.venv\Scripts\python.exe scripts\run_ip4_v13_model_blind.py --preregistration $prereg --run-id $runId --report $report
if ($LASTEXITCODE -ne 0) { throw '评测失败；保留逐题报告并核账。' }
```

每次评测使用唯一 run ID 与独立报告路径；已存在路径会 fail closed。payload 限定冻结 synthetic question、静态候选指标及 allowed metric IDs；不得发送 gold、SQL、查询结果、数据库行或私人数据。已有 r5 报告不覆盖，当前余额未核实前不得运行。
