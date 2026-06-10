"""Per-IP abuse control for the public web API (ADR-046). Pure, clock-injectable, zero deps.

Philosophy: CACHED reports are always free to serve — these limits only protect work that SPENDS
(Helius credits / full analyses). Three layers:
  TokenBucket      – short-burst smoothing per IP (every /api hit)
  DailyMintLedger  – fresh (cache-miss) mints per IP per UTC day
  (the global daily Helius web budget lives in core/credits.DailyCap on the governor side)

In-memory by design: this is abuse control, not billing — a restart resetting counters is acceptable.
"""

from __future__ import annotations

import time
from collections import OrderedDict


class TokenBucket:
    """Classic token bucket: `capacity` instant burst, refilling at `refill_per_s`. Clock-injectable."""

    def __init__(self, capacity: float, refill_per_s: float, now: float | None = None):
        self.capacity = float(capacity)
        self.refill = float(refill_per_s)
        self.tokens = float(capacity)
        self.t = time.monotonic() if now is None else now

    def allow(self, now: float | None = None, cost: float = 1.0) -> bool:
        now = time.monotonic() if now is None else now
        self.tokens = min(self.capacity, self.tokens + max(0.0, now - self.t) * self.refill)
        self.t = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False

    def retry_after_s(self, cost: float = 1.0) -> int:
        """Seconds until `cost` tokens will have refilled (for the Retry-After header)."""
        if self.refill <= 0:
            return 60
        missing = max(0.0, cost - self.tokens)
        return max(1, int(missing / self.refill + 0.999))


class IpLimiter:
    """ip → TokenBucket with LRU eviction (bounded memory). allow(ip) → (ok, retry_after_s)."""

    def __init__(self, capacity: float, refill_per_s: float, max_ips: int = 10_000):
        self.capacity, self.refill, self.max_ips = capacity, refill_per_s, max_ips
        self._buckets: OrderedDict[str, TokenBucket] = OrderedDict()

    def allow(self, ip: str, now: float | None = None) -> tuple[bool, int]:
        b = self._buckets.get(ip)
        if b is None:
            b = TokenBucket(self.capacity, self.refill, now=now)
            self._buckets[ip] = b
            while len(self._buckets) > self.max_ips:
                self._buckets.popitem(last=False)  # evict least-recently-seen IP
        else:
            self._buckets.move_to_end(ip)
        ok = b.allow(now=now)
        return ok, (0 if ok else b.retry_after_s())


class DailyMintLedger:
    """(ip, UTC day) → set of FRESH mints this IP has triggered. Re-checking a mint already charged
    today is free (it'll be cached anyway); only genuinely new analysis work counts."""

    def __init__(self, max_per_day: int):
        self.max_per_day = int(max_per_day)
        self._day = ""
        self._mints: dict[str, set[str]] = {}

    def allow(self, ip: str, mint: str, day: str) -> bool:
        if day != self._day:  # UTC-day rollover wipes the ledger
            self._day, self._mints = day, {}
        s = self._mints.setdefault(ip, set())
        if mint in s:
            return True
        if len(s) >= self.max_per_day:
            return False
        s.add(mint)
        return True
