"""Stage 1b — anti-rug / anti-manipulation entry filters (ADR-037). Deterministic + pure => unit-tested.

Catches the wash-traded / honeypot-shaped / hyper-pumped patterns the user flagged (SPCX, BARRON, SV151)
BEFORE we buy. All signals are FREE: DexScreener txns + GeckoTerminal UNIQUE buyers/sellers (merged onto the
TokenMarket). Returns (hard, soft):
  - hard  → a veto (REJECT) — the unmistakable manipulation shapes
  - soft  → a safety-score penalty — suspicious but not damning on its own
Signals degrade gracefully: a check is simply skipped when its data wasn't fetched (None).
"""

from __future__ import annotations

from ..core.config import ManipulationCfg
from ..core.models import TokenMarket


def assess(
    market: TokenMarket,
    age_min: float | None,
    float_pct: float | None,
    cfg: ManipulationCfg,
    holder_count: int | None = None,
) -> tuple[list[str], list[str]]:
    """(hard_flags, soft_flags). Pure. `float_pct` = circulating (non-pool) supply % (None if unknown).
    `holder_count` = trusted non-infra holder count (None if not fully resolved)."""
    hard: list[str] = []
    soft: list[str] = []
    if not cfg.enabled:
        return hard, soft

    liq = market.liquidity_usd or 0.0
    vol = market.volume_24h or 0.0
    buyers, sellers = market.buyers_h24, market.sellers_h24

    # 0) REAL-ACTIVITY floor (ADR-038) — reject husks/honeypots BEFORE the ratio checks. A token must be a
    #    genuinely, two-sidedly traded, distributed coin. Catches WIF (37 holders, 1 txn), BARRON (3 buyers/
    #    0 sellers), IRAN (1/0) — all of which sailed through because they were "too thin to judge."
    if holder_count is not None and holder_count < cfg.min_holders:
        hard.append("too_few_holders")
    if buyers is not None and sellers is not None:
        if (buyers + sellers) < cfg.min_traders:
            hard.append("too_few_traders")
        elif sellers == 0 and buyers >= cfg.no_seller_min_buyers:
            hard.append("no_sellers")  # plenty of buyers, ZERO sellers → can't/won't sell = honeypot

    # 1) LOPSIDED FLOW — almost everyone buying, ~nobody selling = honeypot / one-way pump.
    #    (BARRON: 33 unique sellers vs 1,696 buyers ≈ 2% seller share.)
    if buyers is not None and sellers is not None:
        traders = buyers + sellers
        if traders >= cfg.min_unique_traders:
            seller_share = sellers / traders if traders else 0.0
            if seller_share < cfg.min_seller_share:
                hard.append("lopsided_flow")

    # 2) WASH VOLUME — absurd turnover vs the pool size = fake churn. Hard veto when the pool is also thin
    #    (where wash actually moves the price); otherwise just a soft note.
    if liq > 0 and (vol / liq) > cfg.max_vol_liq_ratio:
        hard.append("wash_volume") if liq < cfg.wash_liquidity_usd else soft.append("high_turnover")

    # 3) BUY/SELL TXN IMBALANCE (soft) — lots more buy txns than sell txns.
    b, s = market.txns_buys_h24, market.txns_sells_h24
    if b and s and min(b, s) > 0 and max(b, s) / min(b, s) > cfg.max_txn_imbalance:
        soft.append("txn_imbalance")

    # 4) HYPER-PUMP — brand-new + vertical price move on a thin pool = manipulation (SPCX/AMERICA shape).
    if (
        age_min is not None
        and age_min <= cfg.young_minutes
        and (market.price_change_h24 or 0) >= cfg.max_young_pump_pct
        and liq < cfg.young_pump_liquidity_usd
    ):
        hard.append("hyper_pump")

    # 5) LOW FLOAT (soft) — too little supply circulates outside the pool → trivially pumpable / rug-prone.
    if float_pct is not None and float_pct < cfg.min_float_pct:
        soft.append("low_float")

    return hard, soft
