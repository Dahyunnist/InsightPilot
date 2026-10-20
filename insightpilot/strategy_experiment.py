"""Offline paired policy experiment; never changes the production policy."""

from __future__ import annotations

import json
import statistics
from pathlib import Path


def reward(row: dict) -> float:
    score = 0.5 * bool(row.get("metric_ok")) + 0.5 * bool(row.get("dimension_ok"))
    score += bool(row.get("numeric_ok")) + bool(row.get("sql_verified"))
    if row.get("result", {}).get("status") in ("clarify", "refused"):
        score += 0.25
    if row.get("result", {}).get("status") == "failed" and row.get("result", {}).get("stage") in ("query", "verification"):
        score -= 1
    return round(score, 2)


def _load(path: Path) -> dict[str, dict]:
    return {item["case_id"]: item for item in
            (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())}


def run(reports: Path, output: Path) -> dict:
    retrieval = _load(reports / "enterprise-evaluation-raw.jsonl")
    no_retrieval = _load(reports / "enterprise-ablation-no-retrieval.jsonl")
    paired = sorted(set(retrieval) & set(no_retrieval))
    if len(paired) < 3:
        raise ValueError("paired enterprise cases required; need at least three")
    frozen_ids = [paired[-1]]
    train_ids = paired[:-1]
    actions = {"semantic_retrieval": retrieval, "no_retrieval": no_retrieval}
    q_values = {name: statistics.mean(reward(rows[case_id]) for case_id in train_ids)
                for name, rows in actions.items()}
    learned_action = max(q_values, key=q_values.get)
    always_retrieval_score = statistics.mean(reward(retrieval[i]) for i in frozen_ids)
    learned_score = statistics.mean(reward(actions[learned_action][i]) for i in frozen_ids)
    report = {
        "method": "offline_paired_terminal_reward_action_value",
        "train_case_ids": train_ids,
        "frozen_holdout_case_ids": frozen_ids,
        "action_mean_reward_train": q_values,
        "learned_action": learned_action,
        "frozen_holdout_mean_reward": {
            "learned_policy": round(learned_score, 3),
            "always_semantic_retrieval": round(always_retrieval_score, 3),
        },
        "frozen_holdout_gain": round(learned_score - always_retrieval_score, 3),
        "reproducible_policy_gain": learned_score > always_retrieval_score,
        "agentic_rl_claim_supported": False,
        "reason": "N=1 冻结用例且策略未胜过固定检索基线；该离线单步奖励实验不构成 Agentic-RL。",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    root = Path.cwd()
    print(json.dumps(run(root / "reports", root / "reports/strategy-experiment.json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
