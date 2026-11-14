"""Run the frozen 22-question IP4 synthetic retrieval-on/off comparison."""

from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.agent import InsightPilot, _preflight
from insightpilot.config import Settings, load_settings
from insightpilot.enterprise_evaluate import BudgetedPlanner, EvaluationBudget, _compare
from insightpilot.metrics import METRICS, retrieve_metrics
from insightpilot.query_engine import SQLiteQueryEngine
from insightpilot.runner import read_data_identity
from data.build_ip4_v13_holdout import MARKETING_SEED, ORDER_COUNT, RETAIL_SEED
from data.multidomain_seed import create_multidomain_database


BUNDLE = ROOT / "benchmarks/ip4-offline-prep-v1"
MANIFEST = BUNDLE / "manifest.json"
DATABASE = ROOT / "data/insightpilot-multidomain.sqlite"
LEDGER = ROOT / "reports/model-experiment-cost-ledger.json"
OUTPUT = ROOT / "reports/ip4-retrieval-ablation-model.json"
INPUT_TOKENS_PER_CALL = 4_000  # UTF-8 prompt bytes are capped at this value.
OUTPUT_TOKENS_PER_CALL = 960
CAP_USD = 0.1873344
INPUT_RATE = 0.30
OUTPUT_RATE = 1.20
PROMPT_BYTE_CAP = 4_000
CONSERVATIVE_CNY_PER_USD = 7.5


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_bundle(database: Path) -> tuple[dict, list[dict], dict[str, dict], list[tuple[str, Path, Path]]]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    domains = []
    cases: list[dict] = []
    gold: dict[str, dict] = {}
    for domain in ("refunds", "marketing_roi"):
        entry = manifest["domains"][domain]
        blind_path = BUNDLE / entry["blind_path"]
        gold_path = BUNDLE / entry["gold_path"]
        if sha(blind_path) != entry["blind_sha256"] or sha(gold_path) != entry["gold_sha256"]:
            raise SystemExit("Frozen question/gold hash mismatch; no model request sent.")
        domain_cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        domain_gold = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(domain_cases) != entry["case_count"] or {r["case_id"] for r in domain_cases} != {r["case_id"] for r in domain_gold}:
            raise SystemExit("Frozen question/gold case identity mismatch; no model request sent.")
        cases.extend(domain_cases)
        gold.update({row["case_id"]: row for row in domain_gold})
        domains.append((domain, blind_path, gold_path))
    if len(cases) != manifest["case_count"] or len(gold) != len(cases):
        raise SystemExit("Frozen IP4 case count mismatch; no model request sent.")
    version, fingerprint = read_data_identity(database)
    if version != manifest["data"]["data_version"] or fingerprint != manifest["data"]["data_fingerprint"]:
        raise SystemExit("Frozen IP4 database identity mismatch; no model request sent.")
    if sha(database) != manifest["data"]["sqlite_sha256"]:
        raise SystemExit("Frozen IP4 database hash mismatch; no model request sent.")
    return manifest, cases, gold, domains


def prompt_bytes(case: dict, retrieval_on: bool) -> int:
    """Mirror the planner's fixed system/user content without making a request."""
    from dataclasses import asdict

    from insightpilot.planner import OpenAICompatiblePlanner

    candidates = retrieve_metrics(case["question"]) if retrieval_on else []
    candidate_ids = [metric.metric_id for metric in candidates] if retrieval_on else list(METRICS)
    metric_context = [asdict(METRICS[item]) for item in candidate_ids] if retrieval_on else []
    user_content = json.dumps({"question": case["question"], "candidate_metrics": metric_context,
                               "allowed_metric_ids": candidate_ids}, ensure_ascii=False)
    system = (
        "Extract exactly one analytics query plan as JSON. Never produce SQL. "
        "Choose metric_id only from allowed_metric_ids. Dates must be YYYY-MM-DD. "
        "Convert each inclusive final calendar date to the next date as exclusive end. "
        "For comparison, return start_date, end_date_exclusive and comparison_periods as a list of two [start, exclusive_end] ranges. "
        "Return the shortest valid JSON. If question has no explicit calendar date(s), no single metric, requests multiple metrics, or cannot be grounded, set clarify=true and explain briefly. "
        "Allowed dimensions come from candidate_metrics.valid_dimensions. Allowed region: a literal from user or null. "
        'Return keys: clarify (boolean), clarification (string), metric_id, start_date, end_date_exclusive, comparison_periods, region, dimension.'
    )
    # Match the production planner cap (system + user content, UTF-8 bytes).
    return len(system.encode("utf-8")) + len(user_content.encode("utf-8"))


def planned_external_analyses(cases: list[dict], retrieval_on: bool) -> int:
    """Count deterministic planner short-circuits without making a network call."""
    from insightpilot.planner import OpenAICompatiblePlanner

    class StopBeforeRequest(Exception):
        pass

    class ProbePlanner(OpenAICompatiblePlanner):
        def _complete(self, question: str, candidates: list[str], allowed_metric_ids: list[str]) -> dict:
            raise StopBeforeRequest

    probe_settings = Settings(api_key="", base_url="https://invalid.local", model="preflight",
                              timeout_seconds=1, max_completion_tokens=OUTPUT_TOKENS_PER_CALL,
                              database_path=DATABASE)
    count = 0
    for case in cases:
        probe = ProbePlanner(probe_settings, use_metric_retrieval=retrieval_on)
        try:
            probe.plan(case["question"])
        except StopBeforeRequest:
            count += 1
        except Exception:
            # Deterministic clarification/refusal/failure before _complete means no request.
            pass
    return count


def assess_balance(payload: dict, required_usd: float) -> dict:
    """Accept USD funds directly or CNY funds at the conservative locked rate."""
    if not isinstance(payload, dict) or payload.get("is_available") is not True:
        return {"status": "account_unavailable"}
    rows = payload.get("balance_infos")
    if not isinstance(rows, list):
        return {"status": "balance_data_missing"}
    currencies: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("currency") not in {"USD", "CNY"}:
            continue
        try:
            amount = float(row.get("total_balance") or 0)
        except (TypeError, ValueError):
            continue
        if math.isfinite(amount) and amount >= 0:
            currencies[row["currency"]] = amount
    usd = currencies.get("USD", 0.0)
    if usd + 1e-12 >= required_usd:
        return {"status": "passed", "currency_checked": "USD", "available": usd,
                "required_usd": required_usd}
    cny = currencies.get("CNY", 0.0)
    required_cny = required_usd * CONSERVATIVE_CNY_PER_USD
    if cny + 1e-9 >= required_cny:
        return {"status": "passed", "currency_checked": "CNY", "available": cny,
                "required_cny": round(required_cny, 6),
                "conservative_cny_per_usd": CONSERVATIVE_CNY_PER_USD}
    return {"status": "unavailable_or_below_reserve", "required_usd": required_usd,
            "required_cny": round(required_cny, 6), "usd_available": usd, "cny_available": cny}


def check_balance(settings, required_usd: float) -> dict:
    parsed = urlparse(settings.base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    request = Request(origin + "/user/balance", headers={"Authorization": "Bearer " + settings.api_key,
                                                           "Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "status": "check_failed",
                "error_class": type(exc).__name__}
    result = assess_balance(payload, required_usd)
    result["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
    return result


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    # Rebuild the frozen fixture outside the repository so the unrelated working
    # database cannot silently contaminate this holdout or be overwritten.
    tempdir = tempfile.TemporaryDirectory(prefix="insightpilot-ip4-model-")
    atexit.register(tempdir.cleanup)
    temp_root = Path(tempdir.name)
    database = temp_root / "frozen-ip4.sqlite"
    create_multidomain_database(database, seed=MARKETING_SEED, retail_seed=RETAIL_SEED,
                                order_count=ORDER_COUNT)
    manifest, cases, gold, domain_files = load_bundle(database)
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    remaining_key = "remaining_global_cap_after_ip4_v13_model_usd"
    remaining = float(ledger[remaining_key])
    per_call = (INPUT_TOKENS_PER_CALL * INPUT_RATE + OUTPUT_TOKENS_PER_CALL * OUTPUT_RATE) / 1_000_000
    blocked = sum(1 for case in cases if _preflight(case["question"]) is not None)
    arm_planned = {"retrieval_on": planned_external_analyses(cases, True),
                   "retrieval_off": planned_external_analyses(cases, False)}
    max_calls = 2 * sum(arm_planned.values())
    reserve = round(max_calls * per_call, 8)
    settings = load_settings(ROOT)
    settings = replace(settings, max_completion_tokens=min(settings.max_completion_tokens, OUTPUT_TOKENS_PER_CALL),
                       database_path=database)
    if settings.model != "deepseek-v4-flash":
        raise SystemExit("Configured model differs from frozen IP4 approval; no request sent.")
    if remaining + 1e-12 < reserve or reserve > CAP_USD:
        raise SystemExit("Global remaining budget is below this run's approved reserve; no request sent.")
    if blocked != 3 or arm_planned != {"retrieval_on": 15, "retrieval_off": 19} or max_calls > 88:
        raise SystemExit("Local deterministic guard count changed; re-freeze budget before model calls.")
    max_prompt = max(prompt_bytes(case, arm) for case in cases for arm in (True, False))
    if max_prompt > PROMPT_BYTE_CAP:
        raise SystemExit("A frozen prompt exceeds the byte cap; no model request sent.")
    if args.preflight_only:
        print(json.dumps({"preflight": "passed", "suite_id": manifest["suite_id"], "case_count": len(cases),
                          "retrieval_arms": 2, "local_guard_skips_per_arm": blocked,
                          "maximum_api_calls": max_calls, "planned_external_analyses_by_arm": arm_planned,
                          "max_prompt_content_bytes": max_prompt,
                          "completion_token_cap": settings.max_completion_tokens,
                          "worst_case_reserve_usd": reserve,
                          "remaining_after_reserve_usd": round(remaining - reserve, 8)}, indent=2))
        return

    balance = check_balance(settings, reserve)
    if balance["status"] != "passed":
        blocked = {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "blocked_provider_balance_preflight",
            "suite_id": manifest["suite_id"], "case_count": len(cases),
            "retrieval_arms": ["retrieval_on", "retrieval_off"],
            "maximum_api_calls": max_calls, "worst_case_reserve_usd": reserve,
            "global_remaining_before_usd": remaining,
            "global_remaining_after_usd": remaining,
            "provider_balance_preflight": balance,
            "model_requests_sent": 0, "model_payload_data_sent": False,
            "model_call_authorization_used": False,
            "manifest_sha256": sha(MANIFEST),
            "blind_sha256": {domain: sha(blind) for domain, blind, _ in domain_files},
            "gold_sha256_local_only": {domain: sha(gpath) for domain, _, gpath in domain_files},
            "note": "Only the provider's account-balance endpoint was checked. The synthetic questions and all evaluation data remained local.",
        }
        write_json(OUTPUT.with_name("ip4-retrieval-ablation-model-preflight.json"), blocked)
        print(json.dumps({key: value for key, value in blocked.items()
                          if key not in ("blind_sha256", "gold_sha256_local_only")}, ensure_ascii=False, indent=2))
        return
    # Debit the full maximum before the first call. An interrupted run keeps this reserve.
    after_reserve = round(remaining - reserve, 8)
    ledger[remaining_key] = after_reserve
    ledger["ip4_retrieval_ablation_model"] = {
        "status": "in_progress_reserve_held", "suite_id": manifest["suite_id"],
        "data_fingerprint": manifest["data"]["data_fingerprint"], "case_count": len(cases),
        "maximum_api_calls": max_calls, "reserve_usd": reserve,
        "remaining_before_usd": remaining, "remaining_after_reserve_usd": after_reserve,
        "model": settings.model, "provider_balance_preflight": balance,
        "request_payload_fields": ["question", "candidate_metrics", "allowed_metric_ids"],
        "request_excludes_gold_rows_sql_and_query_results": True,
    }
    write_json(LEDGER, ledger)

    # Prevent optional tracing exporters from sending any evaluation data elsewhere.
    for name in ("INSIGHTPILOT_TRACE_JSONL", "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        os.environ.pop(name, None)

    budget = EvaluationBudget(cap_usd=reserve, max_calls=max_calls,
                              prompt_tokens=INPUT_TOKENS_PER_CALL,
                              completion_tokens=settings.max_completion_tokens)
    arms = []
    for retrieval_on in (True, False):
        arm_name = "retrieval_on" if retrieval_on else "retrieval_off"
        detail_rows = []
        for case in cases:
            planner = BudgetedPlanner(settings, budget, use_metric_retrieval=retrieval_on)
            planner.max_prompt_bytes_per_call = PROMPT_BYTE_CAP
            core = InsightPilot(planner, database, query_engine=SQLiteQueryEngine(database), tracer=None)
            try:
                result = core.analyze(case["question"])
            except Exception as exc:
                result = {"status": "failed", "stage": "runner", "reason": type(exc).__name__, "model_calls": []}
            scores = _compare(case, gold[case["case_id"]], result)
            calls = list(getattr(planner, "last_calls", []))
            detail_rows.append({"case_id": case["case_id"], "score": scores,
                                "actual_status": result.get("status"), "terminal_stage": result.get("stage"),
                                "terminal_reason": result.get("reason"), "model_calls": len(calls),
                                "usage": [call.get("usage") for call in calls]})
            # Persist progress while the full cap remains debited in the ledger.
            checkpoint = {"status": "in_progress", "arm": arm_name, "completed_cases_in_arm": len(detail_rows),
                          "total_cases_per_arm": len(cases), "calls_used": budget.calls,
                          "held_reserve_usd": reserve}
            write_json(OUTPUT.with_name("ip4-retrieval-ablation-model-progress.json"), checkpoint)
        passed = sum(row["score"].get("passed", False) for row in detail_rows)
        arms.append({"arm": arm_name, "passed": passed, "total": len(detail_rows),
                     "answerable_passed": sum(row["score"].get("passed", False) for row in detail_rows
                                               if gold[row["case_id"]]["expected_status"] == "answered"),
                     "answerable_total": sum(row["expected_status"] == "answered" for row in gold.values()),
                     "guard_passed": sum(row["score"].get("passed", False) for row in detail_rows
                                          if gold[row["case_id"]]["expected_status"] != "answered"),
                     "guard_total": sum(row["expected_status"] != "answered" for row in gold.values()),
                     "failures": [row for row in detail_rows if not row["score"].get("passed", False)],
                     "cases": detail_rows})

    estimate = budget.estimated_usd
    released = round(max(0.0, reserve - estimate), 8)
    final_remaining = round(after_reserve + released, 8)
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "completed",
        "suite_id": manifest["suite_id"], "data_fingerprint": manifest["data"]["data_fingerprint"],
        "manifest_sha256": sha(MANIFEST),
        "blind_sha256": {domain: sha(blind) for domain, blind, _ in domain_files},
        "gold_sha256_local_only": {domain: sha(gpath) for domain, _, gpath in domain_files},
        "model": settings.model, "retrieval_arms": ["retrieval_on", "retrieval_off"],
        "planned_case_runs": len(cases) * 2, "planned_external_analyses_by_arm": arm_planned,
        "maximum_api_calls": max_calls,
        "observed_api_calls": budget.calls, "input_content_byte_cap": PROMPT_BYTE_CAP,
        "completion_token_cap": settings.max_completion_tokens,
        "reserve_usd": reserve, "observed_peak_price_estimate_usd": round(estimate, 8),
        "remaining_before_usd": remaining, "remaining_after_usd": final_remaining,
        "provider_balance_preflight": balance,
        "request_payload_fields": ["question", "candidate_metrics", "allowed_metric_ids"],
        "request_excludes_case_id_gold_expected_status_database_rows_sql_and_query_results": True,
        "sample_limit": "仅 22 道冻结合成题；结果仅描述本题集，不外推生产或总体泛化。",
        "arms": arms,
    }
    write_json(OUTPUT, report)
    ledger[remaining_key] = final_remaining
    ledger["ip4_retrieval_ablation_model"] = {
        "status": "completed", "suite_id": manifest["suite_id"], "model_calls": budget.calls,
        "passed": {arm["arm"]: f"{arm['passed']}/{arm['total']}" for arm in arms},
        "reserve_usd": reserve, "peak_price_estimate_usd": round(estimate, 8),
        "released_unused_reserve_usd": released, "remaining_before_usd": remaining,
        "remaining_after_usd": final_remaining, "report": "reports/ip4-retrieval-ablation-model.json",
        "provider_balance_preflight": balance,
    }
    write_json(LEDGER, ledger)
    progress = OUTPUT.with_name("ip4-retrieval-ablation-model-progress.json")
    if progress.exists():
        progress.unlink()
    print(json.dumps({key: value for key, value in report.items() if key != "arms"}, ensure_ascii=False, indent=2))
    for arm in arms:
        print(f"{arm['arm']}: {arm['passed']}/{arm['total']} (answerable {arm['answerable_passed']}/{arm['answerable_total']}; guards {arm['guard_passed']}/{arm['guard_total']})")


if __name__ == "__main__":
    main()
