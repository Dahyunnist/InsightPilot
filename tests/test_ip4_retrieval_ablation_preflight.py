from scripts.run_ip4_retrieval_ablation_model import assess_balance


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
