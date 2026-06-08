"""GeckoTerminal API client — FREE, no key (~30 req/min). The funnel's candidate + winners source (ADR-036).

Two roles, both keyless:
  - new_pools()      : freshest Solana pools → the funnel's candidate stream (real DEX listings)
  - trending_pools() : recent movers → the "winners" we mine for recurring smart-money wallets (discovery)

Docs: https://api.geckoterminal.com/docs/index.html  (api.geckoterminal.com/api/v2)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .base import BaseClient

BASE = "https://api.geckoterminal.com/api/v2"


@dataclass(frozen=True)
class GeckoPool:
    """A DEX pool as GeckoTerminal sees it (base token = the coin we'd trade)."""

    mint: str  # base-token mint
    quote_mint: str | None
    name: str | None
    pool_address: str | None
    dex: str | None
    liquidity_usd: float | None  # reserve_in_usd
    fdv_usd: float | None
    market_cap_usd: float | None
    volume_24h: float | None
    price_change_24h: float | None
    created_at: datetime | None
    buyers_h24: int | None = None   # UNIQUE buyer wallets (24h) — the organic-flow signal we want
    sellers_h24: int | None = None  # UNIQUE seller wallets (24h) — lopsided vs buyers = manipulation
    buys_h24: int | None = None     # buy TXN count (24h)
    sells_h24: int | None = None    # sell TXN count (24h)

    def age_days(self) -> float | None:
        if not self.created_at:
            return None
        return max(0.0, (datetime.now(timezone.utc) - self.created_at).total_seconds() / 86400)


class GeckoTerminalClient(BaseClient):
    def __init__(self, network: str = "solana"):
        # free tier ≈ 30 req/min → ~2.1s between calls keeps us safely under it; short cache for polling
        super().__init__(
            BASE,
            timeout=20.0,
            min_interval_s=2.1,
            cache_ttl_s=20.0,
            headers={"Accept": "application/json;version=20230302"},
        )
        self.network = network

    async def new_pools(self, pages: int = 1) -> list[GeckoPool]:
        """Freshest pools on the network (newest first). Each page ≈ 20 pools."""
        return await self._pools("new_pools", pages)

    async def trending_pools(self, pages: int = 1) -> list[GeckoPool]:
        """Trending pools = recent movers. These are the 'winners' we mine for smart-money wallets."""
        return await self._pools("trending_pools", pages)

    async def token_pool(self, mint: str) -> GeckoPool | None:
        """The most-liquid pool for a token, WITH unique buyers/sellers (the manipulation signal). One free
        call, cached. Returns None if the token has no GeckoTerminal pool yet."""
        path = f"/networks/{self.network}/tokens/{mint}/pools?page=1"
        try:
            data = await self.get_json(path, cache_key=f"tokpool:{self.network}:{mint}")
        except Exception:
            return None
        pools = [
            p
            for p in (_parse_pool(row, self.network) for row in (data or {}).get("data") or [])
            if p and p.mint == mint
        ]
        if not pools:
            return None
        return max(pools, key=lambda x: x.liquidity_usd or 0)

    async def _pools(self, kind: str, pages: int) -> list[GeckoPool]:
        out: list[GeckoPool] = []
        for page in range(1, max(1, pages) + 1):
            path = f"/networks/{self.network}/{kind}?page={page}"
            try:
                data = await self.get_json(path, cache_key=f"{kind}:{self.network}:{page}")
            except Exception:
                break
            rows = (data or {}).get("data") or []
            for row in rows:
                pool = _parse_pool(row, self.network)
                if pool and pool.mint:
                    out.append(pool)
            if len(rows) < 20:  # last page
                break
        return out


def _parse_pool(row: dict, network: str) -> GeckoPool | None:
    attrs = (row or {}).get("attributes") or {}
    rels = (row or {}).get("relationships") or {}
    base = _rel_token(rels.get("base_token"), network)
    if not base:
        return None
    tx24 = (attrs.get("transactions") or {}).get("h24") or {}
    return GeckoPool(
        mint=base,
        quote_mint=_rel_token(rels.get("quote_token"), network),
        name=attrs.get("name"),
        pool_address=attrs.get("address"),
        dex=((rels.get("dex") or {}).get("data") or {}).get("id"),
        liquidity_usd=_f(attrs.get("reserve_in_usd")),
        fdv_usd=_f(attrs.get("fdv_usd")),
        market_cap_usd=_f(attrs.get("market_cap_usd")),
        volume_24h=_f((attrs.get("volume_usd") or {}).get("h24")),
        price_change_24h=_f((attrs.get("price_change_percentage") or {}).get("h24")),
        created_at=_dt(attrs.get("pool_created_at")),
        buyers_h24=_i(tx24.get("buyers")),
        sellers_h24=_i(tx24.get("sellers")),
        buys_h24=_i(tx24.get("buys")),
        sells_h24=_i(tx24.get("sells")),
    )


def _rel_token(rel: dict | None, network: str) -> str | None:
    """relationships.<token>.data.id is 'solana_<MINT>' — strip the network prefix to the bare mint."""
    tid = ((rel or {}).get("data") or {}).get("id")
    if not tid:
        return None
    prefix = f"{network}_"
    return tid[len(prefix) :] if tid.startswith(prefix) else tid


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _i(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _dt(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
