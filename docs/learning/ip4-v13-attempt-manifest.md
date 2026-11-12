# IP4 v13 尝试状态

真实评测状态为 `blocked_by_review`。另一次执行中断后供应商是否收到请求未知，故 `provider_received` 与实际费用均为 `null`。不得改成 `completed`，不得声称零调用或零成本；不重试、不换路由、不拆题。

Manifest 只保留冻结集、gold、scorer、预算和运行时标识，不含密钥、原始 prompt、全文响应或日志。冻结集及 hash 不变；没有模型成绩。用户如在本机获准手动评测，运行 `python scripts/run_ip4_v13_model_blind.py`，须留存逐题请求/响应状态、供应商 usage、延迟、失败和可核实际费用；不得把未知费用记为零。
