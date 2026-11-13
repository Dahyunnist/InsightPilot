# IP4 v13 过期锁回归

预注册锁的 agent/runtime 哈希与当前文件不一致。模型 runner 在供应商余额检查前验证文件哈希，旧锁必须 fail closed。预注册脚本会请求供应商余额，因此本回归不运行该脚本，也不刷新锁。

测试只比较本地 SHA 与源码顺序。冻结题集、gold、状态 `blocked_by_review`、provider receipt 未知和实际费用 `null` 均保持原样。没有模型请求或网络访问。IP1 RLS 真实数据库验证仍待用户执行 `scripts/run_ip1_rls.ps1`。
