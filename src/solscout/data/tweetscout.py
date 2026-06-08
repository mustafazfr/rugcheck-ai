"""TweetScout client — Twitter authenticity signals (account age, followers, score, handle-reuse).

Needs TWEETSCOUT_API_KEY; degrades gracefully to None without it (the funnel just drops the Twitter
sub-signals). NOTE: exact response field names must be confirmed against the live API once a key is
available — parsing here is intentionally tolerant and conservative (unknown -> None, never fabricated).
Docs: https://docs.tweetscout.io
"""

from __future__ import annotations

from datetime import datetime, timezone

from .base import BaseClient

BASE = "https://api.tweetscout.io"


class TweetScoutClient(BaseClient):
    def __init__(self, api_key: str):
        super().__init__(
            BASE,
            timeout=15.0,
            min_interval_s=0.3,
            headers={"ApiKey": api_key, "Accept": "application/json"},
        )
        self.api_key = api_key

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def info(self, handle: str) -> dict | None:
        if not self.available or not handle:
            return None
        try:
            return await self.get_json(f"/v2/info/{handle}")
        except Exception:
            return None

    async def score(self, handle: str) -> float | None:
        if not self.available or not handle:
            return None
        try:
            d = await self.get_json(f"/v2/score/{handle}")
            return (
                float(d.get("score"))
                if isinstance(d, dict) and d.get("score") is not None
                else None
            )
        except Exception:
            return None


def account_age_days(info: dict | None) -> int | None:
    """Best-effort parse of account creation date from a TweetScout info payload."""
    if not info:
        return None
    raw = info.get("register_date") or info.get("created_at") or info.get("join_date")
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%a %b %d %H:%M:%S %z %Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(str(raw)[:25], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0, (datetime.now(timezone.utc) - dt).days)
        except ValueError:
            continue
    return None
