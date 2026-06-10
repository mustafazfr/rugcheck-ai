"""RDAP domain-age client (ADR-046) — FREE, keyless, the registries' own protocol.

`rdap.org/domain/<domain>` redirects to the authoritative registry RDAP server, which returns the
domain's lifecycle events — including `registration`. A project "website" whose domain was registered
days ago is a classic rug tell (real projects have real domains; scammers spin up throwaways).

One GET per domain, cached a day, fail-open: no answer → no flag. Pure parsing is unit-tested.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from .base import BaseClient

BASE = "https://rdap.org"
# Two-level public suffixes we actually meet on meme sites — keeps `registrable_domain` honest without
# pulling in the full Public Suffix List (overkill for a soft signal).
_TWO_LEVEL_TLDS = {
    "co.uk", "org.uk", "ac.uk", "com.au", "net.au", "org.au", "co.jp", "ne.jp", "or.jp",
    "com.br", "com.cn", "com.tr", "com.mx", "co.in", "co.kr", "com.sg", "com.hk",
}
# hosts that are never the PROJECT's own site (socials/dex infra) — don't RDAP these
INFRA_HOSTS = {
    "x.com", "twitter.com", "t.me", "telegram.me", "discord.gg", "discord.com", "medium.com",
    "github.com", "youtube.com", "youtu.be", "instagram.com", "tiktok.com", "linktr.ee",
    "dexscreener.com", "pump.fun", "raydium.io", "jup.ag", "birdeye.so", "geckoterminal.com",
    "coinmarketcap.com", "coingecko.com", "docs.google.com", "gitbook.io", "notion.site",
}


def registrable_domain(url: str | None) -> str | None:
    """Hostname → the registrable domain RDAP understands ('app.pepe.lol' → 'pepe.lol'). None for
    missing/unparseable URLs or known social/dex infra hosts (their age says nothing about the token)."""
    if not url:
        return None
    try:
        host = (urlparse(url if "://" in url else f"https://{url}").hostname or "").lower()
    except ValueError:
        return None
    host = host.removeprefix("www.")
    if not host or "." not in host:
        return None
    if host in INFRA_HOSTS or any(host.endswith("." + i) for i in INFRA_HOSTS):
        return None
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LEVEL_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def registration_age_days(rdap_json: dict | None, now: datetime | None = None) -> int | None:
    """events[].eventAction == 'registration' → days since. None when absent/unreadable. Pure."""
    if not isinstance(rdap_json, dict):
        return None
    now = now or datetime.now(timezone.utc)
    for ev in rdap_json.get("events") or []:
        if not isinstance(ev, dict) or ev.get("eventAction") != "registration":
            continue
        raw = str(ev.get("eventDate") or "")
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (now - dt).days)
    return None


class RdapClient(BaseClient):
    def __init__(self):
        # 8s timeout: registries can be slow; this is a best-effort soft signal, never worth a long wait.
        super().__init__(BASE, timeout=8.0, min_interval_s=0.5, cache_ttl_s=86_400.0,
                         follow_redirects=True,
                         headers={"accept": "application/rdap+json, application/json"})

    async def domain(self, domain: str) -> dict | None:
        if not domain:
            return None
        try:
            d = await self.get_json(f"/domain/{domain}", cache_key=f"rd:{domain.lower()}")
        except Exception:
            return None
        return d if isinstance(d, dict) else None
