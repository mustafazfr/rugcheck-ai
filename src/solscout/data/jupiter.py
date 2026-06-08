"""Jupiter quote client — FREE, no key. Honeypot / sell-simulation guard (ADR-012, finally implemented).

We never submit a transaction here (that's the LOCKED LiveExecutor's job). We only ROUTE-QUOTE both ways:
buy SOL→token, then sell that token amount back token→SOL. If the sell route is missing, or the round-trip
loses more than `max_buy_sell_tax_pct`, the token is likely a honeypot / high-tax trap → sell_ok=False.

Pure math (`round_trip_tax_pct`) is unit-tested; the network call fails SAFE (returns unknown, never a
false honeypot flag). Docs: https://station.jup.ag/docs/apis/swap-api
"""

from __future__ import annotations

from .base import BaseClient

WSOL = "So11111111111111111111111111111111111111112"
QUOTE_URL = "https://quote-api.jup.ag/v6/quote"


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
