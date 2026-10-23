# IP-4 v10：题面与 gold 日期对齐

## v9 有效性复核

v9 已发出 10 次 provider 请求，usage 峰价估值 `$0.0093684`。事后逐题核对发现 7 道题面请求 11 月窗口，但 v5 gold builder 按 case ID 生成 10 月 gold。X04/X05 的题面为 11 月 15–21 日，gold 却是 10 月 15–21 日。v9 严格分数失效，不作为能力结果；其 blind/gold 不改写、不重跑，费用照记。详情见 `reports/enterprise-v9-validity-review.json`。

## v10 冻结

v10 使用新 seed `20261025`、新数据库、重新编写题面。所有答题题面的日期与 case 映射的独立 gold 窗口相符。Python 源行 oracle 生成 gold。blind/gold/manifest 哈希锁定，历史精确题面重叠 0；语义近似检查仍有限。RulePlanner 先行基线 8/20：可答 0/11、guard 8/9、错误拒答 11、误接受 0。v10 模型评测尚未运行。

## 预算锁

授权 cap `$5`，v9 实际 usage 已扣 `$0.0093684`，当前余 `$4.68622135`。v10 每题最多 2 次、每请求最多 2,000 输入与 1,024 输出 tokens，最多 40 次请求。DeepSeek Flash 峰价上界 `$0.073152`；预留后余 `$4.61306935`。预登记锁模型和执行文件哈希，逐次按 provider usage 记费；缺 usage 按单次上界收费。不得超 40 次调用。

Compose 仍未验证。下一轮可用本机 PostgreSQL 镜像或主机 PostgreSQL；不得重跑 v6、v7、v8 或旧 IP4。
