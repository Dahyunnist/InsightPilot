"""Check sealed evaluation evidence and budget arithmetic without external calls."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_ip4_freeze_seal import verify as verify_ip4_seal


def _read(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _sha(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def verify() -> dict:
    seal = verify_ip4_seal()
    rule = _read("reports/domain-validation-v1.json")
    diagnostic = _read("reports/ip4-r01-r02-dev-diagnostic.json")
    prereg = _read("reports/domain-validation-model-preregistration.json")
    model = _read("reports/domain-validation-model-blind-report.json")
    ledger = _read("reports/model-experiment-cost-ledger.json")
    accounting = ledger["domain_validation_model_v1"]

    rule_good = rule["status"] == "passed" and rule["model_calls"] == 0 and len(rule["runs"]) == 6
    rule_good = rule_good and all(row["result"]["passed"] == row["result"]["total"] == 6
                                  for row in rule["runs"])
    dev_good = diagnostic["model_calls"] == 0 and len(diagnostic["diagnostics"]) == 6
    dev_good = dev_good and all(row["status"] == "answered" and row["verification_passed"]
                                for row in diagnostic["diagnostics"])
    dev_good = dev_good and all(row["only_own_tenant_visible"]
                                for key, row in diagnostic["tenant_scope_probe"].items()
                                if key.startswith("tenant-"))
    dev_good = dev_good and diagnostic["tenant_scope_probe"]["missing_context"]["rejected"]

    arms_good = len(model["runs"]) == 3 and all(run["passed"] == run["total"] == 12
                                                 for run in model["runs"])
    sql_cases = [case for run in model["runs"] for case in run["cases"]
                 if case["oracle_gold"]["expected_status"] == "answered"]
    arms_good = arms_good and all(case["compiled_sql"] and case["passed"] and case["verification_passed"]
                                  for case in sql_cases)
    arms_good = arms_good and all(not run["false_refusals"] and not run["false_accepts"]
                                  for run in model["runs"])
    budget_good = (model["old_ip4_touched"] is False and
                   model["observed_calls"] <= prereg["maximum_api_calls"] and
                   model["worst_case_reserve_usd"] == prereg["worst_case_reserve_usd"] and
                   abs(model["estimated_max_charge_for_run_usd"] -
                       (model["peak_list_price_estimate_usd"] +
                        model["missing_usage_worst_case_reserve_usd"])) < 1e-10 and
                   abs(ledger["remaining_global_cap_after_domain_model_usd"] -
                       (model["global_remaining_before_usd"] -
                        model["estimated_max_charge_for_run_usd"])) < 1e-8)
    refs_good = (_sha("reports/domain-validation-model-preregistration.json") ==
                 model["preregistration_sha256"] and
                 _sha("reports/domain-validation-model-blind-report.json") ==
                 accounting["report_sha256"] and
                 prereg["scorer"]["sha256"] == model["scorer_sha256"])

    checks = {"ip4_seal": seal["status"] == "passed", "rule_domain_sets": rule_good,
              "r01_r02_dev_diagnostics_and_rls": dev_good,
              "model_blind_scores_and_sql": arms_good, "model_budget": budget_good,
              "report_hash_references": refs_good}
    return {"status": "passed" if all(checks.values()) else "failed", "checks": checks,
            "model_calls_during_check": 0, "sql_question_rows": len(sql_cases),
            "strict_arms": {f"{run['backend']}:{run['tenant_id'] or 'shared'}":
                            f"{run['passed']}/{run['total']}" for run in model["runs"]},
            "estimated_charge_usd": model["estimated_max_charge_for_run_usd"],
            "remaining_global_cap_usd": ledger["remaining_global_cap_after_domain_model_usd"]}


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
