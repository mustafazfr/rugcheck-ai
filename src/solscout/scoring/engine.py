"""Stage 5 — the DETERMINISTIC decision engine. NOT the LLM. Pure => unit-tested.

Layer A (veto): any breach => REJECT regardless of score.
Layer B (weighted composite 0..100): over {safety, smart_money, social, narrative}, weights from config,
renormalized over the signals actually available (missing signal => its weight is dropped, others scale up).
Layer C (two-tier BUY, ADR-036): smart-money is a BOOST + size multiplier + confidence tier, never a hard
gate. BUY when score >= the tier's bar AND in the opportunity zone AND mature:
  - smart   tier (a proven watchlist wallet is in)  → buy_threshold,        full size
  - quality tier (no smart money, but safe + clean)  → quality_buy_min_score, quality_size_mult × size
Otherwise WATCH (>= watch_threshold) | REJECT. Fail-safe: anything uncertain resolves downward, never to BUY.
"""

from __future__ import annotations

from ..core.config import Config
from ..core.models import (
    Decision,
    FilterResult,
    LlmSynthesis,
    ScoreBreakdown,
    SmartMoneyReport,
    SocialReport,
    Verdict,
)


def decide(
    mint: str,
    filt: FilterResult,
    social: SocialReport | None,
    smart: SmartMoneyReport | None,
    llm: LlmSynthesis | None,
    cfg: Config,
    token_age_min: float | None = None,
) -> Decision:
    veto = _vetoes(filt, social, smart, cfg)
    if veto:
        return Decision(
            mint=mint,
            verdict=Verdict.REJECT,
            composite_score=0.0,
            reasons=["Hard veto — see veto_flags."],
            veto_flags=veto,
        )

    # On the free tier we fetch NO community data (no TweetScout/Telegram key), so "low authenticity" is
    # uninformative — it just means Qwen saw no community. Only use the authenticity signal when we actually
    # have social evidence; otherwise the meme's narrative strength carries the LLM component (ADR-036).
    social_data = _social_data_present(social)
    components = _components(filt, social, smart, llm, social_data)
    breakdown = _composite(components, cfg)
    reasons = _reasons(components, breakdown, social, smart, llm)
    sc = cfg.scoring

    # LLM rug veto-cap: only for OBVIOUS rugs. Concrete scam tells (name/ticker impersonation, '1000x/presale')
    # always count; the low-authenticity path counts ONLY when we actually fetched community data — never
    # punish a coin merely for having no socials we could read. The cap pushes the score below every BUY bar.
    extra_veto: list[str] = []
    authenticity_rug = social_data and llm is not None and llm.community_authenticity < sc.llm_min_authenticity
    scam_rug = llm is not None and len(llm.scam_language_flags) >= 2
    if authenticity_rug or scam_rug:
        extra_veto.append("llm_rug")  # keeps it out of the "clean coins" list
        if breakdown.composite > sc.llm_scam_score_cap:
            breakdown.composite = sc.llm_scam_score_cap
            reasons.append(
                f"LLM flagged likely rug → score capped at {sc.llm_scam_score_cap:.0f} "
                f"({'low authenticity, ' if authenticity_rug else ''}"
                f"{len(llm.scam_language_flags)} scam flags)."
            )

    score = breakdown.composite

    # TWO-TIER BUY (ADR-036). Smart-money is a BOOST + size multiplier + confidence tier, NOT a hard gate.
    #   smart   = a proven watchlist wallet is in  → full size, lower bar (buy_threshold)
    #   quality = no smart money, but safe+liquid+momentum+LLM-clean → HALF size, higher bar (quality_buy_min_score)
    smart_in = bool(smart and len(smart.smart_wallets_in) >= cfg.smart_money.min_wallets_for_gate)
    tier = "smart" if smart_in else "quality"
    buy_bar = sc.buy_threshold if smart_in else sc.quality_buy_min_score

    # OPPORTUNITY zone (money focus): only BUY coins that can still multiply (right size + momentum + not
    # ancient). Out-of-zone tokens downgrade to WATCH (still logged) — never a silent REJECT — UNLESS a
    # proven wallet is in (then we follow the smart money even out of zone).
    opp = _opportunity(filt.metrics, token_age_min, cfg)
    opp_ok = (opp is not False) or smart_in
    # maturity gate (rug protection): too young to trust → can't BUY yet, only WATCH + re-eval later
    too_young = token_age_min is not None and token_age_min < cfg.filters.min_pair_age_minutes

    if score >= buy_bar and opp_ok and not too_young:
        verdict = Verdict.BUY
    elif score >= sc.watch_threshold:
        verdict = Verdict.WATCH
    else:
        verdict = Verdict.REJECT

    if verdict != Verdict.BUY and score >= buy_bar:  # would-buy explanations
        if too_young:
            reasons.append(
                f"Would BUY but pair is only {token_age_min:.0f} min old "
                f"(< {cfg.filters.min_pair_age_minutes} min maturity) → WATCH, re-eval later."
            )
        elif not opp_ok:
            reasons.append(
                "Out of opportunity zone (size/momentum/age) and no smart-money → WATCH not BUY."
            )
    if (
        verdict == Verdict.WATCH
        and not smart_in
        and sc.buy_threshold <= score < sc.quality_buy_min_score
    ):
        reasons.append(
            f"Score {score:.0f} would BUY with smart-money confirmation "
            f"(quality-only needs {sc.quality_buy_min_score:.0f})."
        )

    mult = 1.0 if smart_in else sc.quality_size_mult
    size = (
        round(cfg.execution.per_trade_cap_sol * (score / 100) * mult, 4)
        if verdict == Verdict.BUY
        else 0.0
    )
    return Decision(
        mint=mint,
        verdict=verdict,
        composite_score=round(score, 2),
        breakdown=breakdown,
        reasons=reasons,
        gate_met=smart_in,
        tier=(tier if verdict == Verdict.BUY else ""),
        veto_flags=extra_veto,
        position_size_sol=size,
    )


def _vetoes(filt, social, smart, cfg) -> list[str]:
    v: list[str] = []
    if not filt.passed:
        v.extend(filt.hard_flags or ["filters_failed"])
    if filt.sell_ok is False:  # honeypot (ADR-012)
        v.append("honeypot_cannot_sell")
    if (
        social
        and social.handle_reuse_count is not None
        and social.handle_reuse_count > cfg.social.max_twitter_handle_reuse
    ):
        v.append("twitter_handle_reuse")
    if smart and smart.deployer_rugged_before and cfg.smart_money.reject_if_deployer_rugged_before:
        v.append("deployer_rugged_before")
    return v


def _social_data_present(social) -> bool:
    """True only when we actually FETCHED community evidence (chatter / Twitter intel) — not just a
    DexScreener link. Without a paid social key this is False, so authenticity can't be used as a rug."""
    return bool(
        social
        and (
            social.chatter_texts
            or social.twitter_age_days is not None
            or social.notable_followers is not None
            or social.tg_unique_speakers is not None
        )
    )


def _components(filt, social, smart, llm, social_data: bool = False) -> dict[str, float]:
    c: dict[str, float] = {"safety": filt.safety_score}
    if smart and smart.score is not None:
        c["smart_money"] = smart.score
    if social and social.social_score is not None:
        c["social"] = social.social_score
    if llm is not None:
        # with real community data, blend narrative + authenticity; without it, authenticity is unknown
        # (Qwen returns ~0.5) so let the meme's narrative strength carry the component instead of diluting it.
        narrative = (
            0.6 * llm.narrative_strength + 0.4 * llm.community_authenticity
            if social_data
            else llm.narrative_strength
        )
        if llm.scam_language_flags:
            narrative *= 0.5  # scam-language penalty
        c["narrative"] = max(0.0, min(1.0, narrative))
    return c


def _composite(components: dict[str, float], cfg: Config) -> ScoreBreakdown:
    weights = {k: cfg.scoring.weights.get(k, 0.0) for k in components}
    total = sum(weights.values()) or 1.0
    norm = {k: w / total for k, w in weights.items()}  # renormalize over available signals
    composite = 100 * sum(components[k] * norm[k] for k in components)
    return ScoreBreakdown(components=components, weights_used=norm, composite=composite)


def _opportunity(metrics: dict, age_min: float | None, cfg) -> bool | None:
    """Money filter. True=in the opportunity zone (worth surfacing), False=established/dead (hide unless a
    buy signal), None=not enough market data to judge (don't filter on it)."""
    mcap = metrics.get("market_cap")
    if mcap is None:
        return None  # no DEX data yet → leave it to other gates
    sc = cfg.scoring
    size_ok = sc.opp_min_market_cap_usd <= mcap <= sc.opp_max_market_cap_usd
    liq = metrics.get("liquidity_usd") or 0
    vol = metrics.get("volume_24h") or 0
    momentum_ok = liq > 0 and (vol / liq) >= sc.opp_min_volume_ratio
    age_ok = age_min is None or age_min <= sc.opp_max_age_days * 1440
    return bool(size_ok and momentum_ok and age_ok)


def _reasons(components, breakdown, social, smart, llm) -> list[str]:
    r = [f"{k}={v:.2f} (w={breakdown.weights_used.get(k, 0):.2f})" for k, v in components.items()]
    missing = {"smart_money", "social", "narrative"} - set(components)
    if missing:
        r.append("Missing signals (weight renormalized): " + ", ".join(sorted(missing)))
    if llm and llm.scam_language_flags:
        r.append("Scam-language flags: " + ", ".join(llm.scam_language_flags))
    return r
