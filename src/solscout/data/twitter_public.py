"""Keyless Twitter/X profile intel via fxtwitter (ADR-041) — the project's "social identity" check.

fxtwitter (`api.fxtwitter.com/<handle>`) returns a public profile WITHOUT an API key: account age (joined),
followers, tweet count, verified, description, avatar. Scammers reuse brand-new / low-follower / no-tweet
accounts, so these are real authenticity signals. We never log in or scrape — one public endpoint, cached.
Profile FACTS only (age/followers/verified), not sentiment. Fail-open (no profile → "unverified").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .base import BaseClient

BASE = "https://api.fxtwitter.com"
_HANDLE = re.compile(r"(?:twitter\.com|x\.com)/(?:#!/)?@?([A-Za-z0-9_]{1,15})", re.I)
_BAD_PATHS = {"i", "intent", "share", "home", "search", "hashtag", "explore", "messages"}


def handle_from_url(url: str | None) -> str | None:
    """Extract a Twitter/X handle from a profile URL (ignore intent/share/status paths)."""
    if not url:
        return None
    m = _HANDLE.search(url)
    if not m:
        return None
    h = m.group(1)
    return None if h.lower() in _BAD_PATHS else h


@dataclass
class TwitterProfile:
    handle: str
    available: bool = False
    name: str | None = None
    followers: int | None = None
    following: int | None = None
    tweets: int | None = None
    age_days: int | None = None
    verified: bool = False
    description: str | None = None
    avatar_url: str | None = None
    url: str | None = None


class TwitterPublicClient(BaseClient):
    def __init__(self):
        super().__init__(BASE, timeout=15.0, min_interval_s=0.3, cache_ttl_s=600.0,
                         headers={"accept": "application/json", "user-agent": "rugcheck.ai/1.0"})

    async def profile(self, handle: str) -> TwitterProfile:
        handle = (handle or "").lstrip("@")
        if not handle:
            return TwitterProfile(handle=handle, available=False)
        try:
            d = await self.get_json(f"/{handle}", cache_key=f"tw:{handle.lower()}")
        except Exception:
            return TwitterProfile(handle=handle, available=False)
        u = (d or {}).get("user") or {}
        if not u or not u.get("screen_name"):
            return TwitterProfile(handle=handle, available=False)
        return TwitterProfile(
            handle=u.get("screen_name") or handle,
            available=True,
            name=u.get("name"),
            followers=_i(u.get("followers")),
            following=_i(u.get("following")),
            tweets=_i(u.get("tweets")),
            age_days=_age_days(u.get("joined")),
            verified=bool(u.get("verified") or u.get("is_blue_verified")),
            description=u.get("description"),
            avatar_url=u.get("avatar_url"),
            url=f"https://x.com/{u.get('screen_name') or handle}",
        )


def authenticity(p: TwitterProfile, cfg) -> tuple[float, str, list[str]]:
    """(score 0..1, verdict, flags) from a profile. Pure → unit-tested. Higher = more credible identity."""
    if not p.available:
        return 0.0, "no_profile", ["no_twitter"]
    flags: list[str] = []
    score = 0.4  # baseline for "the linked account actually exists"
    if p.age_days is not None:
        if p.age_days < cfg.min_age_days:
            flags.append("brand_new_account")
        else:
            score += min(0.25, p.age_days / (cfg.mature_age_days) * 0.25)
    if p.followers is not None:
        if p.followers < cfg.min_followers:
            flags.append("low_followers")
        else:
            score += 0.2
    if p.tweets is not None:
        if p.tweets < cfg.min_tweets:
            flags.append("few_tweets")
        else:
            score += 0.1
    if p.verified:
        score += 0.15
    score = max(0.0, min(1.0, score))
    # verdict: any tell keeps it out of "credible"; two strong tells = inauthentic
    if "brand_new_account" in flags and "low_followers" in flags:
        verdict = "inauthentic"
    elif flags:
        verdict = "weak"
    elif score >= 0.7:
        verdict = "credible"
    else:
        verdict = "ok"
    return round(score, 3), verdict, flags


def _i(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _age_days(joined) -> int | None:
    """fxtwitter `joined` is a Twitter date string like 'Sun Jul 31 02:25:52 +0000 2022'."""
    if not joined:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(str(joined), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0, (datetime.now(timezone.utc) - dt).days)
        except ValueError:
            continue
    return None
