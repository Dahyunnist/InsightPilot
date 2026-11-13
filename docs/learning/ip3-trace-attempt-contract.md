# IP3 Trace 与 IP4 Attempt 契约

JSONL trace 固定记录事件、run/trace/span ID、阶段、状态与允许的失败类别；不含问题、SQL、租户或 token。I/O 失败返回 false，不中断分析。IP3 fake planner 已覆盖超时后取消、RunStore 终态、幂等重放和后续请求恢复；这不验证供应商 SDK 的底层取消。

IP4 attempt manifest 是评测状态，不是 trace event。当前保持 `blocked_by_review`，provider receipt 与 cost 为 null。测试确保不能把未知 receipt/费用伪装成 completed 或零成本。
