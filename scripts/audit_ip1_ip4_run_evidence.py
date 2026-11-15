"""Build a deterministic, offline projection of recorded IP-1..IP-4 evidence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.run_evidence import audit_record_evidence


def main() -> None:
    audit = audit_record_evidence(ROOT)
    output = ROOT / "reports/ip1-ip4-run-evidence-audit.json"
    output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
