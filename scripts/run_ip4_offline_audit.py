"""Run deterministic IP4 rules, verifier ablation, retrieval recall, and DB parity checks."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from data.multidomain_seed import create_multidomain_database
from insightpilot.agent import InsightPilot, _preflight
from insightpilot.enterprise_evaluate import _compare, _compare_without_verification, _without_verification
from insightpilot.metrics import retrieve_metrics
from insightpilot.planner import _MAX_CALLS_PER_ANALYSIS
from insightpilot.query_engine import PostgresQueryEngine
from insightpilot.rule_baseline import RulePlanner


BUNDLE = ROOT / "benchmarks/ip4-offline-prep-v1"
DEFAULT_DATABASE = ROOT / "data/insightpilot-ip4-v13.sqlite"
DEFAULT_OUTPUT = ROOT / "reports/ip4-offline-prep-v1-audit.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_cases(manifest: dict, bundle: Path) -> tuple[list[dict], dict[str, dict]]:
    cases, gold_by_id = [], {}
    for domain, paths in manifest["domains"].items():
        blind_path = bundle / paths["blind_path"]
        gold_path = bundle / paths["gold_path"]
        if _sha(blind_path) != paths["blind_sha256"] or _sha(gold_path) != paths["gold_sha256"]:
            raise ValueError(f"Frozen blind/gold hash mismatch for {domain}")
        domain_cases = [json.loads(line) for line in blind_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        domain_gold = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if any(set(row) != {"case_id", "question"} for row in domain_cases):
            raise ValueError("Agent-visible inputs contain fields beyond case_id and question")
        if {row["case_id"] for row in domain_cases} != {row["case_id"] for row in domain_gold}:
            raise ValueError(f"Frozen blind/gold IDs differ for {domain}")
        if any(row["domain"] != domain for row in domain_gold):
            raise ValueError(f"Gold domain mismatch for {domain}")
        cases.extend(domain_cases)
        gold_by_id.update({row["case_id"]: row for row in domain_gold})
    if len(cases) != manifest["case_count"] or len(gold_by_id) != len(cases):
        raise ValueError("Frozen case count does not match manifest")
    return cases, gold_by_id


def _group_summary(rows: list[dict], cases: list[dict], gold: dict[str, dict]) -> dict:
    by_domain = {}
    for domain in sorted({gold[row["case_id"]]["domain"] for row in rows}):
        subset = [row for row in rows if gold[row["case_id"]]["domain"] == domain]
        by_domain[domain] = {
            "passed": sum(row["passed"] for row in subset), "total": len(subset),
            "failures": [row for row in subset if not row["passed"]],
            "answerable_count": sum(gold[row["case_id"]]["expected_status"] == "answered" for row in subset),
            "false_refusals": [row["case_id"] for row in subset
                               if gold[row["case_id"]]["expected_status"] == "answered"
                               and row.get("actual_status") in ("refused", "clarify")],
            "false_accepts": [row["case_id"] for row in subset
                              if gold[row["case_id"]]["expected_status"] != "answered"
                              and row.get("actual_status") == "answered"],
        }
    expected_refusals = [row for row in rows if gold[row["case_id"]]["expected_status"] == "refused"]
    answerable = [row for row in rows if gold[row["case_id"]]["expected_status"] == "answered"]
    return {
        "passed": sum(row["passed"] for row in rows), "total": len(rows),
        "strict_accuracy": round(sum(row["passed"] for row in rows) / len(rows), 4) if rows else None,
        "by_domain": by_domain,
        "refusal_metrics": {
            "expected_refusals": len(expected_refusals),
            "correct_refusals": sum(row.get("actual_status") == "refused" and row.get("reason_ok", True)
                                     for row in expected_refusals),
            "false_refusals_on_answerable": [row["case_id"] for row in answerable
                                               if row.get("actual_status") in ("refused", "clarify")],
            "false_accepts_on_refusal_cases": [row["case_id"] for row in expected_refusals
                                                 if row.get("actual_status") == "answered"],
            "guard_false_accepts_including_clarify_cases": [row["case_id"] for row in rows
                if gold[row["case_id"]]["expected_status"] != "answered" and row.get("actual_status") == "answered"],
        },
        "failures": [row for row in rows if not row["passed"]],
        "case_results": rows,
    }


def _run_arm(cases: list[dict], gold: dict[str, dict], database: Path,
             query_engine=None) -> tuple[dict, dict[str, dict]]:
    core = InsightPilot(RulePlanner(), database, query_engine=query_engine, tracer=None)
    results, compared = {}, []
    for case in cases:
        result = core.analyze(case["question"])
        results[case["case_id"]] = result
        compared.append({**_compare(case, gold[case["case_id"]], result),
                         "actual_status": result.get("status"), "actual_reason": result.get("reason"),
                         "actual_stage": result.get("stage")})
    return _group_summary(compared, cases, gold), results


def _retrieval_coverage(cases: list[dict], gold: dict[str, dict]) -> dict:
    domains = {}
    for domain in sorted({gold[case["case_id"]]["domain"] for case in cases}):
        answerable = [case for case in cases if gold[case["case_id"]]["domain"] == domain
                      and gold[case["case_id"]]["expected_status"] == "answered"]
        misses = []
        for case in answerable:
            candidate_ids = {metric.metric_id for metric in retrieve_metrics(case["question"])}
            if gold[case["case_id"]]["metric"] not in candidate_ids:
                misses.append(case["case_id"])
        domains[domain] = {"metric_candidate_recall": round((len(answerable) - len(misses)) / len(answerable), 4),
                           "covered": len(answerable) - len(misses), "answerable": len(answerable),
                           "misses": misses}
    return {"method": "retrieve_metrics candidate coverage; no planner/model call",
            "by_domain": domains,
            "total_covered": sum(item["covered"] for item in domains.values()),
            "total_answerable": sum(item["answerable"] for item in domains.values())}


def _run_no_verifier(cases: list[dict], gold: dict[str, dict], database: Path) -> dict:
    rows = []
    for case in cases:
        result = _without_verification(RulePlanner(), database, case["question"])
        scored, adjustment = _compare_without_verification(case, gold[case["case_id"]], result)
        rows.append({**scored, "actual_status": result.get("status"),
                     "actual_reason": result.get("reason"), "actual_stage": result.get("stage"),
                     "independent_verifier_invoked": False,
                     **({"audit_adjustment": adjustment} if adjustment else {})})
    return _group_summary(rows, cases, gold)


def _build_or_check_database(database: Path, manifest: dict) -> dict:
    if not database.exists():
        database.parent.mkdir(parents=True, exist_ok=True)
        data = manifest["data"]
        create_multidomain_database(database, seed=data["marketing_seed"], retail_seed=data["retail_seed"],
                                    order_count=data["order_count"])
    if _sha(database) != manifest["data"]["sqlite_sha256"]:
        raise ValueError("SQLite dataset file hash differs from the frozen offline manifest")
    connection = sqlite3.connect(database)
    try:
        identity = dict(connection.execute("SELECT key,value FROM data_meta"))
    finally:
        connection.close()
    expected_identity = {
        "data_version": manifest["data"]["data_version"],
        "data_fingerprint": manifest["data"]["data_fingerprint"],
        "seed": str(manifest["data"]["marketing_seed"]),
        "retail_seed": str(manifest["data"]["retail_seed"]),
        "order_count": str(manifest["data"]["order_count"]),
        "snapshot_at": manifest["data"]["snapshot_at"],
    }
    for key, value in expected_identity.items():
        if identity.get(key) != value:
            raise ValueError(f"SQLite dataset identity mismatch: {key}")
    return identity


def _require_local_postgres_dsn(dsn: str) -> None:
    parsed = urlsplit(dsn)
    host = parsed.hostname
    if not host:
        raise ValueError("Offline IP4 audit requires a PostgreSQL URL with a loopback host")
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host.casefold() == "localhost"
    if not local:
        raise ValueError("Offline IP4 audit refuses remote PostgreSQL hosts")


def run(bundle: Path = BUNDLE, database: Path = DEFAULT_DATABASE, output: Path | None = None,
        postgres_dsn: str | None = None, tenant_ids: tuple[str, str] = ("tenant-a", "tenant-b")) -> dict:
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases, gold = _load_cases(manifest, bundle)
    identity = _build_or_check_database(database, manifest)
    sqlite_arm, sqlite_results = _run_arm(cases, gold, database)

    no_verifier = _run_no_verifier(cases, gold, database)
    arms = ("retrieval_on", "retrieval_off")
    per_request_usd = 3000 * 0.30 / 1_000_000 + 1024 * 1.20 / 1_000_000
    planned_first_pass_calls = len(cases) * len(arms)
    budgeted_max_calls = planned_first_pass_calls * _MAX_CALLS_PER_ANALYSIS
    preflight_blocked_ids = [case["case_id"] for case in cases if _preflight(case["question"])]
    call_cap_after_preflight = ((len(cases) - len(preflight_blocked_ids)) * len(arms)
                                * _MAX_CALLS_PER_ANALYSIS)
    ledger = json.loads((ROOT / "reports/model-experiment-cost-ledger.json").read_text(encoding="utf-8"))
    legacy_audit = json.loads((ROOT / "reports/cost-audit-v9.json").read_text(encoding="utf-8"))
    retrieval_eval_path = ROOT / "reports/ip4-retrieval-ablation-model.json"
    retrieval_eval = (json.loads(retrieval_eval_path.read_text(encoding="utf-8"))
                      if retrieval_eval_path.exists() else None)
    project_cap = float(ledger["cap_usd"])
    latest_remaining = float(ledger["remaining_global_cap_after_ip4_v13_model_usd"])
    planned_reserve = round(budgeted_max_calls * per_request_usd, 8)
    usage_estimate_after_v8 = legacy_audit["named_usage_estimates_after_v8_usd"] + sum(
        ledger[key]["peak_list_price_estimate_usd"] for key in (
            "v9_independent_holdout", "v10_independent_holdout", "v11_independent_holdout",
            "v12_independent_holdout", "ip4_v3_model_run"))
    older_unreconciled = legacy_audit["prior_reserve_or_unreconciled_usage_usd"]
    prior_v13_reserve = ledger["ip4_v13_model_evaluation"]["historical_uncertain_reserve_usd"]
    current_v13_peak_estimate = ledger["ip4_v13_model_evaluation"]["peak_list_price_estimate_usd"]
    retrieval_ledger = ledger.get("ip4_retrieval_ablation_model", {})
    current_retrieval_peak_estimate = retrieval_ledger.get("peak_price_estimate_usd", 0.0)
    booked_breakdown = round(usage_estimate_after_v8 + older_unreconciled + prior_v13_reserve
                             + current_v13_peak_estimate + current_retrieval_peak_estimate, 8)
    if abs(booked_breakdown - (project_cap - latest_remaining)) > 1e-8:
        raise ValueError("Current cumulative peak/reserve breakdown does not reconcile to the ledger")
    retrieval_status = "pending_user_authorization_and_provider_transport"
    retrieval_summary = None
    if retrieval_eval is not None:
        if retrieval_eval.get("suite_id") != manifest["suite_id"]:
            raise ValueError("Retrieval model report suite does not match the frozen IP4 bundle")
        if retrieval_eval.get("status") == "completed":
            report_arms = {item.get("arm"): item for item in retrieval_eval.get("arms", [])}
            ledger_arms = retrieval_ledger.get("passed", {})
            for arm_name in arms:
                item = report_arms.get(arm_name)
                if not item or len(item.get("cases", [])) != len(cases):
                    raise ValueError(f"Completed retrieval model report is incomplete: {arm_name}")
                observed = f"{item.get('passed')}/{item.get('total')}"
                if ledger_arms.get(arm_name) != observed:
                    raise ValueError(f"Retrieval model report and cost ledger differ: {arm_name}")
            if abs(float(retrieval_eval.get("observed_peak_price_estimate_usd", -1))
                   - float(current_retrieval_peak_estimate)) > 1e-8:
                raise ValueError("Retrieval model report cost does not match the ledger")
            retrieval_status = "completed"
            retrieval_summary = {
                "status": retrieval_status,
                "observed_api_calls": retrieval_eval["observed_api_calls"],
                "maximum_api_calls": retrieval_eval["maximum_api_calls"],
                "reserve_usd": retrieval_eval["reserve_usd"],
                "peak_price_estimate_usd": retrieval_eval["observed_peak_price_estimate_usd"],
                "remaining_after_usd": retrieval_eval["remaining_after_usd"],
                "arms": {name: {key: report_arms[name][key] for key in
                                ("passed", "total", "answerable_passed", "answerable_total",
                                 "guard_passed", "guard_total")}
                         for name in arms},
            }
    postgres_arms = {}
    sqlite_parity = {}
    result_parity = {}
    if postgres_dsn:
        _require_local_postgres_dsn(postgres_dsn)
        for tenant in tenant_ids:
            engine = PostgresQueryEngine(postgres_dsn, tenant_id=tenant)
            try:
                arm, pg_results = _run_arm(cases, gold, database, query_engine=engine)
                postgres_arms[tenant] = arm
                parity_failures = []
                for case in cases:
                    case_id = case["case_id"]
                    if not _result_equal(sqlite_results[case_id], pg_results[case_id]):
                        parity_failures.append(case_id)
                sqlite_parity[tenant] = {"passed": len(cases) - len(parity_failures),
                                         "total": len(cases), "failures": parity_failures}
                result_parity[tenant] = {"passed": len(cases) - len(parity_failures),
                                         "total": len(cases), "failures": parity_failures}
            finally:
                engine.close()

    all_parity_passed = all(item["passed"] == item["total"] for item in result_parity.values())
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": ("completed" if len(postgres_arms) == len(tenant_ids) and all_parity_passed
                   else "completed_with_database_mismatch" if postgres_arms
                   else "pending_postgresql"),
        "suite_id": manifest["suite_id"], "manifest_sha256": _sha(manifest_path),
        "data": {key: identity.get(key) for key in
                 ("data_version", "data_fingerprint", "seed", "retail_seed", "snapshot_at")},
        "model_calls": 0, "model_requests_sent": 0, "estimated_model_cost_usd": 0.0,
        "sample_limit": manifest["sample_limit"],
        "ruleplanner_sqlite": sqlite_arm,
        "ruleplanner_without_independent_verifier_sqlite": no_verifier,
        "metric_retrieval_candidate_coverage": _retrieval_coverage(cases, gold),
        "postgresql_ruleplanner_by_tenant": postgres_arms,
        "postgresql_sqlite_row_parity": sqlite_parity,
        "postgresql_sqlite_result_parity": result_parity,
        "interpretation": "完成状态表示双后端执行和逐题一致性检查完成；规则基线错误作为评测结果保留，不折算为执行器通过。",
        "model_ablation": {
            "status": retrieval_status,
            "comparison": "semantic metric retrieval on vs off; verifier behavior is measured separately on deterministic RulePlanner output",
            "model_backend_scope": "SQLite only; PostgreSQL remains covered by deterministic RulePlanner and parity arms",
            "planned_first_pass_requests": planned_first_pass_calls,
            "retrieval_arms": {arm: len(cases) for arm in arms},
            "max_attempts_per_analysis": _MAX_CALLS_PER_ANALYSIS,
            "maximum_api_calls_conservative": budgeted_max_calls,
            "maximum_api_calls_after_current_preflight": (
                retrieval_eval["maximum_api_calls"] if retrieval_summary else call_cap_after_preflight),
            "preflight_blocked_case_ids_per_arm": preflight_blocked_ids,
            "cases": {domain: item["case_count"] for domain, item in manifest["domains"].items()},
            "input_tokens_per_request_cap": 3000,
            "output_tokens_per_request_cap": 1024,
            "per_request_peak_price_upper_bound_usd": round(per_request_usd, 8),
            "conservative_reserve_usd": (
                retrieval_eval["reserve_usd"] if retrieval_summary else planned_reserve),
            "authorized_worst_case_reserve_usd": planned_reserve,
            "project_cap_usd": project_cap,
            "latest_ledger_remaining_before_reserve_usd": (
                retrieval_eval["remaining_before_usd"] if retrieval_summary else latest_remaining),
            "post_model_evaluation_ledger_remaining_usd": latest_remaining,
            "ledger_implied_booked_peak_estimates_and_reserves_usd": round(project_cap - latest_remaining, 8),
            "cumulative_accounting_breakdown": {
                "usage_backed_peak_price_estimates_usd": round(usage_estimate_after_v8, 8),
                "older_reserve_or_unreconciled_usage_usd": older_unreconciled,
                "prior_v13_unknown_exposure_reserve_usd": prior_v13_reserve,
                "current_v13_no_response_attempts_peak_estimate_usd": current_v13_peak_estimate,
                "retrieval_ablation_peak_price_estimate_usd": current_retrieval_peak_estimate,
                "provider_invoice_actuals_available": False,
            },
            "balance_after_planned_reserve_usd": (
                None if retrieval_summary else round(latest_remaining - planned_reserve, 8)),
            "provider_invoice_actuals_verified": False,
            "unresolved_prior_v13_reserve_usd": ledger["ip4_v13_model_evaluation"]["historical_uncertain_reserve_usd"],
            "combined_v13_unreceipted_worst_case_usd": round(prior_v13_reserve + current_v13_peak_estimate, 8),
            "latest_v13_attempts": {
                "attempted_requests": ledger["ip4_v13_model_evaluation"]["model_calls"],
                "provider_responses_received": 0,
                "confirmed_actual_cost_usd": None,
                "peak_price_estimate_usd": ledger["ip4_v13_model_evaluation"]["peak_list_price_estimate_usd"],
            },
            "latest_retrieval_model_run": retrieval_summary,
            "price_basis": "existing IP4 v13 preregistration at $0.30/M uncached input and $1.20/M output",
            "request_payload": {
                "common_system_prompt": "fixed planner instructions and JSON output contract",
                "user_payload_fields": ["question", "candidate_metrics", "allowed_metric_ids"],
                "retrieval_on": "frozen synthetic question, retrieved static metric definitions and their IDs",
                "retrieval_off": "same question, empty candidate_metrics and all allowed metric IDs",
                "includes_query_results_or_database_rows": False,
                "includes_gold_or_expected_answers": False,
                "synthetic_only": True,
            },
            "replay_note": "No live-model verifier on/off replay is included. The deterministic no-verifier arm uses RulePlanner and adds no API calls.",
            "data_artifacts": {
                "bundle_root": str(bundle),
                "agent_visible_files": manifest["agent_input_contract"]["files"],
                "scoring_gold_files": [item["gold_path"] for item in manifest["domains"].values()],
                "data_fingerprint": manifest["data"]["data_fingerprint"],
                "sqlite_sha256": manifest["data"]["sqlite_sha256"],
            },
        },
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _rows_equal(left: list[dict], right: list[dict]) -> bool:
    def normalize(rows):
        return sorted(({key: float(value) if hasattr(value, "as_tuple") else value for key, value in row.items()}
                       for row in rows), key=lambda row: str(row.get("group_value", "")))
    left, right = normalize(left), normalize(right)
    if len(left) != len(right):
        return False
    for lhs, rhs in zip(left, right):
        if lhs.keys() != rhs.keys():
            return False
        for key in lhs:
            a, b = lhs[key], rhs[key]
            if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)):
                if abs(a - b) > 1e-6:
                    return False
            elif a != b:
                return False
    return True


def _result_equal(left: dict, right: dict) -> bool:
    for key in ("status", "stage", "reason"):
        if left.get(key) != right.get(key):
            return False
    if left.get("status") != "answered":
        return True
    if (left.get("metric", {}).get("id") != right.get("metric", {}).get("id") or
            left.get("plan") != right.get("plan") or
            left.get("data_version") != right.get("data_version") or
            left.get("data_fingerprint") != right.get("data_fingerprint")):
        return False
    return _rows_equal(left.get("rows", []), right.get("rows", []))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=BUNDLE)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--postgres-dsn", default=os.environ.get("INSIGHTPILOT_IP4_APP_DSN"))
    parser.add_argument("--tenant-a", default="tenant-a")
    parser.add_argument("--tenant-b", default="tenant-b")
    args = parser.parse_args()
    report = run(args.bundle, args.database, args.output, args.postgres_dsn,
                 (args.tenant_a, args.tenant_b))
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ("ruleplanner_sqlite", "ruleplanner_without_independent_verifier_sqlite",
                                     "postgresql_ruleplanner_by_tenant")}, ensure_ascii=False, indent=2))
    if report["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
