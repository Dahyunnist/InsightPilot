"""Verify sealed IP4 data and evaluation result hashes without modifying them."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEAL = ROOT / "reports/ip4-freeze-seal.json"


def verify() -> dict:
    seal = json.loads(SEAL.read_text(encoding="utf-8"))
    actual = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
              for name in seal["files"]}
    mismatches = {name: {"expected": seal["files"][name], "actual": digest}
                  for name, digest in actual.items() if seal["files"][name] != digest}
    return {"status": "passed" if not mismatches else "failed",
            "sealed_file_count": len(actual), "mismatches": mismatches,
            "blind_or_gold_modified": any(name.startswith("evals/ip4-") for name in mismatches)}


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "passed":
        raise SystemExit(1)
