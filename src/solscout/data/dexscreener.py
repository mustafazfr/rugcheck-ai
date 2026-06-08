"""DexScreener client (keyless) — price, liquidity, socials. Fallback for price: Birdeye/Jupiter.

Docs: https://docs.dexscreener.com/api/reference
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.models import Social, TokenMarket
from .base import BaseClient

BASE = "https://api.dexscreener.com"


class DexScreenerClient(BaseClient):
    def __init__(self):
        super().__init__(BASE, timeout=15.0, min_interval_s=0.3, cache_ttl_s=15.0)

    async def get_token(self, mint: str) -> TokenMarket | None:
        """Most-liquid pair where `mint` is the base token, mapped to TokenMarket."""
        data = await self.get_json(f"/latest/dex/tokens/{mint}", cache_key=f"tok:{mint}")
        pairs = (data or {}).get("pairs") or []
        # ONLY pairs where our mint is the base token. NO fallback to unrelated pairs — that returned
        # bogus liquidity/price (e.g. $2.4M @ $1.001) for fresh tokens and would corrupt paper fills.
        ours = [p for p in pairs if (p.get("baseToken") or {}).get("address") == mint]
        if not ours:
            return None
        p = max(ours, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
        base = p.get("baseToken") or {}
        info = p.get("info") or {}

        socials = [
            Social(type=s["type"], url=s["url"])
            for s in (info.get("socials") or [])
            if s.get("url")
        ]
        websites = [w["url"] for w in (info.get("websites") or []) if w.get("url")]

        created = None
        if p.get("pairCreatedAt"):
            created = datetime.fromtimestamp(p["pairCreatedAt"] / 1000, tz=timezone.utc)

        txns24 = (p.get("txns") or {}).get("h24") or {}
        return TokenMarket(
            mint=mint,
            name=base.get("name"),
            symbol=base.get("symbol"),
            price_usd=_f(p.get("priceUsd")),
            liquidity_usd=_f((p.get("liquidity") or {}).get("usd")),
            fdv=_f(p.get("fdv")),
            market_cap=_f(p.get("marketCap")),
            volume_24h=_f((p.get("volume") or {}).get("h24")),
            pair_created_at=created,
            dex=p.get("dexId"),
            pair_address=p.get("pairAddress"),
            txns_buys_h24=_i(txns24.get("buys")),
            txns_sells_h24=_i(txns24.get("sells")),
            price_change_h24=_f((p.get("priceChange") or {}).get("h24")),
            socials=socials,
            websites=websites,
        )

    async def get_price_usd(self, mint: str) -> float | None:
        m = await self.get_token(mint)
        return m.price_usd if m else None

    async def promoted_tokens(self) -> list[str]:
        """Newly profiled/boosted Solana tokens (free, no key). These are freshly-listed / actively-promoted
        coins — a real candidate stream, NOT the old broad 'SOL/USDC' search (which returned stablecoins and
        established blue-chips = noise, ADR-036). The caller's liquidity filter keeps only the real ones."""
        out: list[str] = []
        for path in (
            "/token-profiles/latest/v1",
            "/token-boosts/latest/v1",
            "/token-boosts/top/v1",
        ):
            try:
                data = await self.get_json(path, cache_key=path)
            except Exception:
                continue
            for x in data if isinstance(data, list) else []:
                if isinstance(x, dict) and x.get("chainId") == "solana" and x.get("tokenAddress"):
                    out.append(x["tokenAddress"])
        return list(dict.fromkeys(out))  # dedupe, preserve order


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
