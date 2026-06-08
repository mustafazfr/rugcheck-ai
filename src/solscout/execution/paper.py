"""PaperExecutor — simulates fills with a LIQUIDITY-AWARE price-impact model (ADR-037).

The old model filled at the quoted price minus a flat 3% slip — so it booked fictional +300% sells on
illiquid/rugged pools you could never actually exit. Now we model a constant-product (x·y=k) AMM:

  selling a position worth `v` USD (at mid price) into a pool with one-sided quote reserve `Q ≈ liquidity/2`
  realizes  v · Q/(v+Q)   (effective price = mid · Q/(v+Q)).

As liquidity → 0 (a rug), the realized value → 0 automatically — the honest "you're stuck holding worthless
tokens" outcome. Buys pay the inverse impact. When liquidity is unknown (None) we fall back to the old flat
slippage so injected-price unit tests still hold. Deterministic + offline-testable. No keys, no risk.
"""

from __future__ import annotations

from ..core.models import Fill, Position


def impact_factor(value_usd: float, liquidity_usd: float | None) -> float:
    """Fraction of mid-price value actually realizable for a trade of `value_usd` into a pool of
    `liquidity_usd`. 1.0 = no impact (tiny trade / deep pool), →0 = trade dwarfs the pool / rugged. Pure."""
    if not liquidity_usd or liquidity_usd <= 0:
        return 0.0  # no pool / rug → you can't get value out
    q = liquidity_usd / 2.0  # one-sided quote reserve of a balanced AMM
    if value_usd <= 0:
        return 1.0
    return q / (value_usd + q)


class PaperExecutor:
    paper = True

    def __init__(self, fee_bps: int = 25):
        self.fee_bps = fee_bps  # swap fee + priority-fee drag, in basis points

    async def buy(
        self,
        mint: str,
        sol_amount: float,
        token_price_usd: float,
        sol_usd: float,
        max_slippage_bps: int,
        liquidity_usd: float | None = None,
    ) -> Fill:
        usd_in = sol_amount * sol_usd * (1 - self.fee_bps / 10_000)
        if liquidity_usd is not None:
            # buying pushes the price up → fewer tokens (inverse impact)
            tokens_out = (usd_in / token_price_usd) * impact_factor(usd_in, liquidity_usd) if token_price_usd > 0 else 0.0
        else:
            eff = token_price_usd * (1 + max_slippage_bps / 10_000)
            tokens_out = usd_in / eff if eff > 0 else 0.0
        eff_price = usd_in / tokens_out if tokens_out > 0 else token_price_usd
        return Fill(
            mint=mint,
            side="buy",
            sol_amount=sol_amount,
            token_amount=tokens_out,
            price_usd=eff_price,
            paper=True,
        )

    async def sell(
        self,
        position: Position,
        token_price_usd: float,
        sol_usd: float,
        max_slippage_bps: int,
        liquidity_usd: float | None = None,
    ) -> Fill:
        value_at_mid = position.token_amount * token_price_usd
        if liquidity_usd is not None:
            # dumping the whole position craters an illiquid pool — this is where the fake +300% dies
            usd_out = value_at_mid * impact_factor(value_at_mid, liquidity_usd) * (1 - self.fee_bps / 10_000)
        else:
            eff = token_price_usd * (1 - max_slippage_bps / 10_000)
            usd_out = position.token_amount * eff * (1 - self.fee_bps / 10_000)
        sol_out = usd_out / sol_usd if sol_usd > 0 else 0.0
        eff_price = usd_out / position.token_amount if position.token_amount > 0 else token_price_usd
        return Fill(
            mint=position.mint,
            side="sell",
            sol_amount=sol_out,
            token_amount=position.token_amount,
            price_usd=eff_price,
            paper=True,
        )
