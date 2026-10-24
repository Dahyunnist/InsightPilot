"""Write a no-call v12 model budget lock from frozen local artifacts."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from insightpilot.config import load_settings

CAP = 5.0
REMAINING = 4.67035375
CASES = 20
ATTEMPTS = 2
INPUT_LIMIT = 2_000
OUTPUT_LIMIT = 1_024
INPUT_PRICE = 0.30
OUTPUT_PRICE = 1.20
CNY_PER_USD = 6.671


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_provider_balance(api_key: str, origin: str) -> dict:
    if urlparse(origin).netloc.lower() != "api.deepseek.com":
        raise SystemExit("Configured provider is not DeepSeek; balance check cannot run")
    request = Request(origin + "/user/balance", headers={"Authorization": "Bearer " + api_key,
                                                            "Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise SystemExit("DeepSeek balance query failed: " + type(exc).__name__) from None
    if not isinstance(payload, dict) or payload.get("is_available") is not True:
        raise SystemExit("DeepSeek reports account unavailable")
    entries = payload.get("balance_infos")
    if not isinstance(entries, list):
        raise SystemExit("DeepSeek balance response is missing balance_infos")
    return {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "is_available": True,
            "balances": [{key: item.get(key) for key in ("currency", "total_balance", "granted_balance", "topped_up_balance")}
                         for item in entries if isinstance(item, dict)]}


def main() -> None:
    settings = load_settings(ROOT)
    parsed = urlparse(settings.base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    per_call = (INPUT_LIMIT * INPUT_PRICE + OUTPUT_LIMIT * OUTPUT_PRICE) / 1_000_000
    reserve = round(CASES * ATTEMPTS * per_call, 8)
    if REMAINING + 1e-12 < reserve or REMAINING > CAP:
        raise SystemExit("v12 worst-case reserve exceeds available global cap")
    provider_balance = read_provider_balance(settings.api_key, origin)
    balances = {item["currency"]: item for item in provider_balance["balances"]}
    if "USD" in balances and float(balances["USD"].get("total_balance", "0")) >= reserve:
        provider_reserve_check = {"currency": "USD", "available": float(balances["USD"]["total_balance"]),
                                 "required": reserve, "sufficient": True}
    else:
        cny_balance = float(balances.get("CNY", {}).get("total_balance", "0"))
        reserve_cny = round(reserve * CNY_PER_USD, 6)
        if cny_balance + 1e-9 < reserve_cny:
            raise SystemExit("DeepSeek CNY balance cannot cover the v12 worst-case reserve")
        provider_reserve_check = {"currency": "CNY", "available": cny_balance,
                                  "required": reserve_cny, "sufficient": True,
                                  "usd_to_cny_rate": CNY_PER_USD,
                                  "fx_source": "https://www.imf.org/external/np/fin/ert/GUI/Pages/Report.aspx?CF=Compressed&CT=%27CHN%27&CUF=Period&DS=Ascending&DT=Blank&EX=REP&P=MonthToDate",
                                  "post_reserve_estimate": round(cny_balance - reserve_cny, 6)}
    blind = ROOT / "evals/enterprise-blind-v12.jsonl"
    gold = ROOT / "evals/enterprise-gold-v12.jsonl"
    manifest = ROOT / "evals/enterprise-holdout-manifest-v12.json"
    scorer = ROOT / "insightpilot/enterprise_evaluate.py"
    planner = ROOT / "insightpilot/planner.py"
    runner = ROOT / "insightpilot/enterprise_evaluate.py"
    model_runner = ROOT / "scripts/run_enterprise_v12_model.py"
    rule_baseline = ROOT / "reports/enterprise-v12-rule-baseline.json"
    question_only = '.analyze(case["question"])'
    if question_only not in runner.read_text(encoding="utf-8"):
        raise SystemExit("Agent invocation boundary changed; no preregistration written")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    lock = {
        "suite_id": "enterprise-blind-v12",
        "status": "frozen_pre_model",
        "model_calls": 0,
        "seed": 20261027,
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
        "base_url_origin": origin,
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
        "provider_balance_snapshot": provider_balance,
        "provider_balance_reserve_check": provider_reserve_check,
        "scorer_sha256": sha(scorer),
        "planner_sha256": sha(planner),
        "evaluator_sha256": sha(runner),
        "model_runner_sha256": sha(model_runner),
        "rule_baseline_sha256": sha(rule_baseline),
        "old_ip4_touched": False,
        "v6_v7_reused_as_test": False,
    }
    (ROOT / "reports/enterprise-v12-model-preregistration.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(lock, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()



