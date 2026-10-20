# S9 学习记录：冻结集策略实验

## 问题

检索消融显示语义检索有价值，但这本身不能证明 Agentic-RL。用 S8 的 gold 奖励做一轮可复现的离线策略对照，并锁定最后一道 B03 为 holdout。

## 方法与结果

`strategy_experiment.py` 从成对的主臂和无检索臂估计终局奖励均值。B01、B02 作为训练样本；按训练均值选择“语义检索”或“无检索”。B03 只用于冻结评估。

当前训练均值为检索 3.0、无检索 2.25。策略选检索。B03 上 learned policy 与固定检索基线均为 3.0，增益 0.0；测试集只有 1 题。因此报告明确标记 `agentic_rl_claim_supported=false`。当前策略不替换线上策略。

## 边界

这是配对离线终局奖励的单步动作值实验，不含多步环境交互、信用分配、策略更新循环或有统计把握的 holdout 增益。它是策略实验，不是 Agentic-RL 证据。重新生成报告：`.venv/Scripts/python -m insightpilot.strategy_experiment`。
