"""Stage 1 — hard on-chain rug filters. Deterministic and pure (no I/O) => unit-tested.

Any hard flag => the funnel stops and the token is REJECTED. `safety_score` (0..1) feeds Stage 5 only
when the token passes.

Active checks: mint/freeze authorities, DEX-liquidity floor, and EXTREME holder concentration only.
Concentration is NOT a blanket veto anymore (ADR-036): new memes are concentrated by nature, so a 35%
top-10 veto rejected every opportunity. We hard-veto only genuinely dangerous, TRUSTWORTHY concentration
(top-10 ~everything, or one non-infra wallet that could dump the whole market); milder concentration just
drags the graded safety score down. Honeypot sell-sim (sell_ok) lands via the Jupiter client (ADR-012).
"""

from __future__ import annotations

from ..core.config import FiltersCfg
from ..core.models import FilterResult, MintInfo, TokenMarket


def run(
    mint: str, market: TokenMarket | None, mint_info: MintInfo | None, cfg: FiltersCfg
) -> FilterResult:
    flags: list[str] = []
    metrics: dict = {}

    if mint_info is None:
        return FilterResult(mint=mint, passed=False, hard_flags=["not_a_valid_spl_mint"])

    metrics["mint_authority"] = mint_info.mint_authority
    metrics["freeze_authority"] = mint_info.freeze_authority
    metrics["top10_pct"] = mint_info.top10_pct
    metrics["top1_pct"] = mint_info.top1_pct
    metrics["liquidity_usd"] = market.liquidity_usd if market else None
    metrics["market_cap"] = market.market_cap if market else None
    metrics["volume_24h"] = market.volume_24h if market else None

    # --- authorities ---
    if cfg.require_freeze_null and mint_info.freeze_authority:
        flags.append("freeze_authority_active")  # classic honeypot: dev can freeze your tokens
    if cfg.require_mint_renounced and mint_info.mint_authority:
        flags.append("mint_authority_active")  # dev can mint infinite supply

    # --- liquidity floor (DEX) ---
    liq = market.liquidity_usd if market else None
    if liq is None:
        flags.append("no_liquidity_data")
    elif liq < cfg.min_liquidity_usd:
        flags.append("low_liquidity")

    # --- EXTREME holder concentration only (top10/top1 are None when the set isn't trustworthy → no flag) ---
    if mint_info.top10_pct is not None and mint_info.top10_pct > cfg.extreme_top10_pct:
        flags.append("holder_concentration_extreme")
    if mint_info.top1_pct is not None and mint_info.top1_pct > cfg.single_wallet_max_pct:
        flags.append("single_wallet_dominant")

    passed = not flags
    return FilterResult(
        mint=mint,
        passed=passed,
        hard_flags=flags,
        metrics=metrics,
        safety_score=_safety_score(market, mint_info, cfg) if passed else 0.0,
    )


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _safety_score(market: TokenMarket | None, mi: MintInfo, cfg: FiltersCfg) -> float:
    """Discriminating 0..1 safety score. For DEX-listed tokens it spreads across authorities, liquidity
    depth, holder concentration, trading VOLUME (dead vs alive) and pair AGE/maturity (older = a rug had
    time to surface), so scores vary instead of collapsing to a single value."""
    authorities_clean = 1.0 if (not mi.mint_authority and not mi.freeze_authority) else 0.0

    if market and market.liquidity_usd:
        liquidity_score = _clamp(market.liquidity_usd / (cfg.min_liquidity_usd * 10))
    else:
        liquidity_score = 0.0

    # concentration: None (untrusted set) → neutral 0.5; else fades from 1.0 (spread) toward 0 (concentrated)
    concentration_score = 0.5 if mi.top10_pct is None else _clamp(1 - mi.top10_pct / 100)

    has_market = bool(market and market.liquidity_usd)
    if has_market:
        vol = market.volume_24h or 0
        volume_score = _clamp(vol / (cfg.min_volume_24h_usd * 10))  # dead (0) → very active (1)
        age_score = _clamp(
            _age_minutes(market) / (cfg.min_pair_age_minutes * 4)
        )  # matures over ~4x the gate
        return round(
            0.25 * authorities_clean
            + 0.25 * liquidity_score
            + 0.20 * concentration_score
            + 0.15 * volume_score
            + 0.15 * age_score,
            4,
        )
    # no-market path: authorities + (neutral) concentration only
    return round(0.6 * authorities_clean + 0.4 * concentration_score, 4)


def _age_minutes(market: TokenMarket | None) -> float:
    if not market or not market.pair_created_at:
        return 0.0
    from datetime import datetime, timezone

    return max(0.0, (datetime.now(timezone.utc) - market.pair_created_at).total_seconds() / 60)
