"""Build independent Python-oracle answers for the v7 synthetic holdout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from data.build_enterprise_v5 import build


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/insightpilot-enterprise-v7.sqlite"))
    parser.add_argument("--blind", type=Path, default=Path("evals/enterprise-blind-v7.jsonl"))
    parser.add_argument("--gold", type=Path, default=Path("evals/enterprise-gold-v7.jsonl"))
    parser.add_argument("--manifest", type=Path, default=Path("evals/enterprise-holdout-manifest-v7.json"))
    args = parser.parse_args()
    print(json.dumps(build(args.database, args.blind, args.gold, args.manifest), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
