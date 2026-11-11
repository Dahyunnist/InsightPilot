"""Seal and pre-register IP4 v13; checks balance, sends no completion request."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data.build_ip4_v13_holdout import CASES, MAX_PROMPT_BYTES_PER_CALL
from insightpilot.config import load_settings

CAP_USD = 5.0
INPUT_TOKENS_PER_CALL = 3_000
OUTPUT_TOKENS_PER_CALL = 1_024
MAX_ATTEMPTS_PER_CASE = 2
INPUT_PEAK_USD_PER_MILLION = 0.30
OUTPUT_PEAK_USD_PER_MILLION = 1.20
CONSERVATIVE_CNY_PER_USD = 7.50
PRICING_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def provider_balance(api_key: str, origin: str) -> dict:
    if urlparse(origin).netloc.lower() != "api.deepseek.com":
        raise SystemExit("Configured provider is not DeepSeek; no preregistration written")
    request = Request(origin + "/user/balance", headers={"Authorization": "Bearer " + api_key,
                                                            "Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise SystemExit("DeepSeek balance query failed: " + type(exc).__name__) from None
    if not isinstance(payload, dict) or payload.get("is_available") is not True:
        raise SystemExit("DeepSeek account unavailable; no preregistration written")
    rows = payload.get("balance_infos")
    if not isinstance(rows, list):
        raise SystemExit("DeepSeek balance response missing balance_infos")
    return {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "is_available": True,
            "balances": [{key: row.get(key) for key in
                          ("currency", "total_balance", "granted_balance", "topped_up_balance")}
                         for row in rows if isinstance(row, dict)]}


def main() -> None:
    evals = ROOT / "evals"
    reports = ROOT / "reports"
    blind = evals / "ip4-v13-blind.jsonl"
    gold = evals / "ip4-v13-gold.jsonl"
    manifest_path = evals / "ip4-v13-manifest.json"
    baseline_path = reports / "ip4-v13-rule-baseline.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["status"] != "frozen_pre_model" or manifest["old_ip4_touched"]:
        raise SystemExit("v13 manifest is not a valid new holdout; no registration written")
    if sha(blind) != manifest["blind_sha256"] or sha(gold) != manifest["gold_sha256"]:
        raise SystemExit("v13 blind/gold hash mismatch; no registration written")
    if len(CASES) != manifest["case_count"] or manifest["exact_normalized_prompt_overlap_count"] != 0:
        raise SystemExit("v13 case count or prompt overlap check failed")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline["model_calls"] != 0 or baseline["data_fingerprint"] != manifest["data_fingerprint"]:
        raise SystemExit("v13 offline baseline does not match frozen data")

    ledger_path = reports / "model-experiment-cost-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    existing_reservation = ledger.get("ip4_v13_reservation", {})
    if existing_reservation.get("status") == "reserved":
        if (existing_reservation.get("holdout") != manifest["suite_id"] or
                existing_reservation.get("blind_sha256") != sha(blind) or
                existing_reservation.get("gold_sha256") != sha(gold)):
            raise SystemExit("A different cost reservation is pending")
        prior_remaining = existing_reservation["global_remaining_before_usd"]
    else:
        prior_remaining = ledger.get("remaining_global_cap_after_ip4_v13_model_usd",
                                     ledger["remaining_global_cap_after_ip4_v3_model_usd"])
    if ledger["cap_usd"] != CAP_USD or prior_remaining > CAP_USD:
        raise SystemExit("Global model cost ledger exceeds the authorized $5 cap")
    max_calls = len(CASES) * MAX_ATTEMPTS_PER_CASE
    per_call = (INPUT_TOKENS_PER_CALL * INPUT_PEAK_USD_PER_MILLION +
                OUTPUT_TOKENS_PER_CALL * OUTPUT_PEAK_USD_PER_MILLION) / 1_000_000
    reserve_usd = round(max_calls * per_call, 8)
    reserve_cny = round(reserve_usd * CONSERVATIVE_CNY_PER_USD, 6)
    if reserve_usd > prior_remaining or reserve_cny > 5.0:
        raise SystemExit("v13 peak-price reserve exceeds the remaining cap or ¥5 limit")

    settings = load_settings(ROOT)
    parsed = urlparse(settings.base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    balance = provider_balance(settings.api_key, origin)
    currencies = {row["currency"]: float(row["total_balance"] or 0)
                  for row in balance["balances"] if row.get("currency")}
    if currencies.get("USD", 0.0) >= reserve_usd:
        balance_check = {"currency": "USD", "available": currencies["USD"],
                         "required": reserve_usd, "sufficient": True}
    else:
        available_cny = currencies.get("CNY", 0.0)
        if available_cny + 1e-9 < reserve_cny:
            raise SystemExit("DeepSeek balance cannot cover v13 peak reserve; no model request sent")
        balance_check = {"currency": "CNY", "available": available_cny,
                         "required": reserve_cny, "sufficient": True,
                         "conservative_cny_per_usd": CONSERVATIVE_CNY_PER_USD}

    stamp = datetime.now(timezone.utc).isoformat()
    seal = {
        "suite_id": manifest["suite_id"], "status": "sealed_pre_model", "sealed_at_utc": stamp,
        "data_fingerprint": manifest["data_fingerprint"], "blind_sha256": sha(blind),
        "gold_sha256": sha(gold), "manifest_sha256": sha(manifest_path),
        "case_count": manifest["case_count"], "gold_method": manifest["gold_method"],
        "gold_not_in_agent_context": True, "old_ip4_touched": False,
        "model_calls_during_freeze_and_baseline": 0,
    }
    seal_path = reports / "ip4-v13-gold-seal.json"
    seal_path.write_text(json.dumps(seal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    artifacts = {
        "blind_sha256": sha(blind), "gold_sha256": sha(gold),
        "manifest_sha256": sha(manifest_path), "gold_seal_sha256": sha(seal_path),
        "scorer_sha256": sha(ROOT / "insightpilot/enterprise_evaluate.py"),
        "planner_sha256": sha(ROOT / "insightpilot/planner.py"),
        "model_runner_sha256": sha(ROOT / "scripts/run_ip4_v13_model_blind.py"),
        "rule_baseline_sha256": sha(baseline_path),
        "rule_verifier_sha256": sha(ROOT / "scripts/verify_ip4_v13_rule.py"),
        "holdout_builder_sha256": sha(ROOT / "data/build_ip4_v13_holdout.py"),
        "dataset_builder_sha256": sha(ROOT / "data/multidomain_seed.py"),
        "test_sha256": sha(ROOT / "tests/test_ip4_v13_holdout.py"),
        "preregistration_script_sha256": sha(ROOT / "scripts/preregister_ip4_v13_model.py"),
        "agent_runtime_sha256": sha(ROOT / "insightpilot/agent.py"),
        "metric_runtime_sha256": sha(ROOT / "insightpilot/metrics.py"),
        "query_runtime_sha256": sha(ROOT / "insightpilot/query_engine.py"),
        "sql_runtime_sha256": sha(ROOT / "insightpilot/sql.py"),
        "sql_policy_runtime_sha256": sha(ROOT / "insightpilot/sql_policy.py"),
        "verification_runtime_sha256": sha(ROOT / "insightpilot/verification.py"),
        "config_runtime_sha256": sha(ROOT / "insightpilot/config.py"),
        "requirements_sha256": sha(ROOT / "requirements.txt"),
        "cost_ledger_sha256_before_reservation": sha(ledger_path),
    }
    lock = {
        "suite_id": manifest["suite_id"], "status": "frozen_pre_model",
        "preregistered_at_utc": stamp, "model_calls": 0,
        "freeze_commit_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "python_version": platform.python_version(), "python_implementation": platform.python_implementation(),
        "model": settings.model, "base_url_origin": origin,
        "data_version": manifest["data_version"], "data_fingerprint": manifest["data_fingerprint"],
        "retail_seed": manifest["retail_seed"], "marketing_seed": manifest["marketing_seed"],
        "case_count": len(CASES), "answerable_case_count": manifest["answerable_case_count"],
        "guard_case_count": manifest["guard_case_count"], "max_attempts_per_case": MAX_ATTEMPTS_PER_CASE,
        "max_api_calls": max_calls, "execution_attempts_per_case": 1,
        "planned_max_api_calls": len(CASES), "input_tokens_per_request": INPUT_TOKENS_PER_CALL,
        "max_prompt_bytes_per_call": MAX_PROMPT_BYTES_PER_CALL,
        "output_tokens_per_request": OUTPUT_TOKENS_PER_CALL,
        "temperature": 0, "single_backend_arm": "SQLite",
        "agent_input_contract": "Each model request receives one blind question only; scorer loads gold after analysis.",
        "pricing": {"source": PRICING_SOURCE, "basis": "DeepSeek Flash peak uncached input and peak output rates",
                    "input_peak_usd_per_million": INPUT_PEAK_USD_PER_MILLION,
                    "output_peak_usd_per_million": OUTPUT_PEAK_USD_PER_MILLION},
        "per_call_peak_usd": round(per_call, 8), "worst_case_reserve_usd": reserve_usd,
        "worst_case_reserve_cny": reserve_cny,
        "global_cap_usd": CAP_USD, "global_remaining_before_usd": prior_remaining,
        "global_remaining_after_reserve_usd": round(prior_remaining - reserve_usd, 8),
        "max_reserve_cny_limit": 5.0, "provider_balance_snapshot": balance,
        "provider_balance_reserve_check": balance_check,
        "old_ip4_touched": False, "v12_score_reused": False,
        "artifacts": artifacts,
    }
    lock_path = reports / "ip4-v13-model-preregistration.json"
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger["remaining_global_cap_after_ip4_v13_reserve_usd"] = round(prior_remaining - reserve_usd, 8)
    ledger["ip4_v13_reservation"] = {
        "status": "reserved", "holdout": manifest["suite_id"],
        "worst_case_reserve_usd": reserve_usd, "worst_case_reserve_cny": reserve_cny,
        "global_remaining_before_usd": prior_remaining,
        "global_remaining_after_reserve_usd": round(prior_remaining - reserve_usd, 8),
        "blind_sha256": artifacts["blind_sha256"], "gold_sha256": artifacts["gold_sha256"],
        "preregistration_sha256": sha(lock_path), "model_calls": 0,
    }
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in lock.items()
                      if key not in ("provider_balance_snapshot", "artifacts")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
