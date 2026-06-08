"""Structured logging (rich). Use get_logger(__name__)."""

from __future__ import annotations

import logging

from rich.logging import RichHandler

_CONFIGURED = False


def setup(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )
    # httpx logs full request URLs at INFO — that would leak the Helius api-key query param. Silence them.
    for noisy in ("httpx", "httpcore", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(name)
