"""Stable content hashes for UTF-8 text files across Windows and Unix."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    canonical = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
