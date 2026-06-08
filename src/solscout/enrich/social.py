"""Stage 2 — social/OSINT authenticity. Pulls the project's linked Twitter (TweetScout) + public
Telegram channel (web-preview, zero login), produces a SocialReport + chatter for the LLM.

social_score is a soft signal; Telegram ALONE is capped so it can't open the BUY gate (scammers have
channels too). The strong gate remains smart-money. Sub-signal weights are v1 constants (could move to
config). handle_reuse / ca_tweet need specific TweetScout endpoints — wired when a key is available (TODO).
"""

from __future__ import annotations

from ..core.config import Config
from ..core.models import SocialReport
from ..data.telegram_web import TelegramWebClient
from ..data.tweetscout import TweetScoutClient, account_age_days

_W = {"age": 0.35, "followers": 0.30, "ca_tweet": 0.20, "telegram": 0.15}


async def assess(
    market, cfg: Config, *, tg_client: TelegramWebClient, ts_client: TweetScoutClient
) -> SocialReport | None:
    if market is None:
        return None
    handle = _handle(market.social_url("twitter"))
    channel = _handle(market.social_url("telegram"))
    if not handle and not channel:
        return None

    report = SocialReport(mint=market.mint, twitter_handle=handle)
    chatter: list[str] = []
    tg_active: float | None = None

    if channel:
        ch = await tg_client.fetch_channel(channel)
        if ch.exists:
            chatter += ch.messages
            tg_active = 1.0 if len(ch.messages) >= 10 else (0.6 if ch.messages else 0.3)
            if ch.subscribers is not None:
                report.flags.append(f"tg_subs={ch.subscribers}")

    if ts_client.available and handle:
        info = await ts_client.info(handle)
        if info:
            report.twitter_age_days = account_age_days(info)
            fc = info.get("followers_count") or info.get("followers")
            if isinstance(fc, (int, float)):
                report.notable_followers = int(fc)
        sc = await ts_client.score(handle)
        if sc is not None:
            report.flags.append(f"tweetscout_score={sc:.0f}")

    report.chatter_texts = chatter[:40]
    report.social_score = _score(report, tg_active, cfg)
    return report


def _handle(url: str | None) -> str | None:
    if not url:
        return None
    return (url.rstrip("/").split("/")[-1].lstrip("@").split("?")[0]) or None


def _score(report: SocialReport, tg_active: float | None, cfg: Config) -> float | None:
    subs: list[float] = []
    weights: list[float] = []
    if report.twitter_age_days is not None:
        subs.append(min(1.0, report.twitter_age_days / 365))
        weights.append(_W["age"])
    if report.notable_followers is not None:
        subs.append(min(1.0, report.notable_followers / max(1, cfg.social.min_followers) / 5))
        weights.append(_W["followers"])
    if report.ca_tweet_verified is not None:
        subs.append(1.0 if report.ca_tweet_verified else 0.0)
        weights.append(_W["ca_tweet"])
    if tg_active is not None:
        subs.append(tg_active)
        weights.append(_W["telegram"])
    if not subs:
        return None
    score = sum(s * w for s, w in zip(subs, weights)) / sum(weights)
    # Telegram alone must not open the BUY gate → cap when no Twitter authenticity is present.
    if report.twitter_age_days is None and report.ca_tweet_verified is None:
        score = min(score, 0.5)
    return round(score, 3)
