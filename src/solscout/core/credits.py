"""Credit budget governor (ADR-022) — make a fixed monthly credit quota last the WHOLE month, 24/7.

Helius free = ~1M credits/month. We pace spending LINEARLY across the calendar month: at any moment you
may have spent at most `budget * (elapsed fraction of month)` (+ a small head-start buffer). If we're
ahead of pace, metered calls are skipped (the token just gets less data — graceful degradation) until the
clock catches up. This GUARANTEES the quota never runs out before month end, regardless of launch volume.

Pure logic here (unit-tested); persistence + wiring live in db/helius/service.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone


def month_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def day_key(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d")


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days = calendar.monthrange(now.year, now.month)[1]
    return start, start + timedelta(days=days)


class CreditGovernor:
    def __init__(
        self,
        budget: float,
        used: float = 0.0,
        now: datetime | None = None,
        head_start: float = 0.02,
    ):
        self.budget = float(budget)
        self.used = float(used)
        self._now = now
        self.start, self.end = month_bounds(now or datetime.now(timezone.utc))
        self.head_start = (
            head_start  # small buffer so we aren't starved in the first minutes of the month
        )

    def _clock(self, now: datetime | None) -> datetime:
        return now or self._now or datetime.now(timezone.utc)

    def paced_allowance(self, now: datetime | None = None) -> float:
        total = (self.end - self.start).total_seconds()
        frac = (self._clock(now) - self.start).total_seconds() / total if total else 1.0
        frac = min(1.0, max(0.0, frac))
        return min(self.budget, self.budget * frac + self.budget * self.head_start)

    def can_spend(self, cost: float, now: datetime | None = None) -> bool:
        nxt = self.used + cost
        return nxt <= self.budget and nxt <= self.paced_allowance(now)

    def note(self, cost: float) -> None:
        self.used += cost

    def remaining(self) -> float:
        return max(0.0, self.budget - self.used)


class DailyCap:
    """ADR-046 — a fixed credit budget per UTC DAY (the web product's sub-budget inside the monthly
    governor). Rolls over automatically at midnight UTC; `used` is reseeded from the db at startup.
    Duck-typed like CreditGovernor (can_spend/note/remaining) so HeliusClient needs no changes."""

    def __init__(self, budget: float, used: float = 0.0, now: datetime | None = None):
        self.budget = float(budget)
        self.used = float(used)
        self._day = day_key(now)

    def _roll(self, now: datetime | None = None) -> None:
        d = day_key(now)
        if d != self._day:
            self._day, self.used = d, 0.0

    def can_spend(self, cost: float, now: datetime | None = None) -> bool:
        self._roll(now)
        return self.used + cost <= self.budget

    def note(self, cost: float) -> None:
        self.used += cost

    def remaining(self) -> float:
        return max(0.0, self.budget - self.used)


class CompositeGovernor:
    """ALL member governors must agree before a spend; a spend is noted on all. Lets the web stack
    enforce 'monthly pace AND daily cap' through the single governor slot HeliusClient already has."""

    def __init__(self, *governors):
        self.governors = [g for g in governors if g is not None]

    def can_spend(self, cost: float, now: datetime | None = None) -> bool:
        return all(g.can_spend(cost, now) for g in self.governors)

    def note(self, cost: float) -> None:
        for g in self.governors:
            g.note(cost)

    def remaining(self) -> float:
        return min((g.remaining() for g in self.governors), default=0.0)
