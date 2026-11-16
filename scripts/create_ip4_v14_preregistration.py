"""Create a v14-specific, non-authorizing model preregistration lock."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.config import load_settings
from insightpilot.ip4_v14_model_runner import ModelRunError, write_preregistration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True,
                        help="existing external synthetic v14 candidate directory")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/ip4-v14-model-preregistration-v5.json")
    args = parser.parse_args()
    try:
        settings = load_settings(ROOT)
        prereg = write_preregistration(args.candidate, settings, args.output)
    except (OSError, ValueError, ModelRunError) as exc:
        parser.exit(2, f"v14 preregistration blocked: {type(exc).__name__}: {exc}\n")
    print(json.dumps({"status": prereg["status"],
                      "external_calls_authorized": prereg["external_calls_authorized"],
                      "case_count": prereg["case_count"],
                      "model_id": prereg["model_id"],
                      "output": str(args.output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
