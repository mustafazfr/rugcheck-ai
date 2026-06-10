"""Jupiter clients — FREE, no key.

1) Quote: honeypot / sell-simulation guard (ADR-012). We never submit a transaction (that's the LOCKED
   LiveExecutor's job). We only ROUTE-QUOTE both ways: buy SOL→token, then sell back token→SOL. If the
   sell route is missing, or the round-trip loses more than `max_buy_sell_tax_pct`, the token is likely
   a honeypot / high-tax trap → sell_ok=False.
2) Token intel (ADR-046): lite-api `tokens/v2/search` — Jupiter's OWN read of a mint: audit booleans
   (mint/freeze disabled, dev balance, devMints = prior launches by the dev!), organicScore (their
   wash-trade detector), isVerified/tags, holderCount. A THIRD independent rug source next to
   RugCheck + GoPlus, and a free substitute for the 10cr Helius deployer-history call.

Pure parsing (`round_trip_tax_pct`, `parse_token_info`) is unit-tested; network calls fail SAFE
(return unknown/None, never a false flag). Docs: https://station.jup.ag/docs
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import BaseClient

WSOL = "So11111111111111111111111111111111111111112"
QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
TOKENS_URL = "https://lite-api.jup.ag/tokens/v2/search"


@dataclass
class JupiterTokenInfo:
    """What Jupiter knows about a mint (all optional — absence is never a red flag by itself)."""

    available: bool = False
    verified: bool = False
    tags: list[str] = field(default_factory=list)
    organic_score: float | None = None
    organic_label: str | None = None  # "high" | "medium" | "low"
    holder_count: int | None = None
    mint_auth_disabled: bool | None = None
    freeze_auth_disabled: bool | None = None
    top_holders_pct: float | None = None
    dev_balance_pct: float | None = None
    dev_mints: int | None = None  # prior token launches by the dev — Jupiter already counted them
    dev_wallet: str | None = None
    first_pool_created_at: str | None = None
    num_traders_24h: int | None = None
    num_net_buyers_24h: int | None = None
    buy_volume_24h: float | None = None
    buy_organic_volume_24h: float | None = None


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse_token_info(payload, mint: str) -> JupiterTokenInfo | None:
    """v2 search returns a list; match OUR mint exactly (search is fuzzy). None = Jupiter doesn't know it.
    Defensive: unknown/missing fields stay None — never fabricated. Pure."""
    if not isinstance(payload, list):
        return None
    entry = next((e for e in payload if isinstance(e, dict) and e.get("id") == mint), None)
    if entry is None:
        return None
    audit = entry.get("audit") or {}
    s24 = entry.get("stats24h") or {}
    hc = _num(entry.get("holderCount"))
    dm = _num(audit.get("devMints"))
    nt = _num(s24.get("numTraders"))
    nb = _num(s24.get("numNetBuyers"))
    return JupiterTokenInfo(
        available=True,
        verified=bool(entry.get("isVerified")),
        tags=[t for t in (entry.get("tags") or []) if isinstance(t, str)],
        organic_score=_num(entry.get("organicScore")),
        organic_label=(entry.get("organicScoreLabel") or None),
        holder_count=int(hc) if hc is not None else None,
        mint_auth_disabled=(bool(audit["mintAuthorityDisabled"]) if "mintAuthorityDisabled" in audit else None),
        freeze_auth_disabled=(bool(audit["freezeAuthorityDisabled"]) if "freezeAuthorityDisabled" in audit else None),
        top_holders_pct=_num(audit.get("topHoldersPercentage")),
        dev_balance_pct=_num(audit.get("devBalancePercentage")),
        dev_mints=int(dm) if dm is not None else None,
        dev_wallet=entry.get("dev") or None,
        first_pool_created_at=((entry.get("firstPool") or {}).get("createdAt") or None),
        num_traders_24h=int(nt) if nt is not None else None,
        num_net_buyers_24h=int(nb) if nb is not None else None,
        buy_volume_24h=_num(s24.get("buyVolume")),
        buy_organic_volume_24h=_num(s24.get("buyOrganicVolume")),
    )


def round_trip_tax_pct(sol_in_lamports: int, sol_out_lamports: int) -> float:
    """Percent of SOL lost on a buy→sell round trip. 0 = perfect, 100 = everything lost. Pure."""
    if sol_in_lamports <= 0:
        return 0.0
    return max(0.0, 100.0 * (1.0 - sol_out_lamports / sol_in_lamports))


class JupiterClient(BaseClient):
    def __init__(self):
        super().__init__("", timeout=15.0, min_interval_s=0.3, cache_ttl_s=30.0)

    async def _quote(
        self, input_mint: str, output_mint: str, amount: int, slippage_bps: int
    ) -> dict | None:
        try:
            d = await self.get_json(
                QUOTE_URL,
                params={
                    "inputMint": input_mint,
                    "outputMint": output_mint,
                    "amount": str(amount),
                    "slippageBps": str(slippage_bps),
                    "onlyDirectRoutes": "false",
                },
                cache_key=f"q:{input_mint}:{output_mint}:{amount}:{slippage_bps}",
            )
        except Exception:
            return None
        # v6 returns the quote object directly (has 'outAmount'); error responses won't.
        return d if isinstance(d, dict) and d.get("outAmount") else None

    async def sell_simulation(
        self,
        mint: str,
        *,
        probe_lamports: int = 10_000_000,
        slippage_bps: int = 300,
    ) -> tuple[bool | None, float | None]:
        """(sell_ok, round_trip_tax_pct). probe_lamports default = 0.01 SOL.
        Returns (None, None) if routing is unavailable (don't penalize — fail safe)."""
        buy = await self._quote(WSOL, mint, probe_lamports, slippage_bps)
        if not buy:
            return None, None  # can't even price a buy → unknown, stay neutral
        tokens_out = int(buy.get("outAmount") or 0)
        if tokens_out <= 0:
            return None, None
        sell = await self._quote(mint, WSOL, tokens_out, slippage_bps)
        if not sell:
            return False, None  # buyable but NOT sellable → classic honeypot
        sol_back = int(sell.get("outAmount") or 0)
        tax = round_trip_tax_pct(probe_lamports, sol_back)
        return True, round(tax, 2)

    async def token_info(self, mint: str) -> JupiterTokenInfo | None:
        """Jupiter's own token intel (ADR-046) — keyless, cached. None on any failure (fail open)."""
        try:
            d = await self.get_json(TOKENS_URL, params={"query": mint}, cache_key=f"ti:{mint}")
        except Exception:
            return None
        return parse_token_info(d, mint)
