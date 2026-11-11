"""Run one frozen IP4 v3 model evaluation across SQLite and both PostgreSQL tenants."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.agent import InsightPilot
from insightpilot.config import load_settings
from insightpilot.ip4_evaluate import compare
from insightpilot.planner import NeedsClarification, OpenAICompatiblePlanner
from insightpilot.query_engine import PostgresQueryEngine, SQLiteQueryEngine
from insightpilot.rule_baseline import RulePlanner


MANIFEST = ROOT / "evals/ip4-v3-manifest.json"
BLIND = ROOT / "evals/ip4-v3-blind.jsonl"
GOLD = ROOT / "evals/ip4-v3-gold.jsonl"
LOCK = ROOT / "reports/ip4-v3-model-preregistration.json"
OUTPUT = ROOT / "reports/ip4-v3-model-blind-report.json"
LEDGER = ROOT / "reports/model-experiment-cost-ledger.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.payload


def measure_prompt_bytes(cases: list[dict], settings, byte_cap: int) -> list[dict]:
    """Exercise the production planner locally with a stub response; send no network request."""
    measured = []

    def fake_urlopen(request, timeout=None):
        body = json.loads(request.data)
        messages = body["messages"]
        user_payload = json.loads(messages[1]["content"])
        measured.append({"case_id": next(row["case_id"] for row in cases
                                          if row["question"] == user_payload["question"]),
                         "prompt_bytes": sum(len(message["content"].encode("utf-8"))
                                              for message in messages),
                         "request_model": body["model"],
                         "prompt_fields": sorted(user_payload)})
        response = {"model": settings.model, "choices": [{"message": {"content": json.dumps(
            {"clarify": True, "clarification": "local prompt sizing only"})}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        return _FakeResponse(response)

    with patch("insightpilot.planner.urllib.request.urlopen", side_effect=fake_urlopen):
        for case in cases:
            planner = OpenAICompatiblePlanner(settings, max_prompt_bytes_per_call=byte_cap)
            try:
                planner.plan(case["question"])
            except NeedsClarification:
                pass
    return measured


def provider_preflight(settings, lock: dict) -> dict:
    origin = f"{urlparse(settings.base_url).scheme}://{urlparse(settings.base_url).netloc}"
    if origin != lock["base_url_origin"]:
        raise RuntimeError("configured provider origin differs from the frozen lock")
    request = Request(origin + "/user/balance", headers={"Authorization": "Bearer " + settings.api_key,
                                                           "Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("provider balance preflight failed: " + type(exc).__name__) from None
    if payload.get("is_available") is not True:
        raise RuntimeError("provider account is unavailable")
    currency = lock["provider_balance_check"]["currency"]
    rows = payload.get("balance_infos")
    balance = next((row for row in rows if isinstance(row, dict) and row.get("currency") == currency), None) \
        if isinstance(rows, list) else None
    available = float(balance.get("total_balance", 0)) if balance else 0.0
    required = lock["provider_balance_check"]["minimum_balance"]
    if available < required:
        raise RuntimeError("provider balance is below the frozen minimum reserve")
    return {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "is_available": True,
            "currency": currency, "available": available, "minimum_required": required}


def validate_preflight(settings, lock: dict, *, include_provider: bool) -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if lock.get("status") != "frozen_pre_model":
        raise RuntimeError("v3 model lock is not frozen_pre_model")
    expected_hashes = {"manifest_sha256": sha(MANIFEST), "blind_sha256": sha(BLIND), "gold_sha256": sha(GOLD),
                       "planner_sha256": sha(ROOT / "insightpilot/planner.py"),
                       "scorer_sha256": sha(ROOT / "insightpilot/ip4_evaluate.py"),
                       "rule_baseline_sha256": sha(ROOT / "reports/ip4-v3-offline-all-backends.json"),
                       "runner_sha256": sha(Path(__file__).resolve())}
    for key, actual in expected_hashes.items():
        if lock.get(key) != actual:
            raise RuntimeError(f"frozen artifact mismatch: {key}")
    if (manifest["suite_id"] != lock["suite_id"] or manifest["case_count"] != lock["case_count"] or
            manifest["data_version"] != lock["data_version"] or
            manifest["data_fingerprint"] != lock["data_fingerprint"] or
            manifest["blind_sha256"] != lock["blind_sha256"] or manifest["gold_sha256"] != lock["gold_sha256"]):
        raise RuntimeError("v3 manifest identity differs from preregistration")
    if (settings.model != lock["model"] or settings.max_completion_tokens != lock["max_output_tokens"] or
            f"{urlparse(settings.base_url).scheme}://{urlparse(settings.base_url).netloc}" != lock["base_url_origin"]):
        raise RuntimeError("configured model settings differ from preregistration")
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    remaining = float(ledger["remaining_global_cap_after_v12_model_usd"])
    if abs(remaining - float(lock["global_remaining_before_usd"])) > 1e-9:
        raise RuntimeError("model ledger balance changed since preregistration")
    request_reserve = ((lock["prompt_token_ceiling"] * lock["input_peak_usd_per_million"] +
                        lock["max_output_tokens"] * lock["output_peak_usd_per_million"]) / 1_000_000)
    expected_reserve = round(lock["case_count"] * lock["backend_runs"] *
                             lock["max_attempts_per_case"] * request_reserve, 8)
    if abs(expected_reserve - float(lock["worst_case_reserve_usd"])) > 1e-9:
        raise RuntimeError("worst-case cost reserve does not match frozen token/call ceilings")
    if remaining < expected_reserve:
        raise RuntimeError("global budget is below frozen worst-case reserve")
    cases = _read_rows(BLIND)
    gold = _read_rows(GOLD)
    if len(cases) != lock["case_count"] or len(gold) != len(cases) or \
            {row["case_id"] for row in cases} != {row["case_id"] for row in gold}:
        raise RuntimeError("case/gold IDs or case count differ from the frozen lock")
    measured = measure_prompt_bytes(cases, settings, lock["prompt_byte_cap"])
    if len(measured) != lock["cases_reaching_planner"] or \
            any(row["prompt_bytes"] > lock["prompt_byte_cap"] or
                row["request_model"] != settings.model or
                row["prompt_fields"] != ["allowed_metric_ids", "candidate_metrics", "question"]
                for row in measured):
        raise RuntimeError("v3 request payload sizing/shape differs from lock")
    max_prompt = max(row["prompt_bytes"] for row in measured)
    if max_prompt != lock["measured_max_prompt_bytes"]:
        raise RuntimeError("measured prompt size differs from preregistration")
    if not os.environ.get("INSIGHTPILOT_IP4_APP_DSN"):
        raise RuntimeError("test-only INSIGHTPILOT_IP4_APP_DSN is required")
    sqlite_engine = SQLiteQueryEngine(ROOT / "data/insightpilot-multidomain.sqlite")
    identity = sqlite_engine.read_data_identity()
    if identity != (manifest["data_version"], manifest["data_fingerprint"]):
        raise RuntimeError("SQLite dataset identity differs from v3 manifest")
    for tenant in lock["postgres_tenants"]:
        engine = PostgresQueryEngine(os.environ["INSIGHTPILOT_IP4_APP_DSN"], tenant_id=tenant)
        try:
            identity = engine.read_data_identity(tenant_id=tenant)
            if identity != (manifest["data_version"], manifest["data_fingerprint"]):
                raise RuntimeError(f"PostgreSQL {tenant} dataset identity differs from v3 manifest")
        finally:
            engine.close()
    provider = provider_preflight(settings, lock) if include_provider else None
    return {"manifest": manifest, "cases": cases, "gold": {row["case_id"]: row for row in gold},
            "ledger_remaining_usd": remaining, "measured_prompts": measured,
            "provider_balance": provider}


def _summarize(backend: str, tenant_id: str | None, rows: list[dict], gold: dict[str, dict]) -> dict:
    by_domain = {}
    for domain in sorted({row["domain"] for row in rows}):
        subset = [row for row in rows if row["domain"] == domain]
        by_domain[domain] = {"passed": sum(row["passed"] for row in subset), "total": len(subset),
                             "failures": [row["case_id"] for row in subset if not row["passed"]]}
    return {"backend": backend, "tenant_id": tenant_id, "passed": sum(row["passed"] for row in rows),
            "total": len(rows), "by_domain": by_domain,
            "answerable": {"passed": sum(row["passed"] for row in rows
                                          if gold[row["case_id"]]["expected_status"] == "answered"),
                           "total": sum(row["expected_status"] == "answered" for row in gold.values())},
            "guards": {"passed": sum(row["passed"] for row in rows
                                      if gold[row["case_id"]]["expected_status"] != "answered"),
                       "total": sum(row["expected_status"] != "answered" for row in gold.values())},
            "failures": [row for row in rows if not row["passed"]], "cases": rows}


def run(lock: dict, preflight: dict) -> dict:
    settings = load_settings(ROOT)
    dsn = os.environ.get("INSIGHTPILOT_IP4_APP_DSN")
    if not dsn:
        raise RuntimeError("test-only INSIGHTPILOT_IP4_APP_DSN is required before model requests")
    database = ROOT / "data/insightpilot-multidomain.sqlite"
    manifest = preflight["manifest"]
    arms = [("sqlite", None, SQLiteQueryEngine(database))]
    arms.extend(("postgresql", tenant, PostgresQueryEngine(dsn, tenant_id=tenant))
                for tenant in lock["postgres_tenants"])
    runs = []
    all_calls = []
    model_errors = []
    run_started = time.perf_counter()
    result = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "running",
              "suite_id": lock["suite_id"], "model": lock["model"],
              "model_response_versions": [], "blind_sha256": lock["blind_sha256"],
              "gold_sha256": lock["gold_sha256"], "manifest_sha256": lock["manifest_sha256"],
              "planner_sha256": lock["planner_sha256"], "scorer_sha256": lock["scorer_sha256"],
              "model_runner_sha256": lock["runner_sha256"],
              "global_remaining_before_usd": lock["global_remaining_before_usd"],
              "worst_case_reserve_usd": lock["worst_case_reserve_usd"],
              "global_remaining_after_reserve_usd": round(
                  lock["global_remaining_before_usd"] - lock["worst_case_reserve_usd"], 8),
              "provider_balance_preflight": preflight["provider_balance"],
              "prompt_sizing_preflight": {"cases_reaching_planner": len(preflight["measured_prompts"]),
                                           "maximum_prompt_bytes": lock["measured_max_prompt_bytes"],
                                           "prompt_byte_cap": lock["prompt_byte_cap"],
                                           "prompt_token_ceiling": lock["prompt_token_ceiling"]},
              "max_attempts_per_case": lock["max_attempts_per_case"], "runs": runs,
              "observed_calls": 0, "observed_prompt_tokens": 0, "observed_completion_tokens": 0,
              "usage_missing_calls": 0, "call_records": all_calls, "model_errors": model_errors}

    def save_partial():
        result["model_response_versions"] = sorted({call.get("response_model") for call in all_calls
                                                     if call.get("response_model")})
        result["observed_calls"] = len(all_calls)
        result["observed_prompt_tokens"] = sum((call.get("usage") or {}).get("prompt_tokens", 0)
                                                for call in all_calls)
        result["observed_completion_tokens"] = sum((call.get("usage") or {}).get("completion_tokens", 0)
                                                   for call in all_calls)
        result["usage_missing_calls"] = sum(1 for call in all_calls
                                            if not isinstance((call.get("usage") or {}).get("prompt_tokens"), int)
                                            or not isinstance((call.get("usage") or {}).get("completion_tokens"), int))
        prompt_tokens = result["observed_prompt_tokens"]
        completion_tokens = result["observed_completion_tokens"]
        missing = result["usage_missing_calls"]
        peak_cost = ((prompt_tokens * lock["input_peak_usd_per_million"] +
                      completion_tokens * lock["output_peak_usd_per_million"]) / 1_000_000)
        per_request_reserve = ((lock["prompt_token_ceiling"] * lock["input_peak_usd_per_million"] +
                                lock["max_output_tokens"] * lock["output_peak_usd_per_million"]) / 1_000_000)
        result["peak_list_price_estimate_usd"] = round(peak_cost, 8)
        result["missing_usage_worst_case_reserve_usd"] = round(missing * per_request_reserve, 8)
        result["estimated_max_charge_usd"] = round(peak_cost + missing * per_request_reserve, 8)
        result["elapsed_seconds"] = round(time.perf_counter() - run_started, 3)
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    abort = False
    for backend, tenant_id, engine in arms:
        try:
            identity = engine.read_data_identity(tenant_id=tenant_id)
            if identity != (manifest["data_version"], manifest["data_fingerprint"]):
                raise RuntimeError(f"{backend}/{tenant_id} dataset identity mismatch")
            core = None
            details = []
            for case in preflight["cases"]:
                planner = OpenAICompatiblePlanner(settings,
                    max_prompt_bytes_per_call=lock["prompt_byte_cap"])
                core = InsightPilot(planner, database, query_engine=engine, tenant_id=tenant_id)
                case_started = time.perf_counter()
                actual = core.analyze(case["question"], tenant_id=tenant_id)
                calls = actual.get("model_calls", [])
                if len(calls) > lock["max_attempts_per_case"]:
                    raise RuntimeError("planner exceeded frozen per-case request cap")
                for call in calls:
                    usage = call.get("usage") or {}
                    if (isinstance(usage.get("prompt_tokens"), int) and
                            usage["prompt_tokens"] > lock["prompt_token_ceiling"]):
                        raise RuntimeError("provider prompt token usage exceeded frozen budget ceiling")
                    if (isinstance(usage.get("completion_tokens"), int) and
                            usage["completion_tokens"] > lock["max_output_tokens"]):
                        raise RuntimeError("provider completion token usage exceeded frozen output ceiling")
                    all_calls.append({**call, "backend": backend, "tenant_id": tenant_id,
                                      "case_id": case["case_id"]})
                score = compare(case, preflight["gold"][case["case_id"]], actual)
                details.append({**score, "model_calls": len(calls), "call_usage": calls,
                                "elapsed_ms": round((time.perf_counter() - case_started) * 1000, 3),
                                "model_latency_ms": actual.get("model_latency_ms"),
                                "terminal_stage": actual.get("stage"), "terminal_reason": actual.get("reason"),
                                "failure_detail": actual.get("failure_detail"), "actual_plan": actual.get("plan")})
                if actual.get("status") == "failed" and actual.get("stage") == "planning":
                    model_errors.append({"backend": backend, "tenant_id": tenant_id,
                                         "case_id": case["case_id"],
                                         "terminal_reason": actual.get("reason"),
                                         "failure_detail": actual.get("failure_detail")})
                    abort = True
                current_summary = _summarize(backend, tenant_id, details, preflight["gold"])
                if runs and runs[-1]["backend"] == backend and runs[-1]["tenant_id"] == tenant_id:
                    runs[-1] = current_summary
                else:
                    runs.append(current_summary)
                save_partial()
                if abort:
                    result["status"] = "aborted_model_error"
                    save_partial()
                    return result
            runs[-1] = _summarize(backend, tenant_id, details, preflight["gold"])
        finally:
            close = getattr(engine, "close", None)
            if close:
                close()
    result["status"] = "completed"
    result["elapsed_seconds"] = round(time.perf_counter() - run_started, 3)
    save_partial()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true",
                        help="validate lock, prompt size and budget without completion requests")
    args = parser.parse_args()
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    settings = load_settings(ROOT)
    try:
        preflight = validate_preflight(settings, lock, include_provider=True)
        if not os.environ.get("INSIGHTPILOT_IP4_APP_DSN"):
            raise RuntimeError("test-only INSIGHTPILOT_IP4_APP_DSN is required")
    except Exception as exc:
        raise SystemExit(f"Preflight blocked; no model completion sent ({type(exc).__name__}: {exc})") from None
    report = {"preflight": "passed", "completion_requests_sent": 0,
              "model": settings.model, "response_model_expected": lock["expected_response_model"],
              "provider_balance": preflight["provider_balance"],
              "case_runs": lock["case_count"] * lock["backend_runs"],
              "maximum_api_calls": lock["case_count"] * lock["backend_runs"] * lock["max_attempts_per_case"],
              "worst_case_reserve_usd": lock["worst_case_reserve_usd"],
              "global_remaining_before_usd": preflight["ledger_remaining_usd"],
              "global_remaining_after_reserve_usd": round(
                  preflight["ledger_remaining_usd"] - lock["worst_case_reserve_usd"], 8),
              "measured_max_prompt_bytes": lock["measured_max_prompt_bytes"],
              "prompt_byte_cap": lock["prompt_byte_cap"]}
    if args.preflight_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result = run(lock, preflight)
    print(json.dumps({key: value for key, value in result.items() if key not in ("runs", "call_records")},
                     ensure_ascii=False, indent=2))
    for run_row in result["runs"]:
        print(f"{run_row['backend']} {run_row['tenant_id'] or '-'}: "
              f"{run_row['passed']}/{run_row['total']} (" + ", ".join(
                  f"{domain} {item['passed']}/{item['total']}" for domain, item in run_row["by_domain"].items()) + ")")
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
