"""Write a no-call v8 model budget lock from frozen local artifacts."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from insightpilot.config import load_settings

CAP = 4.0
REMAINING = 3.69709005
CASES = 20
ATTEMPTS = 2
INPUT_LIMIT = 2_000
OUTPUT_LIMIT = 1_024
INPUT_PRICE = 0.30
OUTPUT_PRICE = 1.20


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    settings = load_settings(ROOT)
    per_call = (INPUT_LIMIT * INPUT_PRICE + OUTPUT_LIMIT * OUTPUT_PRICE) / 1_000_000
    reserve = round(CASES * ATTEMPTS * per_call, 8)
    if REMAINING + 1e-12 < reserve or REMAINING > CAP:
        raise SystemExit("v8 worst-case reserve exceeds available global cap")
    blind = ROOT / "evals/enterprise-blind-v8.jsonl"
    gold = ROOT / "evals/enterprise-gold-v8.jsonl"
    manifest = ROOT / "evals/enterprise-holdout-manifest-v8.json"
    scorer = ROOT / "insightpilot/enterprise_evaluate.py"
    planner = ROOT / "insightpilot/planner.py"
    runner = ROOT / "insightpilot/enterprise_evaluate.py"
    model_runner = ROOT / "scripts/run_enterprise_v8_model.py"
    rule_baseline = ROOT / "reports/enterprise-v8-rule-baseline.json"
    question_only = '.analyze(case["question"])'
    if question_only not in runner.read_text(encoding="utf-8"):
        raise SystemExit("Agent invocation boundary changed; no preregistration written")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    lock = {
        "suite_id": "enterprise-blind-v8",
        "status": "frozen_pre_model",
        "model_calls": 0,
        "seed": 20261023,
        "data_fingerprint": data["data_fingerprint"],
        "case_count": CASES,
        "answerable": data["answerable_case_count"],
        "guard": data["clarify_case_count"] + data["refuse_case_count"],
        "blind_sha256": sha(blind),
        "gold_sha256": sha(gold),
        "manifest_blind_questions_sha256": data["blind_questions_sha256"],
        "manifest_gold_sha256": data["gold_sha256"],
        "manifest_sha256": sha(manifest),
        "gold_oracle": data["oracle"],
        "exact_prompt_overlap_count": data["exact_prompt_overlap_count"],
        "gold_not_in_agent_context": True,
        "agent_input_contract": "Each model call receives one blind question; scorer alone loads gold after analysis.",
        "model": settings.model,
        "base_url_origin": f"{urlparse(settings.base_url).scheme}://{urlparse(settings.base_url).netloc}",
        "temperature": 0,
        "max_output_tokens": OUTPUT_LIMIT,
        "max_attempts_per_case": ATTEMPTS,
        "max_api_calls": CASES * ATTEMPTS,
        "prompt_token_ceiling": INPUT_LIMIT,
        "pricing": {"source": "https://api-docs.deepseek.com/quick_start/pricing/", "basis": "peak uncached Flash rates", "input_usd_per_million": INPUT_PRICE, "output_usd_per_million": OUTPUT_PRICE},
        "per_call_worst_case_usd": round(per_call, 8),
        "worst_case_reserve_usd": reserve,
        "global_cap_usd": CAP,
        "global_remaining_before_usd": REMAINING,
        "global_remaining_after_reserve_usd": round(REMAINING - reserve, 8),
        "scorer_sha256": sha(scorer),
        "planner_sha256": sha(planner),
        "evaluator_sha256": sha(runner),
        "model_runner_sha256": sha(model_runner),
        "rule_baseline_sha256": sha(rule_baseline),
        "old_ip4_touched": False,
        "v6_v7_reused_as_test": False,
    }
    (ROOT / "reports/enterprise-v8-model-preregistration.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(lock, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
