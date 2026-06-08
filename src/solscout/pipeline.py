"""The funnel as one reusable call: a mint → Analysis. Shared by `report` and `scan`.

Network orchestration only — the decision logic lives in filters/ and scoring/ (pure, unit-tested).
Clients are passed in (opened/closed by the caller) so the same open connections serve a whole scan.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, TypeVar

from .core.config import Config
from .core.logging import get_logger
from .core.models import (
    Decision,
    FilterResult,
    LlmSynthesis,
    MintInfo,
    SmartMoneyReport,
    SocialReport,
    TokenMarket,
    Verdict,
)
from .data.goplus import flags as goplus_flags
from .data.rugcheck import flags as rugcheck_flags
from .enrich import cluster, smartmoney, social
from .enrich.discovery import is_infra_wallet
from .filters import manipulation, rug
from .llm.synthesize import analyze_token
from .scoring.engine import decide

# a wallet's SOL funder doesn't change → cache it across tokens so recurring sybils are free to re-check
_funder_cache: dict[str, str | None] = {}

log = get_logger("solscout.pipeline")
T = TypeVar("T")


@dataclass
class Analysis:
    mint: str
    market: TokenMarket | None
    mint_info: MintInfo | None
    filt: FilterResult
    smart: SmartMoneyReport | None
    social: SocialReport | None
    llm: LlmSynthesis | None
    decision: Decision
    ready: bool = (
        True  # False = not yet judgeable (not indexed on DEX yet / too young) → re-eval later
    )
    holders: list[str] = field(
        default_factory=list
    )  # top holder wallets (already fetched; reused for discovery)
    top_holders: list[dict] = field(
        default_factory=list
    )  # [{owner, pct}] non-infra top holders (for the web report's distribution viz)
    holder_count: int | None = None  # trusted non-infra holder count (None if set not fully resolved)


async def _safe(coro: Awaitable[T], label: str) -> T | None:
    try:
        return await coro
    except Exception as e:
        log.warning("%s failed: %s", label, e)
        return None


async def _merge_gecko_flow(market, mint: str, gecko, launch_meta: dict | None) -> None:
    """Attach UNIQUE buyers/sellers (24h) onto the market for the manipulation checks. Prefer flow the
    candidate stream already stashed (no extra call); else one free GeckoTerminal lookup."""
    lm = launch_meta or {}
    if lm.get("buyers_h24") is not None or lm.get("sellers_h24") is not None:
        market.buyers_h24 = lm.get("buyers_h24")
        market.sellers_h24 = lm.get("sellers_h24")
        if market.price_change_h24 is None:
            market.price_change_h24 = lm.get("price_change_24h")
        return
    if gecko is None:
        return
    pool = await _safe(gecko.token_pool(mint), "gecko-pool")
    if pool:
        market.buyers_h24 = pool.buyers_h24
        market.sellers_h24 = pool.sellers_h24
        if market.txns_buys_h24 is None:
            market.txns_buys_h24 = pool.buys_h24
        if market.txns_sells_h24 is None:
            market.txns_sells_h24 = pool.sells_h24
        if market.price_change_h24 is None:
            market.price_change_h24 = pool.price_change_24h


async def cluster_gate(holders_full: list[tuple[str, int]], ccfg, helius) -> tuple[list[str], int, str | None]:
    """Trace the top holders' SOL funders (Helius, cached) and detect a sybil bundle. Best-effort: if a
    holder's history can't be fetched (over credit budget), it just doesn't count toward a cluster."""
    funder_by: dict[str, str | None] = {}
    for owner, _amt in holders_full[: ccfg.top_n]:
        if owner in _funder_cache:
            funder_by[owner] = _funder_cache[owner]
            continue
        txs = await _safe(helius.address_transactions(owner, limit=ccfg.tx_limit), "cluster-txns") or []
        f = cluster.dominant_funder(txs, owner) if txs else None
        _funder_cache[owner] = f
        funder_by[owner] = f
    return cluster.detect_bundle(funder_by, ccfg)


async def analyze(
    mint: str,
    cfg: Config,
    *,
    dex,
    rpc,
    helius,
    tg,
    ts,
    watchlist,
    creator: str | None = None,
    launch_meta: dict | None = None,
    jup=None,
    gecko=None,
    rc=None,
    gp=None,
) -> Analysis:
    # CREDIT DIET (ADR-020): fetch the cheap signals first (DexScreener=free, mint_info=cheap public RPC).
    # If the token isn't indexed yet (PENDING), return WITHOUT the expensive DAS holders call / social /
    # LLM — most fresh launches are PENDING on first pass, so this avoids the biggest credit sink.
    market, mint_info = await asyncio.gather(
        _safe(dex.get_token(mint), "dexscreener"),
        _safe(rpc.get_mint_info(mint), "solana-rpc"),
    )
    if mint_info is None:
        filt = rug.run(mint, market, None, cfg.filters)
        return Analysis(
            mint,
            market,
            None,
            filt,
            None,
            None,
            None,
            decide(mint, filt, social=None, smart=None, llm=None, cfg=cfg),
            ready=False,
        )

    holders_res = await _safe(helius.token_holders(mint), "helius-holders")
    social_report = await _safe(social.assess(market, cfg, tg_client=tg, ts_client=ts), "social")

    # Holder analysis: drop infra/LP/exchange owners + the pool's own vault so they aren't read as whales.
    # Concentration (top-1 / top-10) is trusted ONLY when we got the COMPLETE holder set (small/fresh
    # tokens) — for partial pages we leave it None (neutral), never a false flag.
    owners: list[str] = []
    float_pct: float | None = None  # circulating (non-pool) supply % — for the low-float manipulation check
    holder_count: int | None = None  # trusted non-infra holder count — for the real-activity gate (ADR-038)
    holders_full: list[tuple[str, int]] = []  # full non-infra holder list (for cluster/bundle detection)
    top_holders_pct: list[dict] = []  # [{owner, pct}] top non-infra holders (for the web report)
    if holders_res:
        holders, complete = holders_res
        pair_excl = {market.pair_address} if market and market.pair_address else set()
        holders = [(o, a) for o, a in holders if not is_infra_wallet(o, pair_excl)]
        holders_full = holders
        owners = [o for o, _ in holders[:20]]
        total = sum(a for _, a in holders)
        if complete and total > 0:
            mint_info.top1_pct = round(100 * holders[0][1] / total, 2)
            mint_info.top10_pct = round(100 * sum(a for _, a in holders[:10]) / total, 2)
            mint_info.holders_sampled = len(holders)
            holder_count = len(holders)
            top_holders_pct = [
                {"owner": o, "pct": round(100 * a / total, 2)} for o, a in holders[:15]
            ]
            if mint_info.supply > 0:  # non-pool supply / total supply = circulating float
                float_pct = round(100 * total / mint_info.supply, 2)

    age_min = None
    if market and market.pair_created_at:
        age_min = max(0.0, (datetime.now(timezone.utc) - market.pair_created_at).total_seconds() / 60)

    filt = rug.run(mint, market, mint_info, cfg.filters)

    # ANTI-MANIPULATION (ADR-037): merge UNIQUE buyers/sellers (GeckoTerminal) then flag wash-trading /
    # one-way (honeypot) flow / hyper-pumps. Hard flags veto; soft flags dock the safety score.
    if market and cfg.manipulation.enabled:
        await _merge_gecko_flow(market, mint, gecko, launch_meta)
        m_hard, m_soft = manipulation.assess(
            market, age_min, float_pct, cfg.manipulation, holder_count=holder_count
        )
        if m_hard:
            filt.hard_flags = list(filt.hard_flags) + m_hard
            filt.passed = False
            filt.safety_score = 0.0
        elif m_soft and filt.passed:
            filt.safety_score = round(filt.safety_score * cfg.manipulation.soft_penalty, 4)
            filt.metrics["manip_soft"] = m_soft

    # optional: reject ALL pump.fun-origin coins (the casino) — off by default (ADR-038)
    if cfg.filters.exclude_pump_origin and filt.passed and (
        mint.endswith("pump") or (market and (market.dex or "").lower() == "pumpswap")
    ):
        filt.hard_flags = list(filt.hard_flags) + ["pump_origin"]
        filt.passed = False
        filt.safety_score = 0.0

    # RUGCHECK (ADR-039) — independent aggregated risk read (free, no key). Hard-veto rugged/high-risk/
    # insiders; soft-penalty elevated. If RugCheck gives us its insider graph, we TRUST it and skip our own
    # credit-spending Helius funder-trace below (saves Helius credits).
    rc_insiders_known = False
    if rc is not None and cfg.rugcheck.enabled and market:
        rc_report = await _safe(rc.report(mint), "rugcheck")
        if rc_report and rc_report.available:
            filt.metrics["rugcheck_score"] = rc_report.score
            rc_hard, rc_soft = rugcheck_flags(rc_report, cfg.rugcheck)
            if rc_hard:
                filt.hard_flags = list(filt.hard_flags) + rc_hard
                filt.passed = False
                filt.safety_score = 0.0
            elif rc_soft and filt.passed:
                filt.safety_score = round(filt.safety_score * cfg.manipulation.soft_penalty, 4)
                filt.metrics["rugcheck_soft"] = rc_soft
            rc_insiders_known = cfg.rugcheck.trust_insiders

    # GOPLUS (ADR-041) — a SECOND independent security source. Catches Token-2022 honeypot vectors
    # (transfer_hook / non_transferable) + GoPlus's known-malicious-creator flag that RugCheck may miss.
    if gp is not None and cfg.goplus.enabled and market:
        gp_report = await _safe(gp.token_security(mint), "goplus")
        if gp_report and gp_report.available:
            filt.metrics["goplus"] = {
                "trusted": gp_report.trusted,
                "transfer_hook": gp_report.transfer_hook,
                "non_transferable": gp_report.non_transferable,
                "malicious_creator": gp_report.malicious_creator,
                "transfer_fee_pct": gp_report.transfer_fee_pct,
                "lp_holders": gp_report.lp_holders,
                "risks": gp_report.risks,
            }
            gp_hard, gp_soft = goplus_flags(gp_report, cfg.goplus)
            if gp_hard:
                filt.hard_flags = list(filt.hard_flags) + gp_hard
                filt.passed = False
                filt.safety_score = 0.0
            elif gp_soft and filt.passed:
                filt.safety_score = round(filt.safety_score * cfg.manipulation.soft_penalty, 4)
                filt.metrics["goplus_soft"] = gp_soft

    # honeypot guard (ADR-012) — Jupiter round-trip sell-sim (free). Off by default; only when configured.
    if jup is not None and cfg.filters.require_sell_simulation and filt.passed:
        sell_ok, tax = await _safe(
            jup.sell_simulation(mint, slippage_bps=cfg.execution.max_slippage_bps),
            "jupiter-sellsim",
        ) or (None, None)
        filt.sell_simulated = True
        filt.sell_ok = sell_ok
        filt.round_trip_tax_pct = tax
        if tax is not None and tax > cfg.filters.max_buy_sell_tax_pct:
            filt.sell_ok = False

    smart = smartmoney.score_watchlist_overlap(mint, owners, watchlist, cfg) if owners else None

    # deployer reputation (negative signal) — gated behind a config flag because it costs one expensive
    # Helius enhanced-txns call per launch (credit diet, ADR-020). Off by default on the free tier.
    if creator and cfg.smart_money.check_deployer and getattr(helius, "available", False):
        rugged, prior = await smartmoney.deployer_signal(creator, helius)
        if prior is not None:
            smart = smart or SmartMoneyReport(mint=mint)
            smart.deployer_rugged_before = rugged
            smart.deployer_prior_launches = prior

    # RICH LLM analysis — runs for every coin that PASSED the hard rug filters (don't waste Qwen on rejects).
    # Feeds the whole signal set (on-chain + market + Twitter/Telegram + chatter) → a human commentary.
    llm = None
    if filt.passed and market:
        ctx = {
            "name": market.name,
            "symbol": market.symbol,
            "onchain": {
                "mint_authority": mint_info.mint_authority,
                "freeze_authority": mint_info.freeze_authority,
                "top10_pct": mint_info.top10_pct,
            },
            "market": {
                "liquidity_usd": market.liquidity_usd,
                "volume_24h": market.volume_24h,
                "market_cap": market.market_cap,
                "age_min": round(age_min) if age_min else None,
            },
            "socials": [f"{s.type}:{s.url}" for s in market.socials],
            "twitter": {
                "handle": social_report.twitter_handle,
                "age_days": social_report.twitter_age_days,
                "followers": social_report.notable_followers,
            }
            if social_report
            else {},
            "chatter": social_report.chatter_texts if social_report else [],
        }
        llm = await analyze_token(ctx, cfg.llm)

    decision = decide(
        mint, filt, social=social_report, smart=smart, llm=llm, cfg=cfg, token_age_min=age_min
    )
    if llm:
        decision.llm_summary = llm.summary

    # PRE-BUY BUNDLE GATE (ADR-038) — only for BUY candidates (few → credit-cheap): if the top holders are a
    # sybil cluster funded by one wallet, it's coordinated/insider distribution holder-concentration can't
    # see (the Bubblemaps signal). Reject. Best-effort: if Helius is over budget, the buy proceeds.
    if (
        cfg.cluster.enabled
        and decision.verdict == Verdict.BUY
        and holders_full
        and getattr(helius, "available", False)
        and not rc_insiders_known  # RugCheck already vetted insiders (free) → skip our Helius trace
    ):
        flags, size, funder = await cluster_gate(holders_full, cfg.cluster, helius)
        if flags:
            decision.verdict = Verdict.REJECT
            decision.veto_flags = list(decision.veto_flags) + flags
            decision.reasons.append(
                f"Bundled holders: {size} of the top {cfg.cluster.top_n} share one funder "
                f"({(funder or '?')[:8]}…) → coordinated/sybil distribution, not real holders."
            )
            decision.composite_score = 0.0
            decision.tier = ""
            decision.position_size_sol = 0.0

    # "ready to judge?" — a token needs a real DexScreener market (liquidity) AND maturity. A brand-new
    # listing isn't indexed yet (market None) → NOT ready → re-eval later (DexScreener lags minutes). This
    # stops a false "no_liquidity_data" REJECT on tokens that simply aren't indexed yet.
    mature = age_min is not None and age_min >= cfg.filters.min_pair_age_minutes
    ready = bool(market and market.liquidity_usd) and mature
    return Analysis(
        mint,
        market,
        mint_info,
        filt,
        smart,
        social_report,
        llm,
        decision,
        ready=ready,
        holders=owners[:10],
        top_holders=top_holders_pct,
        holder_count=holder_count,
    )
