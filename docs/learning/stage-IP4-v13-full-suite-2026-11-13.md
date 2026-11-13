# IP-4 v13 全量测试与旧锁失败关闭

## 本轮变更

- 全量 `unittest`：148 项通过。
- 修正两条多域 `QueryPlan` 断言，补齐实际返回的指标语义版本 `1.0.0`。
- v13 预注册文件保持不可变。其 `agent_runtime_sha256` 与当前 `insightpilot/agent.py` 不一致，因此旧模型评测锁仍应失败关闭。
- 测试现在明确验证“预注册未改、运行时锁已过期”，不把旧锁伪装为有效授权。

## 重要边界

本次没有调用模型。全量测试通过不代表 22 题真实模型评测已执行。用户已授权对冻结的 22 道合成题做真实评测，但旧锁绑定的运行时代码哈希已失效。不得修改旧预注册文件、题目或 gold 来绕过。需要先为当前运行时代码建立新的、独立冻结且可审计的运行锁，并在调用前校验总预算不超过 `$5.00`；若系统无法在保留原冻结记录的前提下完成，报告阻塞，不调用。

## 2026-11-13 单轮预检与审批阻断

旧预注册和 gold seal 保持原样。新版本锁 `reports/ip4-v13-model-preregistration-20261113.json` 绑定提交 `e298a0408bc1bfd95ef011d7d5be524805cf4c27`，锁与 seal SHA-256 分别为 `7ff7d2d451402e047d9ac535022a582021bb0860a99e99100fb5764f3b8f303c`、`4985e61498cc5be1415fadb396eeb528075d1759aac695d4176dd20046b4abc2`。冻结问题、gold、manifest SHA 未变，分别为 `c7c8ecdd498cdaae44e7744457b54a2ade850c47e0340f5d3acb66c4f33bfa66`、`f10959a4be664a69f39d6ff3079b6d3d93705e310e09be5abdc6c2b0fe335df2`、`c7b7875dcf01380fc6f9da32c7ad8593f9f19f5ca5fc38f32a3521236e349932`。

固定模型参数为 `deepseek-v4-flash`、temperature 0、每题一次且无重试、最多 22 次请求。按峰价每次 `$0.0021288`，当前轮预留 `$0.0468336`；历史请求 receipt 未知，另保留同额历史峰价预算。总预留 `$0.0936672`，此前 `$5.00` 总 cap 下未预留余额 `$4.53009415`。DeepSeek 余额 API 多次实时确认可用；最后查询 `2026-11-13T12:22:45Z` 为 `9.30 CNY`，门禁要求 `$0.702504` 等值储备，满足。

冻结集测试 5 项及旧锁失败关闭测试 2 项通过；干跑门禁通过，确认模型请求数为 0、22 题哈希匹配、gold 不进入 planner 输入。随后唯一一次模型运行命令在进程启动前被自动审核拒绝：审核认为当前可信消息未直接授权把这批题目发送到外部模型服务。因此本轮 DeepSeek 模型请求数为 0，服务端未收到本轮请求；拒绝记录见 `reports/ip4-v13-attempt-manifest-2026-11-13.json`。先前历史尝试仍是 receipt 未知，未改写为零调用。不得绕过审核；需用户在当前线程直接确认向 DeepSeek 发送这 22 道冻结合成题，确认后才能执行单轮。

审核拒绝后，未重试模型请求。随后生成版本化运行锁 r2，保留 r1、原冻结记录与预算账本；锁 SHA-256 为 `4f4a4e405fe087b3fbdd5f3b9bb9215d8b77e8bd33ac1897b9cab986b572fe23`，seal SHA-256 为 `db48c2607fba0bff7270f6d9471bb19c340f2211ab83e0b69f38905eeeb1a14b`。r2 干跑再次通过，仍未发送模型请求。待当前线程直接授权后，可运行：

```powershell
.\.venv\Scripts\python.exe scripts/run_ip4_v13_model_blind.py --preregistration reports/ip4-v13-model-preregistration-20261113-r2.json --report reports/ip4-v13-model-evaluation-report.json
```

## 复现

在 Windows 本机受信任执行环境运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

结果：`Ran 148 tests ... OK`。受限执行环境会在 Starlette `TestClient` 初始化时因 Windows asyncio socketpair 权限限制卡住；本轮通过已授权的本机执行完成全量套件。
