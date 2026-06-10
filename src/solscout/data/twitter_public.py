"""Keyless Twitter/X intel (ADR-041 profile + ADR-046 timeline) — the project's "social identity" check.

Two zero-login public endpoints:
  - fxtwitter (`api.fxtwitter.com/<handle>`): profile facts — age (joined), followers, tweets, verified,
    plus (ADR-046) the snowflake user id, the bio website, media/likes counts.
  - Twitter syndication SSR (`syndication.twitter.com/srv/timeline-profile/...`): the account's recent
    timeline embedded as `__NEXT_DATA__` JSON → real tweet TEXTS, free.

From those we derive the signals scammers can't fake cheaply (all pure, unit-tested):
  serial CA-shilling (tweets pushing OTHER token mints), whether this mint was ever posted, posting
  cadence/bursts (bot tells), snowflake-id vs claimed join-date mismatch (recycled/forged identity),
  and bio-website ↔ DexScreener-website consistency. Fail-open: no data → no flag, never a crash.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .base import BaseClient

BASE = "https://api.fxtwitter.com"
SYNDICATION = "https://syndication.twitter.com/srv/timeline-profile/screen-name"
_HANDLE = re.compile(r"(?:twitter\.com|x\.com)/(?:#!/)?@?([A-Za-z0-9_]{1,15})", re.I)
_BAD_PATHS = {"i", "intent", "share", "home", "search", "hashtag", "explore", "messages"}
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_CA = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")  # base58 mint-shaped strings in tweet text
# canonical mints people legitimately paste in tweets — never count these as "other token" shills
_COMMON_MINTS = {
    "So11111111111111111111111111111111111111112",   # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}
_TW_EPOCH_MS = 1_288_834_974_657  # Twitter snowflake epoch (2010-11-04)


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
    # ADR-046 — fxtwitter already returns these; all defaulted so existing constructors stay valid
    user_id: str | None = None  # snowflake id → independent creation-date estimate
    website: str | None = None  # bio website (expanded) → cross-check vs DexScreener's site
    verification_type: str | None = None
    media_count: int | None = None
    likes: int | None = None


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
            user_id=(str(u["id"]) if u.get("id") is not None else None),
            website=((u.get("website") or {}).get("url") or None),
            verification_type=((u.get("verification") or {}).get("type") or None),
            media_count=_i(u.get("media_count")),
            likes=_i(u.get("likes")),
        )

    _TL_TTL = 1800.0  # timelines change slowly; longer TTL also respects the endpoint's tight IP quota

    async def timeline(self, handle: str) -> list[dict]:
        """Recent tweets from the public syndication SSR page (ADR-046) — keyless, parsed result cached.
        Returns [{"text","created_at","id"}] (often ~100 entries); [] on ANY failure (fail open).
        NOTE: the endpoint rate-limits per IP aggressively (429s for many minutes after a short burst) —
        one call per fresh token check + this cache keeps us comfortably under it; a 429 just means the
        timeline section is absent for that report, never an error."""
        handle = (handle or "").lstrip("@")
        if not handle:
            return []
        key = f"tl:{handle.lower()}"
        hit = self._cache.get(key)
        if hit and (time.monotonic() - hit[0]) < self._TL_TTL:
            return hit[1]
        try:
            # deliberate SINGLE attempt (no tenacity retry): retrying a 429 here only burns more of the
            # per-IP quota. Throttled like every other call; any non-200 → [] (fail open).
            await self._throttle()
            resp = await self._client.get(f"{SYNDICATION}/{handle}")
            if resp.status_code != 200:
                return []
            html = resp.text
        except Exception:
            return []
        tweets = parse_timeline_html(html)
        self._cache[key] = (time.monotonic(), tweets)
        return tweets


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


# — ADR-046: pure timeline-derived signals (all unit-tested; tolerate any malformed input) —

def parse_timeline_html(html: str) -> list[dict]:
    """Extract tweets from the syndication SSR page's `__NEXT_DATA__` JSON. [] on malformed anything."""
    try:
        m = _NEXT_DATA.search(html or "")
        if not m:
            return []
        d = json.loads(m.group(1))
        entries = ((((d.get("props") or {}).get("pageProps") or {}).get("timeline") or {}).get("entries")) or []
        out = []
        for e in entries:
            t = ((e or {}).get("content") or {}).get("tweet") or {}
            txt = t.get("full_text") or t.get("text")
            if not txt:
                continue
            out.append({"text": str(txt), "created_at": t.get("created_at"),
                        "id": str(t.get("id_str") or t.get("id") or "")})
        return out
    except Exception:
        return []


def tweet_ca_signals(tweets: list[dict], mint: str) -> dict:
    """Mint-shaped (base58) strings in the tweet texts. Mentions of OUR mint = the account actually posted
    this CA (good). DISTINCT *other* mints = the account pumps token after token = serial shill (bad).
    Common mints (WSOL/USDC/USDT) never count. Pure."""
    mentions, others = 0, set()
    for t in tweets:
        for ca in _CA.findall(t.get("text") or ""):
            if ca == mint:
                mentions += 1
            elif ca not in _COMMON_MINTS:
                others.add(ca)
    return {"mint_mentions": mentions, "other_cas": sorted(others), "other_ca_count": len(others)}


def posting_cadence(tweets: list[dict], now: datetime | None = None) -> dict:
    """Tweets/day over the visible window + the max burst inside any rolling hour (bot tell) + how long
    since the last tweet. None where the dates can't be read. Pure."""
    times = sorted(d for d in (_parse_tw_date(t.get("created_at")) for t in tweets) if d is not None)
    if not times:
        return {"count": len(tweets), "per_day": None, "burst_max_1h": None,
                "last_tweet_age_days": None, "span_days": None}
    now = now or datetime.now(timezone.utc)
    span_days = max(1 / 24, (times[-1] - times[0]).total_seconds() / 86400)
    per_day = round(len(times) / span_days, 2) if len(times) > 1 else None
    burst, j = 1, 0
    for i in range(len(times)):
        while times[i] - times[j] > timedelta(hours=1):
            j += 1
        burst = max(burst, i - j + 1)
    return {"count": len(times), "per_day": per_day, "burst_max_1h": burst,
            "last_tweet_age_days": max(0, (now - times[-1]).days), "span_days": round(span_days, 1)}


def snowflake_age_days(user_id, now: datetime | None = None) -> int | None:
    """Twitter ids are snowflakes (since Nov 2010): (id >> 22) + epoch = REAL creation time, independent
    of whatever the profile claims. Pre-snowflake sequential ids (< ~1e12) aren't derivable → None. Pure."""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    if uid < 1_000_000_000_000:  # sequential-era id (pre-snowflake) — can't derive
        return None
    try:
        dt = datetime.fromtimestamp(((uid >> 22) + _TW_EPOCH_MS) / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    now = now or datetime.now(timezone.utc)
    if dt > now or dt.year < 2010:
        return None
    return max(0, (now - dt).days)


def id_joined_mismatch_days(user_id, age_days: int | None, now: datetime | None = None) -> int | None:
    """|snowflake-derived age − claimed joined age|. Large = forged/recycled identity metadata. None when
    either side is unknown (pre-snowflake accounts, missing joined date). Pure."""
    sf = snowflake_age_days(user_id, now)
    if sf is None or age_days is None:
        return None
    return abs(sf - age_days)


def _norm_domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
    except ValueError:
        return None
    host = host.lower().removeprefix("www.")
    return host or None


def website_matches(profile_site: str | None, market_websites: list[str] | None) -> bool | None:
    """Does the X bio's website match ANY site DexScreener lists for the token (by host)? None when either
    side is missing (no claim → no flag). A mismatch = the account may belong to a different project. Pure."""
    a = _norm_domain(profile_site)
    bs = [d for d in (_norm_domain(w) for w in (market_websites or [])) if d]
    if a is None or not bs:
        return None
    return a in bs


def pick_excerpts(tweets: list[dict], mint: str, count: int, max_len: int) -> list[str]:
    """The most forensically useful tweet texts: ones mentioning OUR mint first, then the newest. Pure."""
    with_mint = [t for t in tweets if mint in (t.get("text") or "")]
    rest = [t for t in tweets if t not in with_mint]
    chosen = (with_mint + rest)[: max(0, count)]
    return [(t.get("text") or "")[:max_len] for t in chosen]


def _parse_tw_date(s) -> datetime | None:
    if not s:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(str(s), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


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
