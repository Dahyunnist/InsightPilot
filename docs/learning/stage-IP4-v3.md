# IP-4 v3：扩展独立跨域 gold

## 为什么扩充

v2 只有 4 题（2 道可答、2 道澄清）。它适合验证构建链与基本语义，不足以覆盖多个退款指标、分组、时间窗和 guard 类型。v2 文件保持不变；v3 使用全新题面并检查历史精确重叠。

## 冻结范围

v3 共 24 题：退款 12 题、营销 ROI 12 题；16 道可答题和 8 道 guard 题。guard 覆盖口径澄清、缺少日期、多指标、地区 ROI、个人信息、提示注入和写操作。数据仍为同一个固定合成数据版本与 seed，不代表真实客户分布。

可答题 gold 值取自只读编译 SQL 的实际结果；退款结果再与独立 Python 源行账本核对，营销结果再与两个独立 Python 事件账本核对。SQL 与 Python 只要有差异，构建就失败。所有 gold 均在模型外生成，模型输出不参与计算。blind 不含 gold 字段。构建器会扫描历史 eval/report JSONL 并拒绝精确规范化题面重叠。

冻结文件：`evals/ip4-v3-blind.jsonl`、`evals/ip4-v3-gold.jsonl`、`evals/ip4-v3-manifest.json`。blind SHA-256 为 `a68ea998603ca8c1609f65d2cabb901d4526657aa0a6d60fe87c95b31f082647`；gold SHA-256 为 `2c295634e27a5cad9762eb13b91dfcd2e28d92926d5f88c0da0c98c604e4fc15`；历史精确题面重叠为 0。

## 验证与限制

`tests/test_ip4_v3_holdout.py` 检查 24 题规模、域分布、guard/answer 状态、重复构建稳定性和 manifest 哈希；固定 RulePlanner 另核对 guard 标签。构建时 SQL 与独立 Python oracle 已逐题交叉核对。未运行模型，不报告模型分数；24 题和单一合成 seed 仍不足以估计生产准确率或总体泛化。v3 使用与 v2 相同的合成数据 fingerprint 和 seed；独立性指新问题和独立 gold 计算，不是独立抽样数据集。精确文本检查不排除语义相似。

IP4 v3 定向测试和 v2 回归在 Python 3.12.15 容器 tmpfs 下通过；全部测试 117/117 通过。环境、命令、耗时、宿主 Python 3.14 对照和失败尝试见 `reports/ip4-v3-verification.json`。容器网络关闭，未传 `.env`，只设置 dummy 环境变量；未调用真实模型或外部 API。
