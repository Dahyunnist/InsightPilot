# IP-4 r5 单轮 transport 阻断（2026-11-14）

r5 使用用户提供的脱敏 DeepSeek 余额收据，采样时间 `2026-11-14T02:09:00Z`，余额 9.24 CNY。至锁定时收据未超过 5 分钟。峰值预留为 `$0.0936672`，项目上限 `$5.00`；dry-run 通过。旧锁未修改，r5 单独绑定当前 scorer、planner、runner、数据、题目和 gold 哈希。题目被标记为 `development_only`，`is_blind=false`。

单轮最多请求 22 次，实际只尝试 15 次。15 次 planning 请求都返回 `URLError`，无 provider response、无 usage。没有重试，没有发送 gold；Agent payload 只包含当前题目。可答题 0/14 产出。8 道 guard 中 7 道由确定性 preflight 拦截，不能算模型成绩。

`$0.031932` 是 15 次请求的峰价最坏估算，不是确认费用；provider receipt 未知。账本保留 r5 预算预留和 `$5.00` cap，剩余额度按预留计算为 `$4.54499575`。报告状态为 `partial_transport_blocked`。失败与 `.env` 配置的模型 API egress 连接失败相符；余额 GET 是用户从受限联网宿主单独完成，不能证明模型 completion egress 可用。未重发任何题目。

报告：`reports/ip4-v13-model-preregistration-20261114-r5.json`、`reports/ip4-v13-gold-seal-20261114-r5.json`、`reports/ip4-v13-model-evaluation-r5.json`。定向回归：`.venv\Scripts\python.exe -m unittest tests.test_ip4_v13_offline_audit tests.test_ip4_v13_holdout tests.test_ip4_v13_stale_lock_fail_closed tests.test_learning_docs_repo_relative`。全套测试在受限环境超过 60 秒无进度后中断，不记为通过。
