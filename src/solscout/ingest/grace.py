"""Grace-period re-evaluation. Fresh launches are usually not indexed/liquid on first pass (PENDING),
so instead of a one-shot verdict we queue them and re-run the funnel later, with backoff.

Scheduling math here is PURE and unit-tested; the queue itself is persisted in SQLite (survives restarts).
A re-eval is 'terminal' once the token gets a real verdict (BUY/WATCH/REJECT) or we exhaust max_attempts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..core.config import GraceCfg


def next_delay_s(attempt: int, cfg: GraceCfg) -> int:
    """Delay before the re-eval numbered `attempt` (1-based): first_delay_s * backoff_mult**(attempt-1)."""
    return int(cfg.first_delay_s * (cfg.backoff_mult ** max(0, attempt - 1)))


def next_due_at(attempt: int, cfg: GraceCfg, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now + timedelta(seconds=next_delay_s(attempt, cfg))


def should_retry(attempts_done: int, cfg: GraceCfg) -> bool:
    return cfg.enabled and attempts_done < cfg.max_attempts


def reeval_action(still_pending: bool, attempts_done: int, cfg: GraceCfg) -> str:
    """Decide what to do after a re-eval attempt completes (pure).
    'terminal'   → token now has real on-chain data; act on the verdict and drop from queue.
    'reschedule' → still pending, attempts remain → queue another re-eval with backoff.
    'giveup'     → still pending and out of attempts → drop (never indexed / dead launch).
    """
    if not still_pending:
        return "terminal"
    return "reschedule" if should_retry(attempts_done, cfg) else "giveup"
