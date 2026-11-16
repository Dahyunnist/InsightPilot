"""Read provider balance only; never submits a model request or saves the response."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insightpilot.config import load_settings
from insightpilot.ip4_v14_model_runner import _provider_origin_hash

PREREGISTRATION = ROOT / "reports/ip4-v14-model-preregistration-v5.json"
CONSERVATIVE_CNY_PER_USD = Decimal("7.5")


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _amount(value: object) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reserve-usd", type=Decimal, default=Decimal("0.187"))
    args = parser.parse_args()
    if not args.reserve_usd.is_finite() or args.reserve_usd <= 0:
        parser.error("--reserve-usd must be positive and finite")

    try:
        settings = load_settings(ROOT)
        prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
        if (settings.model != prereg.get("model_id") or
                _provider_origin_hash(settings) != prereg.get("provider_origin_sha256")):
            raise ValueError("configured provider does not match the frozen v14 preregistration")
        parsed = urlsplit(settings.base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        request = Request(origin + "/user/balance", headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Accept": "application/json",
        }, method="GET")
        with build_opener(_RejectRedirects).open(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, HTTPError, URLError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_class": type(exc).__name__,
                          "model_calls": 0, "provider_requests": 0}, ensure_ascii=False))
        return 2

    balances: dict[str, Decimal] = {}
    rows = payload.get("balance_infos") if isinstance(payload, dict) else None
    if isinstance(payload, dict) and payload.get("is_available") is True and isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or row.get("currency") not in {"USD", "CNY"}:
                continue
            amount = _amount(row.get("total_balance"))
            if amount is not None:
                balances[row["currency"]] = amount

    usd_required = args.reserve_usd
    cny_required = usd_required * CONSERVATIVE_CNY_PER_USD
    if balances.get("USD", Decimal("0")) >= usd_required:
        currency, available, required = "USD", balances["USD"], usd_required
    elif balances.get("CNY", Decimal("0")) >= cny_required:
        currency, available, required = "CNY", balances["CNY"], cny_required
    else:
        print(json.dumps({"status": "insufficient_or_unavailable", "required_usd": str(usd_required),
                          "required_cny_at_7_5": str(cny_required),
                          "usd_available": str(balances.get("USD", Decimal("0"))),
                          "cny_available": str(balances.get("CNY", Decimal("0"))),
                          "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                          "model_calls": 0, "provider_requests": 1}, ensure_ascii=False))
        return 3

    print(json.dumps({"status": "passed", "currency": currency, "available": str(available),
                      "required": str(required), "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                      "model_calls": 0, "provider_requests": 1,
                      "response_saved": False, "api_key_printed": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
