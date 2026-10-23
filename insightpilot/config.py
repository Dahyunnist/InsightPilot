"""Configuration loading without third-party dependencies."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float
    max_completion_tokens: int
    database_path: Path
    database_url: str | None = None


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE entries, without overriding process environment."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def load_settings(root: Path | None = None) -> Settings:
    root = (root or Path.cwd()).resolve()
    load_env_file(root / ".env")
    required = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError("Missing required .env values: " + ", ".join(missing))
    try:
        timeout = float(os.environ.get("LLM_TIMEOUT_SECONDS", "30"))
        tokens = int(os.environ.get("LLM_MAX_COMPLETION_TOKENS", "1200"))
    except ValueError as exc:
        raise ValueError("LLM_TIMEOUT_SECONDS and LLM_MAX_COMPLETION_TOKENS must be numeric") from exc
    if timeout <= 0 or tokens < 1:
        raise ValueError("LLM timeout and completion token limit must be positive")
    database = Path(os.environ.get("INSIGHTPILOT_DATABASE_PATH", "data/insightpilot-s0.sqlite"))
    if not database.is_absolute():
        database = root / database
    database_url = os.environ.get("INSIGHTPILOT_DATABASE_URL") or None
    return Settings(
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.environ["LLM_BASE_URL"].rstrip("/"),
        model=os.environ["LLM_MODEL"],
        timeout_seconds=timeout,
        max_completion_tokens=min(tokens, 1200),
        database_path=database.resolve(),
        database_url=database_url,
    )
