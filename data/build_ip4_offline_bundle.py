"""Freeze an offline IP4 bundle with agent input separated from scoring gold."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from data.build_ip4_v13_holdout import MARKETING_SEED, ORDER_COUNT, RETAIL_SEED
from data.multidomain_seed import create_multidomain_database


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "evals"
SOURCE_BLIND = SOURCE_DIR / "ip4-v13-blind.jsonl"
SOURCE_GOLD = SOURCE_DIR / "ip4-v13-gold.jsonl"
SOURCE_MANIFEST = SOURCE_DIR / "ip4-v13-manifest.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_once(path: Path, content: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise ValueError(f"Refusing to overwrite changed frozen artifact: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def _generated_dataset_identity() -> dict:
    temporary = Path(tempfile.mkdtemp(prefix="insightpilot-ip4-offline-data-"))
    database = temporary / "ip4-v13.sqlite"
    try:
        create_multidomain_database(database, seed=MARKETING_SEED, retail_seed=RETAIL_SEED,
                                    order_count=ORDER_COUNT)
        connection = sqlite3.connect(database)
        try:
            metadata = dict(connection.execute("SELECT key,value FROM data_meta"))
        finally:
            connection.close()
        return {"sqlite_sha256": _sha256(database), **metadata}
    finally:
        shutil.rmtree(temporary)


def build(output: Path | None = None) -> dict:
    output = output or ROOT / "benchmarks/ip4-offline-prep-v1"
    source_manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    source_blind_hash = _sha256(SOURCE_BLIND)
    source_gold_hash = _sha256(SOURCE_GOLD)
    if source_blind_hash != source_manifest["blind_sha256"] or source_gold_hash != source_manifest["gold_sha256"]:
        raise ValueError("Source v13 blind/gold hash differs from its frozen manifest")

    blind_rows = [json.loads(line) for line in SOURCE_BLIND.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_rows = [json.loads(line) for line in SOURCE_GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]
    gold_by_id = {row["case_id"]: row for row in gold_rows}
    if len(blind_rows) != source_manifest["case_count"] or {row["case_id"] for row in blind_rows} != set(gold_by_id):
        raise ValueError("Source v13 blind/gold IDs or count differ")
    if any(set(row) != {"case_id", "question"} for row in blind_rows):
        raise ValueError("Agent-visible rows must contain only case_id and question")

    agent_root = output / "agent-visible"
    scoring_root = output / "scoring-private"
    frozen_hashes = {}
    for domain in ("refunds", "marketing_roi"):
        cases = [row for row in blind_rows if gold_by_id[row["case_id"]]["domain"] == domain]
        gold = [gold_by_id[row["case_id"]] for row in cases]
        if not cases or len(cases) != len(gold):
            raise ValueError(f"Missing cases for domain {domain}")
        blind_path = agent_root / f"{domain}-blind.jsonl"
        gold_path = scoring_root / f"{domain}-gold.jsonl"
        _write_once(blind_path, _jsonl(cases))
        _write_once(gold_path, _jsonl(gold))
        frozen_hashes[domain] = {
            "case_count": len(cases),
            "answerable_count": sum(row.get("expected_status") == "answered" for row in gold),
            "guard_count": sum(row.get("expected_status") != "answered" for row in gold),
            "blind_path": blind_path.relative_to(output).as_posix(),
            "blind_sha256": _sha256(blind_path),
            "gold_path": gold_path.relative_to(output).as_posix(),
            "gold_sha256": _sha256(gold_path),
        }

    dataset = _generated_dataset_identity()
    expected_identity = {
        "data_version": source_manifest["data_version"],
        "data_fingerprint": source_manifest["data_fingerprint"],
        "seed": str(MARKETING_SEED), "retail_seed": str(RETAIL_SEED),
        "order_count": str(ORDER_COUNT), "snapshot_at": source_manifest["snapshot_at"],
    }
    if any(dataset.get(key) != value for key, value in expected_identity.items()):
        raise ValueError("Regenerated IP4 dataset identity differs from the frozen v13 manifest")

    manifest = {
        "suite_id": "insightpilot-ip4-offline-prep-v1",
        "status": "frozen_offline_preparation",
        "created_for": "IP-4 offline baseline and SQLite/PostgreSQL consistency audit",
        "case_count": len(blind_rows),
        "domains": frozen_hashes,
        "data": {
            "data_version": dataset["data_version"],
            "data_fingerprint": dataset["data_fingerprint"],
            "sqlite_sha256": dataset["sqlite_sha256"],
            "marketing_seed": MARKETING_SEED,
            "retail_seed": RETAIL_SEED,
            "order_count": ORDER_COUNT,
            "snapshot_at": dataset["snapshot_at"],
            "builder": "data.multidomain_seed.create_multidomain_database",
            "builder_sha256": _sha256(ROOT / "data/multidomain_seed.py"),
            "holdout_builder_sha256": _sha256(ROOT / "data/build_ip4_v13_holdout.py"),
        },
        "source_v13": {
            "manifest_sha256": _sha256(SOURCE_MANIFEST),
            "blind_sha256": source_blind_hash,
            "gold_sha256": source_gold_hash,
        },
        "agent_input_contract": {
            "directory": "agent-visible",
            "files": [f"agent-visible/{domain}-blind.jsonl" for domain in frozen_hashes],
            "row_fields": ["case_id", "question"],
            "gold_in_agent_visible_directory": False,
        },
        "scoring_contract": {
            "directory": "scoring-private",
            "gold_method": source_manifest["gold_method"],
            "scorer": "insightpilot.enterprise_evaluate._compare",
            "scorer_sha256": _sha256(ROOT / "insightpilot/enterprise_evaluate.py"),
            "independent_verifier_sha256": _sha256(ROOT / "insightpilot/verification.py"),
        },
        "model_calls": 0,
        "sample_limit": "22 个固定 synthetic holdout 样本；区间和分数仅描述此集，不外推至生产或总体泛化。",
    }
    manifest_path = output / "manifest.json"
    _write_once(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    visible_files = sorted(path.relative_to(agent_root).as_posix() for path in agent_root.rglob("*") if path.is_file())
    expected_files = sorted(f"{domain}-blind.jsonl" for domain in frozen_hashes)
    if visible_files != expected_files:
        raise ValueError("Agent-visible directory contains unexpected files")
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
