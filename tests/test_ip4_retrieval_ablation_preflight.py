import hashlib
import json
import tempfile
from pathlib import Path

from data.build_ip4_v13_holdout import MARKETING_SEED, ORDER_COUNT, RETAIL_SEED
from data.multidomain_seed import create_multidomain_database
from scripts.run_ip4_retrieval_ablation_model import (
    BUNDLE, EXPECTED_BLIND_SHA256, EXPECTED_GOLD_SHA256, EXPECTED_MANIFEST_SHA256,
    INPUT_RATE, INPUT_TOKENS_PER_CALL, OUTPUT_RATE, OUTPUT_TOKENS_PER_CALL,
    load_bundle, planned_external_analyses, assess_balance,
)


def test_balance_preflight_accepts_sufficient_usd():
    result = assess_balance(
        {"is_available": True, "balance_infos": [{"currency": "USD", "total_balance": "0.20"}]},
        0.18,
    )
    assert result["status"] == "passed"
    assert result["currency_checked"] == "USD"


def test_balance_preflight_accepts_sufficient_cny_at_conservative_rate():
    result = assess_balance(
        {"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "9.24"}]},
        0.1873344,
    )
    assert result["status"] == "passed"
    assert result["currency_checked"] == "CNY"
    assert result["required_cny"] == 1.405008


def test_balance_preflight_rejects_insufficient_funds():
    result = assess_balance(
        {"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "1.00"}]},
        0.18,
    )
    assert result["status"] == "unavailable_or_below_reserve"


def test_balance_preflight_rejects_unavailable_or_malformed_response():
    assert assess_balance({"is_available": False, "balance_infos": []}, 0.1)["status"] == "account_unavailable"
    assert assess_balance({"is_available": True}, 0.1)["status"] == "balance_data_missing"
    result = assess_balance(
        {"is_available": True, "balance_infos": [{"currency": "CNY", "total_balance": "NaN"}]},
        0.1,
    )
    assert result["status"] == "unavailable_or_below_reserve"


def test_frozen_manifest_questions_and_local_gold_hashes_match_prior_evidence():
    prior = json.loads((Path(__file__).resolve().parents[1] /
                        "reports/ip4-retrieval-ablation-model.json").read_text(encoding="utf-8"))
    manifest_path = BUNDLE / "manifest.json"
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest(manifest_path) == EXPECTED_MANIFEST_SHA256 == prior["manifest_sha256"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["case_count"] == 22
    for domain, entry in manifest["domains"].items():
        blind = BUNDLE / entry["blind_path"]
        gold = BUNDLE / entry["gold_path"]
        assert digest(blind) == entry["blind_sha256"] == EXPECTED_BLIND_SHA256[domain]
        assert digest(gold) == entry["gold_sha256"] == EXPECTED_GOLD_SHA256[domain]
        assert digest(blind) == prior["blind_sha256"][domain]
        assert digest(gold) == prior["gold_sha256_local_only"][domain]


def test_current_deterministic_guard_plan_stays_within_authorized_call_and_cost_caps():
    with tempfile.TemporaryDirectory(prefix="ip4-retrieval-budget-preflight-") as directory:
        database = Path(directory) / "frozen.sqlite"
        create_multidomain_database(database, seed=MARKETING_SEED,
                                    retail_seed=RETAIL_SEED, order_count=ORDER_COUNT)
        manifest, cases, _gold, _domains = load_bundle(database)
    assert len(cases) == manifest["case_count"] == 22
    arm_plan = {
        "retrieval_on": planned_external_analyses(cases, True),
        "retrieval_off": planned_external_analyses(cases, False),
    }
    max_calls = 2 * sum(arm_plan.values())
    per_call = (INPUT_TOKENS_PER_CALL * INPUT_RATE +
                OUTPUT_TOKENS_PER_CALL * OUTPUT_RATE) / 1_000_000
    reserve = max_calls * per_call
    assert arm_plan == {"retrieval_on": 14, "retrieval_off": 19}
    assert max_calls == 66 <= 68 <= 88
    assert round(reserve, 8) == 0.155232
    assert reserve <= 0.159936 <= 0.1873344
