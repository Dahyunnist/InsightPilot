"""Command-line interface for the S1 analysis flow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import build_core
from .config import load_settings


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="InsightPilot read-only refund analysis")
    parser.add_argument("question", help="Natural-language business question")
    args = parser.parse_args()
    root = Path.cwd()
    settings = load_settings(root)
    app = build_core(settings)
    print(json.dumps(app.analyze(args.question), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
