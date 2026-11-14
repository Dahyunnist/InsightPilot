"""Create a new provenance view for r3 without rewriting historical records."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.normalize_ip4_public_manifests import audit_r3_legacy_reservation

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build() -> dict:
    reports = ROOT / "reports"
    attempt_path = reports / "ip4-v13-attempt-manifest-2026-11-13-r3.json"
    lock_path = reports / "ip4-v13-model-preregistration-20261113-r2.json"
    ledger_path = reports / "model-experiment-cost-ledger.json"
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    return {
        "migration": "ip4-v13-r3-ledger-provenance-v1",
        "status": "forward_audit_only",
        "historical_records_modified": False,
        "migration_audit": audit_r3_legacy_reservation(
            attempt, lock, ledger,
            attempt_lock_sha256=attempt.get("prior_preregistration_sha256"),
            lock_file_sha256=sha(lock_path)),
        "source_hashes": {
            "attempt_manifest_sha256": sha(attempt_path),
            "r2_preregistration_sha256": sha(lock_path),
            "ledger_sha256_at_migration": sha(ledger_path),
        },
        "question_sha256": attempt.get("question_set_sha256"),
        "gold_sha256": attempt.get("gold_sha256"),
    }


if __name__ == "__main__":
    target = ROOT / "reports/ip4-v13-r3-ledger-provenance-migration-v1.json"
    target.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(json.loads(target.read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
