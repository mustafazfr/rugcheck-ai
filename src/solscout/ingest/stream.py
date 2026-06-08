"""Stage 0 — the funnel's candidate source. FREE & keyless (ADR-036). pump.fun is gone.

`stream_candidates` merges two free feeds and yields tokens that ALREADY trade on a DEX with real
liquidity (so the rug/concentration/maturity checks are meaningful immediately):
  - GeckoTerminal new_pools : the freshest Solana pools (our main fresh-token feed)
  - DexScreener promoted     : newly profiled/boosted tokens

Each candidate is liquidity-pre-screened (free) before it ever costs a Helius credit.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ..core.models import CandidateSource, TokenCandidate
from ..data.dexscreener import DexScreenerClient
from ..data.geckoterminal import GeckoTerminalClient


async def stream_candidates(
    gecko: GeckoTerminalClient,
    dex: DexScreenerClient,
    *,
    poll_s: int = 30,
    min_liquidity_usd: float = 15000,
    new_pools_pages: int = 1,
    limit: int = 0,
) -> AsyncIterator[TokenCandidate]:
    """Yield deduped, liquidity-screened TokenCandidates forever (limit=0) or until `limit` yielded.
    Clients are passed in (shared, opened/closed by the caller) so one poll loop reuses connections."""
    seen: set[str] = set()
    n = 0
    while True:
        try:
            # carry the GeckoPool (has UNIQUE buyers/sellers) for gecko-sourced candidates so the funnel's
            # manipulation checks don't need an extra call; dex-promoted candidates carry None.
            candidates: list[tuple[str, object]] = []
            for p in await gecko.new_pools(new_pools_pages):
                if p.liquidity_usd is None or p.liquidity_usd >= min_liquidity_usd:
                    candidates.append((p.mint, p))
            for mint in await dex.promoted_tokens():
                candidates.append((mint, None))

            for mint, pool in candidates:
                if mint in seen:
                    continue
                seen.add(mint)
                mk = await dex.get_token(mint)  # authoritative market (most-liquid pair)
                if not mk or not mk.liquidity_usd or mk.liquidity_usd < min_liquidity_usd:
                    continue
                meta = {"src": "gecko/dex", "dex": mk.dex, "liq": mk.liquidity_usd, "symbol": mk.symbol or "?"}
                if pool is not None:  # stash GeckoTerminal organic-flow signals
                    meta["buyers_h24"] = pool.buyers_h24
                    meta["sellers_h24"] = pool.sellers_h24
                    meta["price_change_24h"] = pool.price_change_24h
                yield TokenCandidate(mint=mint, source=CandidateSource.LAUNCH, raw_meta=meta)
                n += 1
                if limit and n >= limit:
                    return
        except Exception:
            pass
        await asyncio.sleep(poll_s)
