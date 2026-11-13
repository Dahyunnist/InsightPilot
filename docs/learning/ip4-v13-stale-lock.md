# IP4 v13 过期锁回归

预注册锁的 agent/runtime 哈希与当前文件不一致。模型 runner 在供应商余额检查前验证文件哈希，旧锁必须 fail closed。预注册脚本会请求供应商余额，因此本回归不运行该脚本，也不刷新锁。

测试只比较本地 SHA 与源码顺序。冻结题集、gold、状态 `blocked_by_review`、provider receipt 未知和实际费用 `null` 均保持原样。没有模型请求或网络访问。IP1 RLS 真实数据库验证仍待用户执行 `scripts/run_ip1_rls.ps1`。

离线报告契约还断言该状态不可标为 `completed`，供应商接收与费用不得写成 `false`/`0`。runner 在冻结哈希不符时必须在请求前停止。真实逐题报告仍不存在，模型指标未完成。
