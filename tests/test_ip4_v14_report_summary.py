import hashlib
import json
from pathlib import Path


def test_versioned_postgres_report_contains_only_redacted_synthetic_summary():
    root = Path(__file__).resolve().parents[1]
    report_path = root / "reports/ip4-v14-postgres-parity-summary-2026-11-15.json"
    raw = report_path.read_bytes()
    report = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == "6b990eec41698c0ca4d2c9ef55cfd38bda85ca6701d4031eb19b186fdb3b5492"
    assert report["synthetic_only"] is True
    assert report["redacted_summary"] is True
    assert report["evaluation"]["model_calls"] == 0
    assert report["evaluation"]["case_count"] == 22
    parity = report["evaluation"]["database_parity"]
    assert parity["status"] == "passed"
    assert parity["postgresql_tenants_checked"] == 2
    assert parity["all_case_score_vectors_match_sqlite"] is True
    assert report["source_report"]["sha256"] == (
        "f3777d3407a0f470734b6226c128830e625453e6b60b8a5af88dd390b373812b"
    )

    forbidden = {"case_id", "cases", "database_rows", "dsn", "gold_rows", "question",
                 "queries", "rows", "sql", "tenant_id", "tenant_ids"}
    keys = set()
    values = []

    def visit(value):
        if isinstance(value, dict):
            keys.update(str(key).casefold() for key in value)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            values.append(value.casefold())

    visit(report)
    assert not (keys & forbidden)
    assert not any(value in ("tenant-a", "tenant-b") or value.startswith(("e:\\", "c:\\"))
                   for value in values)
