"""Global kill switch. Checked before EVERY execution. Fail-safe: tripped => no trading.

File-flag based so it survives restarts and can be tripped manually (`touch .killswitch`).
"""

from __future__ import annotations

from .config import REPO_ROOT

_FLAG = REPO_ROOT / ".killswitch"


def is_tripped() -> bool:
    return _FLAG.exists()


def trip(reason: str) -> None:
    _FLAG.write_text(reason.strip() + "\n")


def reset() -> None:
    _FLAG.unlink(missing_ok=True)


def reason() -> str | None:
    return _FLAG.read_text().strip() if _FLAG.exists() else None
