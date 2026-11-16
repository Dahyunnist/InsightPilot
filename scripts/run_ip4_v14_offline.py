"""Run only the zero-network IP-4 v14 preflight and deterministic baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.ip4_v14_offline import DEFAULT_LOCK, PreflightError, run_offline, write_result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True,
                        help="external synthetic v14 candidate directory")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--report", type=Path,
                        help="new sanitized result path; existing reports are never overwritten")
    args = parser.parse_args()
    try:
        lock = json.loads(args.lock.read_text(encoding="utf-8"))
        report = run_offline(args.candidate, lock)
        if args.report:
            write_result(report, args.report, lock)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (OSError, ValueError, PreflightError) as exc:
        parser.exit(2, f"offline preflight blocked: {type(exc).__name__}: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
