"""Fail-closed, offline-only IP-4 v14 candidate preflight and scorer."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_ip4_v14_offline.py"
EGRESS_WORKER = ROOT / "scripts/ip4_v14_egress_worker.py"
DEFAULT_LOCK = ROOT / "reports/ip4-v14-offline-only-lock-2026-11-15.json"

# Locked separately so future changes to wording fail preflight.
V14_SYSTEM_PROMPT = (
    "Select allowed metric IDs for this synthetic analytics question. "
    "Return JSON with only allowed_metric_ids. Never return SQL, query results, "
    "database rows, private data, file paths, or gold."
)

SOURCE_FILES = {
    "candidate_builder": "data/build_ip4_v14_candidate.py",
    "case_generator": "data/build_ip4_v14_candidate.py",
    "dataset_builder": "data/multidomain_seed.py",
    "refund_oracle": "data/build_enterprise_v5.py",
    "row_parity": "data/build_ip4_holdout.py",
    "marketing_oracle": "data/build_marketing_roi_holdout.py",
    "data_identity": "insightpilot/freeze.py",
    "marketing_calculation": "insightpilot/marketing_oracle.py",
    "planner_prompt_source": "insightpilot/planner.py",
    "metric_catalog": "insightpilot/metrics.py",
    "readonly_runner": "insightpilot/runner.py",
    "query_compiler_runtime": "insightpilot/sql.py",
    "scorer": "insightpilot/ip4_v14_evaluate.py",
    "shared_scorer": "insightpilot/ip4_evaluate.py",
    "agent_runtime": "insightpilot/agent.py",
    "rule_planner": "insightpilot/rule_baseline.py",
    "query_engine": "insightpilot/query_engine.py",
    "offline_runner_module": "insightpilot/ip4_v14_offline.py",
    "egress_worker": "scripts/ip4_v14_egress_worker.py",
}

_FORBIDDEN_KEY_PARTS = ("gold", "sql", "query_result", "queryresult", "database_row",
                        "private_data", "file_path", "filepath", "pathname", "file_name")
_METRIC_ID = re.compile(r"^[a-z][a-z0-9_]{1,80}$")
_PATH_VALUE = re.compile(r"^(?:[a-z]:[\\/]|\\\\|/[^/\s])", re.IGNORECASE)
_PAYLOAD_KEYS = {"question", "allowed_metric_ids"}


class PreflightError(ValueError):
    """A candidate, source, or payload failed a fail-closed check."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prompt_sha256() -> str:
    return hashlib.sha256(V14_SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def source_hashes() -> dict[str, str]:
    return {name: sha256_file(ROOT / relative) for name, relative in SOURCE_FILES.items()}


def create_offline_lock(candidate: Path) -> dict:
    """Create a review-only lock in memory; it grants no external-call authority."""
    if candidate.is_symlink():
        raise PreflightError("candidate symlink is not accepted")
    root = candidate.resolve(strict=True)
    if not root.is_dir() or root == ROOT or ROOT in root.parents:
        raise PreflightError("candidate must be a real directory outside the public repository")
    names = ("manifest.json", "blind.jsonl", "gold.jsonl", "synthetic.sqlite")
    paths = {name: root / name for name in names}
    if any(path.is_symlink() or not path.is_file() for path in paths.values()):
        raise PreflightError("candidate is missing a regular, non-symlink artifact")
    manifest = json.loads(paths["manifest.json"].read_text(encoding="utf-8"))
    if (manifest.get("status") != "candidate_pending_owner_review" or
            manifest.get("synthetic_only") is not True or manifest.get("model_calls") != 0):
        raise PreflightError("candidate is not a zero-call synthetic owner-review candidate")
    return {
        "schema_version": 1,
        "status": "offline_only_not_authorized",
        "external_calls_authorized": False,
        "network_boundary": "code_has_no_network_client_os_egress_not_blocked",
        "suite_id": manifest["suite_id"],
        "candidate_manifest_sha256": sha256_file(paths["manifest.json"]),
        "candidate_sha256": {name: sha256_file(paths[name]) for name in names[1:]},
        "data_version": manifest["data_version"],
        "data_fingerprint": manifest["data_fingerprint"],
        "source_sha256": source_hashes(),
        "prompt_sha256": prompt_sha256(),
        "outbound_fields": ["question", "allowed_metric_ids"],
        "gold_access_boundary": "parent evaluator only; child receives sanitized records on stdin",
        "network_calls": 0,
        "model_calls": 0,
    }


def _reject_sensitive_keys(value: object, *, location: str = "payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9_]", "", str(key).casefold())
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                raise PreflightError(f"forbidden field at {location}: {key}")
            if normalized in {"path", "file", "filename", "source", "directory", "uri"}:
                raise PreflightError(f"path/file field forbidden at {location}: {key}")
            _reject_sensitive_keys(item, location=f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_sensitive_keys(item, location=f"{location}[{index}]")
    elif isinstance(value, str) and _PATH_VALUE.match(value.strip()):
        raise PreflightError(f"path value forbidden at {location}")


def validate_payload(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        _reject_sensitive_keys(payload)
        raise PreflightError("payload must contain exactly question and allowed_metric_ids")
    _reject_sensitive_keys(payload)
    question = payload.get("question")
    metric_ids = payload.get("allowed_metric_ids")
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise PreflightError("question must be non-empty synthetic text under 4000 characters")
    if not isinstance(metric_ids, list) or not metric_ids or len(metric_ids) > 8:
        raise PreflightError("allowed_metric_ids must be a non-empty short list")
    if (len(set(metric_ids)) != len(metric_ids) or
            any(not isinstance(item, str) or not _METRIC_ID.fullmatch(item) for item in metric_ids)):
        raise PreflightError("allowed_metric_ids contains invalid or duplicate IDs")
    return {"question": question, "allowed_metric_ids": metric_ids}


def validate_lock(lock: dict) -> None:
    required = {"schema_version", "status", "external_calls_authorized", "network_boundary",
                "suite_id", "candidate_manifest_sha256", "candidate_sha256", "data_version",
                "data_fingerprint", "source_sha256", "prompt_sha256", "outbound_fields",
                "gold_access_boundary", "network_calls", "model_calls"}
    if not isinstance(lock, dict) or set(lock) != required:
        raise PreflightError("offline lock schema mismatch")
    if lock.get("schema_version") != 1:
        raise PreflightError("unsupported offline lock schema")
    if lock.get("status") != "offline_only_not_authorized":
        raise PreflightError("lock does not explicitly prohibit external calls")
    if (lock.get("external_calls_authorized") is not False or
            lock.get("network_boundary") != "code_has_no_network_client_os_egress_not_blocked"):
        raise PreflightError("offline tool boundary or authorization mismatch")
    if (not isinstance(lock.get("candidate_sha256"), dict) or
            set(lock["candidate_sha256"]) != {"blind.jsonl", "gold.jsonl", "synthetic.sqlite"} or
            not isinstance(lock.get("source_sha256"), dict) or
            set(lock["source_sha256"]) != set(SOURCE_FILES)):
        raise PreflightError("offline lock hash schema mismatch")
    hashes = [lock.get("candidate_manifest_sha256"), lock.get("prompt_sha256"),
              *lock["candidate_sha256"].values(), *lock["source_sha256"].values()]
    if any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
           for value in hashes):
        raise PreflightError("offline lock hash schema mismatch")
    if (lock.get("network_calls") != 0 or lock.get("model_calls") != 0 or
            lock.get("outbound_fields") != ["question", "allowed_metric_ids"] or
            lock.get("suite_id") != "insightpilot-ip4-v14-private-candidate" or
            not isinstance(lock.get("data_version"), str) or
            not re.fullmatch(r"[a-f0-9]{64}", str(lock.get("data_fingerprint")))):
        raise PreflightError("offline lock policy fields mismatch")
    if lock.get("prompt_sha256") != prompt_sha256():
        raise PreflightError("prompt hash mismatch")
    if lock.get("source_sha256") != source_hashes():
        raise PreflightError("source hash mismatch")


def _load_candidate(candidate: Path, lock: dict) -> tuple[dict, list[dict], list[dict]]:
    if candidate.is_symlink():
        raise PreflightError("candidate symlink is not accepted")
    root = candidate.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise PreflightError("candidate must be a real directory")
    if root == ROOT or ROOT in root.parents:
        raise PreflightError("candidate must stay outside the public repository")
    required = ("manifest.json", "blind.jsonl", "gold.jsonl", "synthetic.sqlite")
    files = {name: root / name for name in required}
    if any(path.is_symlink() or not path.is_file() for path in files.values()):
        raise PreflightError("candidate is missing a regular, non-symlink artifact")
    if lock.get("candidate_manifest_sha256") != sha256_file(files["manifest.json"]):
        raise PreflightError("candidate manifest hash mismatch")
    expected_files = lock.get("candidate_sha256", {})
    if any(expected_files.get(name) != sha256_file(files[name]) for name in required[1:]):
        raise PreflightError("candidate dataset hash mismatch")
    manifest = json.loads(files["manifest.json"].read_text(encoding="utf-8"))
    if (manifest.get("status") != "candidate_pending_owner_review" or
            manifest.get("synthetic_only") is not True or manifest.get("model_calls") != 0):
        raise PreflightError("candidate is not a zero-call synthetic owner-review candidate")
    if lock.get("suite_id") != manifest.get("suite_id"):
        raise PreflightError("candidate suite ID mismatch")
    if lock.get("data_version") != manifest.get("data_version") or \
            lock.get("data_fingerprint") != manifest.get("data_fingerprint"):
        raise PreflightError("dataset identity mismatch")
    blind = [json.loads(line) for line in files["blind.jsonl"].read_text(encoding="utf-8").splitlines()
             if line.strip()]
    if (len(blind) != manifest.get("case_count") or
            len({row.get("case_id") for row in blind}) != len(blind)):
        raise PreflightError("candidate question IDs or count differ")
    return manifest, blind, []


def _run_egress_worker(payload: dict) -> dict:
    """Pass only sanitized JSON over stdin to a file/path-blind child process."""
    payload = validate_payload(payload)
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL")
           if key in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory(prefix="ip4-v14-egress-") as cwd:
        result = subprocess.run(
            [sys.executable, str(EGRESS_WORKER)],
            input=json.dumps(payload, ensure_ascii=False), text=True, encoding="utf-8",
            capture_output=True, check=False, cwd=cwd, env=env, timeout=10,
        )
    if result.returncode != 0:
        raise PreflightError("isolated egress worker rejected payload")
    try:
        sanitized = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise PreflightError("isolated egress worker returned invalid JSON") from None
    if validate_payload(sanitized) != payload:
        raise PreflightError("isolated egress worker changed payload")
    return sanitized


def _payload_records(blind: list[dict], manifest: dict) -> list[dict]:
    from data.build_ip4_v14_candidate import generate_cases

    generated = {row["case_id"]: row for row in generate_cases(manifest["seed"])}
    records = []
    for case in blind:
        if set(case) != {"case_id", "domain", "question"}:
            raise PreflightError("blind case schema differs from synthetic candidate contract")
        expected = generated.get(case["case_id"])
        if (expected is None or expected["domain"] != case["domain"] or
                expected["question"] != case["question"]):
            raise PreflightError("blind question differs from the locked synthetic generator")
        records.append(validate_payload({"question": case["question"],
                                         "allowed_metric_ids": expected["candidate_metric_ids"]}))
    if set(generated) != {case["case_id"] for case in blind}:
        raise PreflightError("synthetic generator and blind question IDs differ")
    return records


def verify_result(report: dict, lock: dict) -> dict:
    allowed = {"schema_version", "suite_id", "status", "external_calls_authorized", "network_calls",
               "model_calls", "candidate_sha256", "source_sha256", "prompt_sha256",
               "outbound_contract", "offline_baseline"}
    if not isinstance(report, dict) or set(report) != allowed:
        _reject_sensitive_keys(report)
        raise PreflightError("result report has missing or unexpected fields")
    _reject_sensitive_keys(report)
    if (report["schema_version"] != 1 or report["suite_id"] != lock["suite_id"] or
            report["status"] != "offline_preflight_passed" or
            report["external_calls_authorized"] is not False or
            report["network_calls"] != 0 or report["model_calls"] != 0):
        raise PreflightError("result report is not a zero-call offline result")
    expected_candidate_hashes = {
        "manifest.json": lock["candidate_manifest_sha256"],
        "questions": lock["candidate_sha256"]["blind.jsonl"],
        "sealed_data": lock["candidate_sha256"]["gold.jsonl"],
        "synthetic_database": lock["candidate_sha256"]["synthetic.sqlite"],
    }
    if report["candidate_sha256"] != expected_candidate_hashes:
        raise PreflightError("result candidate hashes differ from lock")
    if (report["source_sha256"] != lock["source_sha256"] or
            report["prompt_sha256"] != lock["prompt_sha256"]):
        raise PreflightError("result source or prompt hashes differ from lock")
    contract = report["outbound_contract"]
    if (not isinstance(contract, dict) or contract.get("fields") != lock["outbound_fields"] or
            contract.get("records") != 22 or
            contract.get("payload_sha256") is None):
        raise PreflightError("result outbound contract is invalid")
    baseline = report["offline_baseline"]
    if not isinstance(baseline, dict):
        raise PreflightError("offline baseline result is malformed")
    by_domain = baseline.get("by_domain")
    if (baseline.get("case_count") != 22 or
            not isinstance(baseline.get("passed"), int) or not 0 <= baseline["passed"] <= 22 or
            baseline.get("mode") != "offline_rule_baseline_no_model_calls" or
            not isinstance(by_domain, dict) or set(by_domain) != {"refunds", "marketing_roi"}):
        raise PreflightError("offline baseline result differs from candidate review evidence")
    if any(not isinstance(row, dict) or not isinstance(row.get("passed"), int) or
           not isinstance(row.get("total"), int) or row["passed"] < 0 or
           row["passed"] > row["total"] for row in by_domain.values()):
        raise PreflightError("offline baseline domain totals do not reconcile")
    if (sum(row["total"] for row in by_domain.values()) != 22 or
            sum(row["passed"] for row in by_domain.values()) != baseline["passed"]):
        raise PreflightError("offline baseline domain totals do not reconcile")
    return report


def run_offline(candidate: Path, lock: dict) -> dict:
    validate_lock(lock)
    manifest, blind, _ = _load_candidate(candidate, lock)
    records = _payload_records(blind, manifest)
    # The child receives no candidate root, gold file, SQL, result rows, or environment secrets.
    sanitized = [_run_egress_worker(record) for record in records]
    payload_bytes = json.dumps(sanitized, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")

    # Gold is opened only after the isolated payload worker has exited.
    root = candidate.resolve(strict=True)
    gold = [json.loads(line) for line in (root / "gold.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    if len(gold) != len(blind) or {row.get("case_id") for row in gold} != {
            row.get("case_id") for row in blind}:
        raise PreflightError("local scorer question/gold IDs differ")

    from .ip4_v14_evaluate import evaluate

    local_result = evaluate(root / "synthetic.sqlite", root / "blind.jsonl",
                            root / "gold.jsonl", root / "manifest.json")
    by_domain = {domain: {"passed": value["passed"], "total": value["total"]}
                 for domain, value in local_result["by_domain"].items()}
    report = {
        "schema_version": 1,
        "suite_id": manifest["suite_id"],
        "status": "offline_preflight_passed",
        "external_calls_authorized": False,
        "network_calls": 0,
        "model_calls": 0,
        "candidate_sha256": {
            "manifest.json": sha256_file(root / "manifest.json"),
            "questions": sha256_file(root / "blind.jsonl"),
            "sealed_data": sha256_file(root / "gold.jsonl"),
            "synthetic_database": sha256_file(root / "synthetic.sqlite"),
        },
        "source_sha256": lock["source_sha256"],
        "prompt_sha256": lock["prompt_sha256"],
        "outbound_contract": {
            "fields": lock["outbound_fields"],
            "records": len(sanitized),
            "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "permission_boundary": "separate stdin/stdout worker receives sanitized synthetic text and metric IDs only",
        },
        "offline_baseline": {
            "mode": local_result["mode"],
            "case_count": local_result["case_count"],
            "passed": local_result["passed"],
            "by_domain": by_domain,
        },
    }
    return verify_result(report, lock)


def write_result(report: dict, output: Path, lock: dict) -> None:
    verify_result(report, lock)
    destination = output.resolve()
    if destination.exists():
        raise PreflightError("result output already exists; refusing overwrite")
    if any(part in destination.name.casefold() for part in _FORBIDDEN_KEY_PARTS):
        raise PreflightError("result output name contains a forbidden field token")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
