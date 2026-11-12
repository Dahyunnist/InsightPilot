# IP4 v13 尝试状态

尝试因 `IncompleteRead` 未能确认结束状态，故记为 `attempt_incomplete_unknown`。不得改成 `completed`，也不得推断零调用或零费用；实际费用为 `null`。

Manifest 只保留冻结集、gold、scorer、预算和运行时标识，不含密钥、原始 prompt、全文响应或日志。该状态不构成模型评测结果。
