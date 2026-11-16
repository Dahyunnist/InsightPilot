"""Single-use, owner-authorized model runner for the private IP-4 v14 candidate."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from .config import Settings
from .ip4_v14_offline import (ROOT, PreflightError, _payload_records,
                              _verify_candidate_artifact_hashes, _run_egress_worker,
                              sha256_file)

MAX_PROVIDER_REQUESTS = 22
MAX_BUDGET_USD = Decimal("0.0468336")
MAX_COMPLETION_TOKENS = 64
REQUEST_TIMEOUT_SECONDS = 30
BUDGET_SNAPSHOT_MAX_AGE_SECONDS = 300
SYSTEM_PROMPT = (
    "For the synthetic analytics question, select relevant metric IDs only from "
    "allowed_metric_ids. Return JSON with exactly selected_metric_ids, a list of "
    "zero or more allowed IDs. Never return SQL, data, explanations, paths, or private content."
)

SOURCE_FILES = {
    "candidate_builder": "data/build_ip4_v14_candidate.py",
    "case_generator": "data/build_ip4_v14_candidate.py",
    "planner": "insightpilot/planner.py",
    "prompt_catalog": "insightpilot/metrics.py",
    "scorer": "insightpilot/ip4_v14_evaluate.py",
    "offline_validator": "insightpilot/ip4_v14_offline.py",
    "model_runner": "insightpilot/ip4_v14_model_runner.py",
    "payload_worker": "scripts/ip4_v14_egress_worker.py",
    "dependency_lock": "requirements.txt",
}

PREREG_FIELDS = {
    "schema_version", "status", "external_calls_authorized", "suite_id", "case_count",
    "candidate_sha256", "data_version", "data_fingerprint", "source_sha256",
    "prompt_sha256", "runtime", "model_id", "provider_origin_sha256",
    "request_policy", "budget_policy",
}
AUTH_FIELDS = {
    "schema_version", "status", "external_calls_authorized", "preregistration_sha256",
    "model_id", "provider_origin_sha256", "max_provider_requests", "max_cost_usd",
    "reserved_usd", "retry_policy", "candidate_hashes_reviewed", "blind_and_gold_reviewed",
    "prompt_and_model_reviewed", "parity_evidence_reviewed", "budget_reviewed",
    "authorized_by", "authorized_at_utc",
}
SNAPSHOT_FIELDS = {
    "schema_version", "project_cap_usd", "spent_usd", "external_reserved_usd", "as_of_utc",
}
RESERVATION_FIELDS = {
    "schema_version", "preregistration_sha256", "authorization_sha256", "reserved_usd",
    "created_at_utc", "status",
}
REPORT_FIELDS = {
    "schema_version", "preregistration_sha256", "candidate_sha256", "model_id", "status",
    "external_calls_authorized", "requests_attempted", "provider_responses_received",
    "model_calls", "retry_count", "reserved_usd", "actual_cost_usd", "cost_status", "cases",
}
CASE_FIELDS = {"case_id", "status", "selected_metric_ids", "latency_ms", "usage"}
HASH64 = re.compile(r"^[a-f0-9]{64}$")


class ModelRunError(ValueError):
    """A fail-closed v14 authorization, candidate, or budget gate rejected the run."""


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _runtime_fingerprint() -> dict:
    packages = sorted({f"{dist.metadata.get('Name', '').casefold()}=={dist.version}"
                       for dist in importlib.metadata.distributions()
                       if dist.metadata.get("Name")})
    details = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "requirements_sha256": sha256_file(ROOT / "requirements.txt"),
        "installed_package_count": len(packages),
        "installed_packages_sha256": hashlib.sha256(_json_bytes(packages)).hexdigest(),
    }
    details["sha256"] = hashlib.sha256(_json_bytes(details)).hexdigest()
    return details


def _source_hashes() -> dict[str, str]:
    return {key: sha256_file(ROOT / path) for key, path in SOURCE_FILES.items()}


def _provider_origin_hash(settings: Settings) -> str:
    parsed = urllib.parse.urlsplit(settings.base_url)
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if ((parsed.scheme != "https" and not local_http) or not parsed.netloc or
            parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ModelRunError("provider endpoint is invalid")
    normalized = urllib.parse.urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(),
                                         parsed.path.rstrip("/"), "", ""))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _candidate_paths(candidate: Path) -> tuple[Path, dict[str, Path]]:
    if candidate.is_symlink():
        raise ModelRunError("candidate symlink is not accepted")
    root = candidate.resolve(strict=True)
    if not root.is_dir() or root == ROOT or ROOT in root.parents:
        raise ModelRunError("candidate must be a real directory outside the repository")
    names = ("manifest.json", "blind.jsonl", "gold.jsonl", "synthetic.sqlite")
    paths = {name: root / name for name in names}
    if any(path.is_symlink() or not path.is_file() for path in paths.values()):
        raise ModelRunError("candidate is missing a regular artifact")
    return root, paths


def _candidate_context(candidate: Path) -> tuple[Path, dict, dict[str, Path], list[dict], list[dict]]:
    root, files = _candidate_paths(candidate)
    try:
        manifest = json.loads(files["manifest.json"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ModelRunError("candidate manifest cannot be read") from None
    if (not isinstance(manifest, dict) or manifest.get("status") != "candidate_pending_owner_review" or
            manifest.get("synthetic_only") is not True or manifest.get("model_calls") != 0):
        raise ModelRunError("candidate is not an unreviewed zero-call synthetic candidate")
    try:
        _verify_candidate_artifact_hashes(files, manifest)
        blind = [json.loads(line) for line in files["blind.jsonl"].read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        records = _payload_records(blind, manifest)
    except (OSError, json.JSONDecodeError, PreflightError, KeyError, TypeError, ValueError):
        raise ModelRunError("candidate hash or blind payload validation failed") from None
    if len(records) != MAX_PROVIDER_REQUESTS or manifest.get("case_count") != MAX_PROVIDER_REQUESTS:
        raise ModelRunError("candidate does not match the fixed 22-request contract")
    return root, manifest, files, records, blind


def build_preregistration(candidate: Path, settings: Settings) -> dict:
    _, manifest, files, _, _ = _candidate_context(candidate)
    candidate_hashes = {name: sha256_file(path) for name, path in files.items()}
    return {
        "schema_version": 1,
        "status": "pending_owner_authorization",
        "external_calls_authorized": False,
        "suite_id": manifest["suite_id"],
        "case_count": MAX_PROVIDER_REQUESTS,
        "candidate_sha256": candidate_hashes,
        "data_version": manifest["data_version"],
        "data_fingerprint": manifest["data_fingerprint"],
        "source_sha256": _source_hashes(),
        "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "runtime": _runtime_fingerprint(),
        "model_id": settings.model,
        "provider_origin_sha256": _provider_origin_hash(settings),
        "request_policy": {
            "payload_fields": ["question", "allowed_metric_ids"],
            "response_fields": ["selected_metric_ids"],
            "max_provider_requests": MAX_PROVIDER_REQUESTS,
            "retry_count": 0,
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
            "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        },
        "budget_policy": {
            "hard_cap_usd": float(MAX_BUDGET_USD),
            "full_reservation_usd": float(MAX_BUDGET_USD),
            "preflight_snapshot_max_age_seconds": BUDGET_SNAPSHOT_MAX_AGE_SECONDS,
        },
    }


def validate_preregistration(value: object, candidate: Path, settings: Settings) -> dict:
    if not isinstance(value, dict) or set(value) != PREREG_FIELDS:
        raise ModelRunError("preregistration schema mismatch")
    expected = build_preregistration(candidate, settings)
    if value != expected:
        raise ModelRunError("preregistration hashes or policy differ from current candidate/runtime")
    return value


def write_preregistration(candidate: Path, settings: Settings, output: Path) -> dict:
    candidate_root = candidate.resolve(strict=True)
    resolved_output = output.resolve()
    if resolved_output == candidate_root or candidate_root in resolved_output.parents:
        raise ModelRunError("preregistration output must stay outside candidate")
    if output.exists() or output.is_symlink():
        raise ModelRunError("preregistration output already exists")
    value = build_preregistration(candidate, settings)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return value


def _read_json_object(path: Path, label: str) -> tuple[dict, str]:
    if path.is_symlink() or not path.is_file():
        raise ModelRunError(f"{label} must be a regular, non-symlink file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ModelRunError(f"{label} JSON is invalid") from None
    if not isinstance(value, dict):
        raise ModelRunError(f"{label} must be an object")
    return value, sha256_file(path)


def _validate_authorization(value: dict, prereg: dict, prereg_sha256: str,
                            settings: Settings) -> None:
    if set(value) != AUTH_FIELDS:
        raise ModelRunError("owner authorization schema mismatch")
    if (value.get("schema_version") != 1 or value.get("status") != "approved" or
            value.get("external_calls_authorized") is not True or
            value.get("preregistration_sha256") != prereg_sha256 or
            value.get("model_id") != prereg["model_id"] or
            value.get("provider_origin_sha256") != _provider_origin_hash(settings) or
            value.get("max_provider_requests") != MAX_PROVIDER_REQUESTS or
            isinstance(value.get("max_provider_requests"), bool) or
            value.get("max_cost_usd") != float(MAX_BUDGET_USD) or
            value.get("reserved_usd") != float(MAX_BUDGET_USD) or
            value.get("retry_policy") != "none" or
            any(value.get(field) is not True for field in
                ("candidate_hashes_reviewed", "blind_and_gold_reviewed", "prompt_and_model_reviewed",
                 "parity_evidence_reviewed", "budget_reviewed")) or
            not isinstance(value.get("authorized_by"), str) or not value["authorized_by"].strip()):
        raise ModelRunError("owner authorization does not approve this exact v14 lock and budget")
    try:
        approved_at = datetime.fromisoformat(value["authorized_at_utc"].replace("Z", "+00:00"))
    except (ValueError, AttributeError, KeyError):
        raise ModelRunError("owner authorization timestamp is invalid") from None
    if approved_at.tzinfo is None or approved_at > datetime.now(timezone.utc):
        raise ModelRunError("owner authorization timestamp is invalid")


def _decimal_amount(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ModelRunError(f"budget {field} is invalid")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ModelRunError(f"budget {field} is invalid") from None
    if not amount.is_finite() or amount < 0:
        raise ModelRunError(f"budget {field} is invalid")
    return amount


def _validate_budget_snapshot(value: dict, path: Path) -> dict[str, Decimal]:
    if set(value) != SNAPSHOT_FIELDS or value.get("schema_version") != 1:
        raise ModelRunError("fresh budget snapshot schema mismatch")
    try:
        observed_at = datetime.fromisoformat(value["as_of_utc"].replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ModelRunError("fresh budget snapshot timestamp is invalid") from None
    now = datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at > now or (now - observed_at).total_seconds() > BUDGET_SNAPSHOT_MAX_AGE_SECONDS:
        raise ModelRunError("budget snapshot is stale")
    file_age = now.timestamp() - path.stat().st_mtime
    if file_age < 0 or file_age > BUDGET_SNAPSHOT_MAX_AGE_SECONDS:
        raise ModelRunError("budget snapshot file is stale")
    return {field: _decimal_amount(value[field], field) for field in
            ("project_cap_usd", "spent_usd", "external_reserved_usd")}


def _outside_repo(path: Path, candidate_root: Path, label: str) -> Path:
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents or resolved == candidate_root or candidate_root in resolved.parents:
        raise ModelRunError(f"{label} must stay outside repository and candidate")
    return resolved


def _read_reservations(path: Path) -> tuple[Decimal, set[str], list[dict]]:
    total = Decimal("0")
    hashes: set[str] = set()
    if not path.exists():
        return total, hashes, []
    value, _ = _read_json_object(path, "v14 reservation journal")
    if set(value) != {"schema_version", "reservations"} or value.get("schema_version") != 1 or not isinstance(
            value.get("reservations"), list):
        raise ModelRunError("existing v14 reservation journal is invalid; budget gate closed")
    for reservation in value["reservations"]:
        if not isinstance(reservation, dict):
            raise ModelRunError("existing v14 reservation journal is invalid; budget gate closed")
        entry = reservation
        if (set(entry) != RESERVATION_FIELDS or entry.get("schema_version") != 1 or
                entry.get("status") != "reserved"):
            raise ModelRunError("existing v14 reservation is invalid; budget gate closed")
        digest = entry.get("preregistration_sha256")
        if not isinstance(digest, str) or not HASH64.fullmatch(digest) or digest in hashes:
            raise ModelRunError("existing v14 reservation is invalid; budget gate closed")
        hashes.add(digest)
        total += _decimal_amount(entry.get("reserved_usd"), "existing reservation")
    return total, hashes, value["reservations"]


def validate_model_report(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != REPORT_FIELDS:
        raise ModelRunError("model run report schema mismatch")
    integer_fields = ("requests_attempted", "provider_responses_received", "model_calls", "retry_count")
    if any(not isinstance(value.get(field), int) or isinstance(value.get(field), bool)
           for field in integer_fields):
        raise ModelRunError("model run report counts are invalid")
    if (value.get("retry_count") != 0 or value.get("reserved_usd") != float(MAX_BUDGET_USD) or
            value.get("external_calls_authorized") is not True or
            value.get("actual_cost_usd") is not None or
            value.get("cost_status") != "unconfirmed_no_billing_api_access"):
        raise ModelRunError("model run report violates fixed request budget")
    candidate_hashes = value.get("candidate_sha256")
    if (not isinstance(candidate_hashes, dict) or
            set(candidate_hashes) != {"manifest.json", "blind.jsonl", "gold.jsonl", "synthetic.sqlite"} or
            any(not isinstance(digest, str) or not HASH64.fullmatch(digest)
                for digest in candidate_hashes.values())):
        raise ModelRunError("model run report candidate hashes are invalid")
    cases = value.get("cases")
    if not isinstance(cases, list) or len(cases) > MAX_PROVIDER_REQUESTS:
        raise ModelRunError("model run report cases are invalid")
    if value["requests_attempted"] > MAX_PROVIDER_REQUESTS or value["model_calls"] != value["requests_attempted"]:
        raise ModelRunError("model run report exceeds the fixed request limit")
    if not 0 <= value["provider_responses_received"] <= value["requests_attempted"]:
        raise ModelRunError("model run report response count is invalid")
    if value["requests_attempted"] > len(cases):
        raise ModelRunError("model run report case/request totals do not reconcile")
    if value.get("status") not in {"completed", "timed_out", "transport_error",
                                   "provider_http_error", "invalid_response_or_preflight"}:
        raise ModelRunError("model run report status is invalid")
    if value["status"] == "completed" and (value["requests_attempted"] != MAX_PROVIDER_REQUESTS or
                                             value["provider_responses_received"] != MAX_PROVIDER_REQUESTS):
        raise ModelRunError("completed model run report is incomplete")
    for row in cases:
        if not isinstance(row, dict) or set(row) != CASE_FIELDS:
            raise ModelRunError("model run report contains unapproved case fields")
        if (not isinstance(row.get("case_id"), str) or
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", row["case_id"]) or
                row.get("status") not in {"received", "timed_out", "transport_error",
                                           "provider_http_error", "invalid_response_or_preflight"}):
            raise ModelRunError("model run report case is invalid")
        selected = row.get("selected_metric_ids")
        if (not isinstance(selected, list) or len(selected) > 8 or
                any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,80}", item)
                    for item in selected) or len(set(selected)) != len(selected)):
            raise ModelRunError("model run report metric IDs are invalid")
        latency = row.get("latency_ms")
        if latency is not None and (not isinstance(latency, (int, float)) or isinstance(latency, bool)
                                    or not math.isfinite(latency) or latency < 0):
            raise ModelRunError("model run report latency is invalid")
        usage = row.get("usage")
        if usage is not None and (not isinstance(usage, dict) or
                not set(usage) <= {"prompt_tokens", "completion_tokens", "total_tokens"} or
                any(not isinstance(count, int) or isinstance(count, bool) or count < 0
                    for count in usage.values())):
            raise ModelRunError("model run report usage is invalid")
    if value["status"] == "completed" and len(cases) != MAX_PROVIDER_REQUESTS:
        raise ModelRunError("completed model run report case count mismatch")
    return value


def _usage(payload: dict) -> dict | None:
    raw = payload.get("usage")
    if not isinstance(raw, dict):
        return None
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        number = raw.get(key)
        if isinstance(number, int) and not isinstance(number, bool) and number >= 0:
            result[key] = number
    return result or None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _no_redirect_open(request, timeout: float):
    opener = urllib.request.build_opener(_NoRedirectHandler)
    return opener.open(request, timeout=timeout)


def _provider_call(settings: Settings, record: dict, opener: Callable = _no_redirect_open) -> tuple[list[str], dict | None]:
    from .ip4_v14_offline import validate_payload

    payload = validate_payload(record)
    # The user content contains only the two preregistered allowlisted fields.
    body = {
        "model": settings.model,
        "temperature": 0,
        "max_tokens": MAX_COMPLETION_TOKENS,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
        ],
    }
    request = urllib.request.Request(
        settings.base_url.rstrip("/") + "/chat/completions", data=_json_bytes(body),
        headers={"Authorization": "Bearer " + settings.api_key, "Content-Type": "application/json"},
        method="POST",
    )
    with opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        response_payload = json.loads(response.read().decode("utf-8"))
    try:
        content = response_payload["choices"][0]["message"]["content"]
        decoded = json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise ModelRunError("provider response schema is invalid") from None
    if not isinstance(decoded, dict) or set(decoded) != {"selected_metric_ids"}:
        raise ModelRunError("provider response contains unapproved fields")
    selected = decoded["selected_metric_ids"]
    allowed = payload["allowed_metric_ids"]
    if (not isinstance(selected, list) or len(selected) > len(allowed) or
            any(not isinstance(item, str) or item not in allowed for item in selected) or
            len(set(selected)) != len(selected)):
        raise ModelRunError("provider selected metric IDs outside the allowed list")
    return selected, _usage(response_payload)


def dry_run(candidate: Path, preregistration_path: Path, settings: Settings) -> dict:
    preregistration, _ = _read_json_object(preregistration_path, "preregistration")
    validate_preregistration(preregistration, candidate, settings)
    return {
        "status": "dry_run_passed",
        "external_calls_authorized": False,
        "model_calls": 0,
        "provider_requests": 0,
        "case_count": MAX_PROVIDER_REQUESTS,
        "max_cost_usd": float(MAX_BUDGET_USD),
        "model_id": preregistration["model_id"],
        "provider_origin_sha256": preregistration["provider_origin_sha256"],
    }


def execute_model_run(candidate: Path, preregistration_path: Path, authorization_path: Path,
                      budget_snapshot_path: Path, state_dir: Path, output_path: Path,
                      settings: Settings, *, opener: Callable = _no_redirect_open) -> dict:
    prereg, prereg_sha256 = _read_json_object(preregistration_path, "preregistration")
    validate_preregistration(prereg, candidate, settings)
    candidate_root, _, _, records, blind = _candidate_context(candidate)
    if state_dir.is_symlink():
        raise ModelRunError("state directory symlink is not accepted")
    auth_path = _outside_repo(authorization_path, candidate_root, "owner authorization")
    budget_path = _outside_repo(budget_snapshot_path, candidate_root, "budget snapshot")
    state_root = _outside_repo(state_dir, candidate_root, "state directory")
    output = _outside_repo(output_path, candidate_root, "run output")
    if output == state_root or state_root in output.parents:
        raise ModelRunError("run output must stay outside reservation state directory")
    authorization, authorization_sha256 = _read_json_object(auth_path, "owner authorization")
    _validate_authorization(authorization, prereg, prereg_sha256, settings)
    snapshot, snapshot_sha256 = _read_json_object(budget_path, "budget snapshot")
    if output.exists() or output.is_symlink():
        raise ModelRunError("run output already exists; refusing replay")
    if not output.parent.is_dir():
        raise ModelRunError("run output parent directory must already exist")
    state_root.mkdir(parents=True, exist_ok=True)
    reservation_journal = state_root / "ip4-v14-reservations.json"
    lock_path = state_root / "ip4-v14-budget.lock"
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ModelRunError("another v14 budget reservation is in progress") from None
    try:
        os.write(lock_fd, str(os.getpid()).encode("ascii"))
        os.close(lock_fd)
        current_snapshot, current_snapshot_sha256 = _read_json_object(budget_path, "budget snapshot")
        if current_snapshot_sha256 != snapshot_sha256:
            raise ModelRunError("budget snapshot changed during preflight")
        budget = _validate_budget_snapshot(current_snapshot, budget_path)
        reserved, prior_hashes, existing_reservations = _read_reservations(reservation_journal)
        if prereg_sha256 in prior_hashes:
            raise ModelRunError("this preregistration already has a reservation; replay is blocked")
        if (budget["spent_usd"] + budget["external_reserved_usd"] + reserved + MAX_BUDGET_USD
                > budget["project_cap_usd"]):
            raise ModelRunError("budget preflight rejected the full v14 reservation")
        reservation = {
            "schema_version": 1,
            "preregistration_sha256": prereg_sha256,
            "authorization_sha256": authorization_sha256,
            "reserved_usd": float(MAX_BUDGET_USD),
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "reserved",
        }
        journal = {"schema_version": 1, "reservations": [*existing_reservations, reservation]}
        pending_path = state_root / "ip4-v14-reservations.pending"
        with pending_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(journal, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending_path, reservation_journal)
    finally:
        try:
            os.close(lock_fd)
        except OSError:
            pass
        lock_path.unlink(missing_ok=True)

    report = {
        "schema_version": 1,
        "preregistration_sha256": prereg_sha256,
        "candidate_sha256": prereg["candidate_sha256"],
        "model_id": prereg["model_id"],
        "status": "running",
        "external_calls_authorized": True,
        "requests_attempted": 0,
        "provider_responses_received": 0,
        "model_calls": 0,
        "retry_count": 0,
        "reserved_usd": float(MAX_BUDGET_USD),
        "actual_cost_usd": None,
        "cost_status": "unconfirmed_no_billing_api_access",
        "cases": [],
    }
    status = "completed"
    for row, record in zip(blind, records):
        request_started = False
        try:
            safe_record = _run_egress_worker(record)
            if safe_record != record:
                raise ModelRunError("sanitized payload changed")
            report["requests_attempted"] += 1
            report["model_calls"] += 1
            request_started = True
            started = time.perf_counter()
            selected, usage = _provider_call(settings, safe_record, opener=opener)
            latency = round((time.perf_counter() - started) * 1000, 3)
            report["provider_responses_received"] += 1
            report["cases"].append({"case_id": row["case_id"], "status": "received",
                                    "selected_metric_ids": selected, "latency_ms": latency,
                                    "usage": usage})
        except urllib.error.HTTPError:
            status = "provider_http_error"
            report["provider_responses_received"] += 1
            report["cases"].append({"case_id": row["case_id"], "status": status,
                                    "selected_metric_ids": [], "latency_ms": None, "usage": None})
            break
        except (TimeoutError, urllib.error.URLError) as exc:
            reason = getattr(exc, "reason", None)
            status = "timed_out" if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError) else "transport_error"
            report["cases"].append({"case_id": row["case_id"], "status": status,
                                    "selected_metric_ids": [], "latency_ms": None, "usage": None})
            break
        except (ModelRunError, ValueError, KeyError, TypeError, OSError):
            status = "invalid_response_or_preflight"
            if request_started:
                report["provider_responses_received"] += 1
            report["cases"].append({"case_id": row["case_id"], "status": status,
                                    "selected_metric_ids": [], "latency_ms": None, "usage": None})
            break
    report["status"] = status
    validate_model_report(report)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return report
