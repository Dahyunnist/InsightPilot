"""Test temporary files under the repository's writable, ignored venv folder."""

from pathlib import Path
import tempfile


_ROOT = Path(__file__).resolve().parents[1] / ".venv" / "test-tmp"
_ROOT.mkdir(parents=True, exist_ok=True)


def TemporaryDirectory(*args, **kwargs):
    kwargs.setdefault("dir", str(_ROOT))
    return tempfile.TemporaryDirectory(*args, **kwargs)
