# IP-4 v14 专用模型 runner（2026-11-16）

## 预登记

新增当前锁 `reports/ip4-v14-model-preregistration-v5.json`，状态为 `pending_owner_authorization`，`external_calls_authorized=false`。v4 绑定现有仓库外 v14 合成候选的 manifest、blind、gold、SQLite 原始 SHA-256，以及数据版本和 fingerprint；还绑定 planner、提示词、scorer、离线校验器、runner 核心、egress worker、依赖清单、Python/平台/已安装依赖清单摘要、模型 ID 和 provider endpoint 摘要。Gold 只参与字节哈希，不解析。

CLI 默认 dry-run；本次真实候选 dry-run 为 22 题、0 模型调用、0 provider 请求。v5 preregistration SHA-256：`0cc47207e9aa7928db0918628f05c88dca1f0754cd575fb059eeea7e8e6630bb`。要发送请求，必须明确传 `--execute`，并提供独立 owner authorization、5 分钟内的预算快照、外部 reservation state 目录和新输出路径。

## 外部调用门

owner authorization 必须是严格 JSON，绑定 preregistration 文件 SHA-256、模型 ID、provider endpoint SHA-256、22 次请求、`$0.0468336` 额度、同额完整预留和 `retry_policy=none`。记录还须明确确认候选哈希、blind/gold、提示词/模型、parity 证据与预算均已审阅。runner 不创建授权记录；本版尚无授权记录。

预算快照字段：`schema_version=1`、`project_cap_usd`、`spent_usd`、`external_reserved_usd`、`as_of_utc`。快照时间和文件修改时间都须在 5 分钟内；`external_reserved_usd` 须含其他项目预留，不含 runner 的 v14 专用 reservation journal。预检要求已花费、外部预留、v14 历史预留与本次 `$0.0468336` 总和不超项目上限。runner 不访问账单 API。

全额预留在首个请求前原子写入外部 state 目录；失败、超时或中断都不自动释放。相同 preregistration 只能执行一次，避免重复计费。超时即停，不重试；输出只保存 case ID、获选指标 ID、状态、延迟和 token 计数，不保存问题、原始响应、API key 或 endpoint。实际账单成本标为未确认。

每条模型 user payload 只含 `question` 与 `allowed_metric_ids`；provider 协议层另带模型名、JSON 格式和固定 64 completion-token 上限。响应必须只含 `selected_metric_ids`，且每个 ID 都在允许列表内。

## 回归与边界

`tests/test_ip4_v14_model_runner.py` 使用内存模拟 HTTP，覆盖错误哈希、意外字段、预算不足、超时无重试、输出脱敏、全额预留、幂等拒绝和禁止重定向；没有真实模型请求。与验证器及 opt-in PostgreSQL 模块合跑：`13 passed, 1 skipped`；集成测试因未设置 opt-in 跳过。

首次预登记时未创建 owner authorization，也未调用模型、Docker、PostgreSQL 或账单服务；当时候选状态为待所有者审阅。

## 2026-11-16 预算保守对账与本轮授权

账本截至 11 月 14 日的项目剩余 cap 为 `$4.50483355`，用户补充的账号级供应商账单合计为 `¥10.91277486`，期间为 10 月 19 日至 11 月 15 日。账号总额不归属 IP-4；为保守核算，按约 `6.6709 CNY/USD` 换算并向上取整为 `$1.64`，全额叠加到项目账本。扣除后余额 `$2.86483355`；预留用户授权的 22 题 `$0.187` 后为 `$2.67783355`；再计 runner 锁定的 `$0.0468336` 后为 `$2.63099995`。cap `$5.00` 未超。

用户明确授权在预算门禁通过后运行这 22 道冻结合成题。授权基于当前 v14 preregistration 哈希；模型请求只传合成问题和允许指标 ID，不传 gold、SQL、查询结果、密钥或私人数据。账本只记录该账单合计，不读取或保存原始 CSV 或 key 字段；全账号账单可能与既有项目费用重叠，因此采用全额额外扣减。运行前仍须通过 runner 的新鲜快照与一次性预留门禁。

复跑发现固定离线锁的四个 source hash 已过期：metric catalog、scorer、query engine、offline runner module。只更新离线锁和其固定 SHA；v14 preregistration 与 22 题 manifest/blind/gold/database SHA 保持不变。此前失败样本、旧/新 hash 与修复结果记于 `reports/ip4-v14-offline-lock-repair-2026-11-16.json`。

修复后离线与模型 runner 测试 `20 passed`。冻结 22 题 RulePlanner 基线通过 `8/22`（marketing ROI `5/11`，refunds `3/11`）；网络调用和模型调用均为 0。v14 模型 runner dry-run 再次通过，仍为 0 请求。真实模型评测继续等待实时 provider balance GET 成功与新鲜预算快照；账单 CSV 仅能做保守 cap 对账，不能证明 provider 当前余额。

只读余额门禁命令：

```powershell
.\.venv\Scripts\python.exe scripts\check_ip4_provider_balance.py --reserve-usd 0.187
```

该命令从 `.env` 读取配置，只向匹配锁定 endpoint 发 `/user/balance` GET；不发模型请求，不保存响应，不打印 API key。请回传 `status=passed`、币种、可用额、预留额和 UTC 时间；门禁通过后再继续真实评测。
