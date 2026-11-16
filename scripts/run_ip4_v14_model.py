"""Default to zero-call dry-run; external v14 requests need --execute and owner gates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.config import load_settings
from insightpilot.ip4_v14_model_runner import (ModelRunError, dry_run,
                                               execute_model_run)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True,
                        help="existing external synthetic v14 candidate directory")
    parser.add_argument("--preregistration", type=Path,
                        default=ROOT / "reports/ip4-v14-model-preregistration-v5.json")
    parser.add_argument("--execute", action="store_true",
                        help="submit requests only after matching owner and budget records pass")
    parser.add_argument("--owner-authorization", type=Path)
    parser.add_argument("--budget-snapshot", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.execute and not all((args.owner_authorization, args.budget_snapshot, args.state_dir, args.output)):
        parser.error("--execute requires owner authorization, fresh budget snapshot, state directory, and output")
    try:
        settings = load_settings(ROOT)
        if not args.execute:
            result = dry_run(args.candidate, args.preregistration, settings)
        else:
            result = execute_model_run(args.candidate, args.preregistration,
                                       args.owner_authorization, args.budget_snapshot,
                                       args.state_dir, args.output, settings)
    except (OSError, ValueError, ModelRunError) as exc:
        parser.exit(2, f"v14 model runner blocked: {type(exc).__name__}: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"dry_run_passed", "completed"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
