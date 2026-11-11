# IP-4 v3：扩展独立跨域 gold

## 为什么扩充

v2 只有 4 题（2 道可答、2 道澄清）。它适合验证构建链与基本语义，不足以覆盖多个退款指标、分组、时间窗和 guard 类型。v2 文件保持不变；v3 使用全新题面并检查历史精确重叠。

## 冻结范围

v3 共 24 题：退款 12 题、营销 ROI 12 题；16 道可答题和 8 道 guard 题。guard 覆盖口径澄清、缺少日期、多指标、地区 ROI、个人信息、提示注入和写操作。数据仍为同一个固定合成数据版本与 seed，不代表真实客户分布。

可答题 gold 值取自只读编译 SQL 的实际结果；退款结果再与独立 Python 源行账本核对，营销结果再与两个独立 Python 事件账本核对。SQL 与 Python 只要有差异，构建就失败。所有 gold 均在模型外生成，模型输出不参与计算。blind 不含 gold 字段。构建器会扫描历史 eval/report JSONL 并拒绝精确规范化题面重叠。

冻结文件：`evals/ip4-v3-blind.jsonl`、`evals/ip4-v3-gold.jsonl`、`evals/ip4-v3-manifest.json`。blind SHA-256 为 `a68ea998603ca8c1609f65d2cabb901d4526657aa0a6d60fe87c95b31f082647`；gold SHA-256 为 `2c295634e27a5cad9762eb13b91dfcd2e28d92926d5f88c0da0c98c604e4fc15`；历史精确题面重叠为 0。

## 验证与限制

`tests/test_ip4_v3_holdout.py` 检查 24 题规模、域分布、guard/answer 状态、重复构建稳定性和 manifest 哈希；固定 RulePlanner 另核对 guard 标签。构建时 SQL 与独立 Python oracle 已逐题交叉核对。RulePlanner 基线为 19/24（严格准确率 79.17%），可答题 11/16，4 道拒绝题 4/4；5 个失败 ID 为 R03、R05、R06、R08、M01，详情见 `reports/ip4-v3-rule-baseline.json`。未运行模型，不报告模型分数；24 题和单一合成 seed 仍不足以估计生产准确率或总体泛化。v3 使用与 v2 相同的合成数据 fingerprint 和 seed；独立性指新问题和独立 gold 计算，不是独立抽样数据集。精确文本检查不排除语义相似。

IP4 v3 定向测试和 v2 回归在 Python 3.12.15 容器 tmpfs 下通过；全部测试 117/117 通过。环境、命令、耗时、宿主 Python 3.14 对照和失败尝试见 `reports/ip4-v3-verification.json`。容器网络关闭，未传 `.env`，只设置 dummy 环境变量；未调用真实模型或外部 API。

## 双后端离线基线与模型预注册（2026-11-11）

`scripts/evaluate_ip4_v3_all_backends.py` 使用同一 v3 blind/gold 和原有 `ip4_evaluate.compare`，在 SQLite、PostgreSQL tenant-a、tenant-b 各跑 24 题。三臂均为 19/24；退款 8/12、营销 ROI 11/12，失败均为 R03、R05、R06、R08、M01。RulePlanner 0 模型调用。完整逐题轨迹见 `reports/ip4-v3-offline-all-backends.json`。

真实模型前账本累计峰价费用为 `$0.33817825`，`$5.00` 上限内剩余 `$4.66182175`。按 24 题 × 3 后端臂 × 每题最多 2 次请求、每请求输入 3,000 tokens、输出 1,024 tokens、DeepSeek Flash 峰价 `$0.30/$1.20` 每百万 tokens，最坏费用预留 `$0.3065472`，预留后全局余额 `$4.35527455`。prompt sizing 用生产 planner 的精确请求构造器和 stub 响应测量；18 题会到达 planner，最大 prompt 2,541 UTF-8 字节，锁为 2,800 字节。供应商余额只读预检返回可用，CNY 余额 ¥9.44；按 ¥7.50/USD 的保守换算，最坏费用约 ¥2.30。

模型、旧别名路由说明、prompt 上限、调用上限、盲集/gold/manifest、planner/scorer/runner 与离线基线 SHA-256 已冻结在 `reports/ip4-v3-model-preregistration.json`。preflight 验证通过，发送 completion 请求数为 0。提交该锁后才运行一次真实模型评测；不修改题面、gold、planner prompt 或 scorer。
