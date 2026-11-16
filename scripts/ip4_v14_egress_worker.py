"""Small stdin/stdout-only process boundary for sanitized offline payload checks."""

from __future__ import annotations

import json
import re
import sys

_PATH_VALUE = re.compile(r"^(?:[a-z]:[\\/]|\\\\|/[^/\s])", re.IGNORECASE)


def validate(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"question", "allowed_metric_ids"}:
        raise ValueError("payload schema rejected")
    question = value["question"]
    ids = value["allowed_metric_ids"]
    if (not isinstance(question, str) or not question.strip() or len(question) > 4000 or
            _PATH_VALUE.match(question.strip())):
        raise ValueError("question rejected")
    if not isinstance(ids, list) or not ids or len(ids) > 8:
        raise ValueError("metric IDs rejected")
    if any(not isinstance(item, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,80}", item) for item in ids):
        raise ValueError("metric IDs rejected")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate metric IDs rejected")
    return {"question": question, "allowed_metric_ids": ids}


def main() -> int:
    try:
        payload = validate(json.load(sys.stdin))
    except Exception:
        sys.stderr.write("payload rejected\n")
        return 2
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
