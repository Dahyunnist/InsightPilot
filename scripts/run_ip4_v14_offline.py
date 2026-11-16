"""Run only the zero-network IP-4 v14 preflight and deterministic baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.ip4_v14_offline import (
    DEFAULT_LOCK, PreflightError, run_offline, sha256_file, validate_lock, write_result,
)

CANONICAL_LOCK_SHA256 = "287fd81dac0703964f78e927ad64dc3342a4d16b236846e61334d05a19fd47a7"


def load_lock(path: Path) -> dict:
    resolved = path.resolve(strict=True)
    if resolved != DEFAULT_LOCK.resolve(strict=True):
        raise PreflightError("--lock replacement is forbidden; only the reviewed canonical lock is accepted")
    if sha256_file(resolved) != CANONICAL_LOCK_SHA256:
        raise PreflightError("canonical offline lock hash mismatch")
    lock = json.loads(resolved.read_text(encoding="utf-8"))
    validate_lock(lock)
    return lock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True,
                        help="external synthetic v14 candidate directory")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--report", type=Path,
                        help="new sanitized result path; existing reports are never overwritten")
    args = parser.parse_args()
    try:
        lock = load_lock(args.lock)
        report = run_offline(args.candidate, lock)
        if args.report:
            write_result(report, args.report, lock)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (OSError, ValueError, PreflightError) as exc:
        parser.exit(2, f"offline preflight blocked: {type(exc).__name__}: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
