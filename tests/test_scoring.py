"""Stage 5 decision engine is deterministic and gates real money → required tests (ADR-036 two tiers)."""

from solscout.core.config import Config
from solscout.core.models import FilterResult, LlmSynthesis, SmartMoneyReport, SocialReport, Verdict
from solscout.scoring.engine import decide

CFG = Config()


def _passed(safety: float = 0.9) -> FilterResult:
    return FilterResult(mint="m", passed=True, safety_score=safety)


def _opp_filt(mcap, liq=50_000, vol=30_000, safety=0.8) -> FilterResult:
    return FilterResult(
        mint="m",
        passed=True,
        safety_score=safety,
        metrics={"market_cap": mcap, "liquidity_usd": liq, "volume_24h": vol},
    )


def _smart(n=1, score=0.9) -> SmartMoneyReport:
    return SmartMoneyReport(
        mint="m",
        smart_wallets_in=[f"W{i}" for i in range(n)],
        best_wallet_winrate=0.78,
        score=score,
    )


# --- vetoes (unchanged: any breach => REJECT) ---


def test_failed_filters_veto_to_reject():
    filt = FilterResult(mint="m", passed=False, hard_flags=["freeze_authority_active"])
    d = decide("m", filt, None, None, None, CFG)
    assert d.verdict == Verdict.REJECT
    assert "freeze_authority_active" in d.veto_flags
    assert d.composite_score == 0.0


def test_handle_reuse_vetoes():
    social = SocialReport(mint="m", handle_reuse_count=3, social_score=0.9)
    d = decide("m", _passed(), social, None, None, CFG)
    assert d.verdict == Verdict.REJECT
    assert "twitter_handle_reuse" in d.veto_flags


def test_deployer_rugger_vetoes():
    smart = SmartMoneyReport(mint="m", deployer_rugged_before=True, score=0.9)
    d = decide("m", _passed(), None, smart, None, CFG)
    assert d.verdict == Verdict.REJECT
    assert "deployer_rugged_before" in d.veto_flags


# --- two-tier BUY: the deadlock is gone — quality alone can buy (smaller), smart confirms (full) ---


def test_quality_tier_buys_without_smart_money():
    # safe + in the opportunity zone + mature, NO smart money → BUY at the QUALITY tier, reduced size
    d = decide("m", _opp_filt(1_000_000, safety=0.8), None, None, None, CFG, token_age_min=100)
    assert d.verdict == Verdict.BUY
    assert d.tier == "quality"
    assert d.gate_met is False
    # size = per_trade_cap * score/100 * quality_size_mult
    expected = round(
        CFG.execution.per_trade_cap_sol * (d.composite_score / 100) * CFG.scoring.quality_size_mult,
        4,
    )
    assert d.position_size_sol == expected


def test_smart_tier_buys_full_size():
    d = decide("m", _opp_filt(1_000_000, safety=0.8), None, _smart(), None, CFG, token_age_min=100)
    assert d.verdict == Verdict.BUY
    assert d.tier == "smart"
    assert d.gate_met is True
    expected = round(CFG.execution.per_trade_cap_sol * (d.composite_score / 100) * 1.0, 4)
    assert d.position_size_sol == expected


def test_quality_needs_higher_bar_than_smart():
    # mid score (~65): clears the smart bar (62) but NOT the quality bar (68).
    mid = _opp_filt(1_000_000, safety=0.65)
    assert (
        decide("m", mid, None, None, None, CFG, token_age_min=100).verdict == Verdict.WATCH
    )  # quality-only
    # same coin with a proven wallet in → smart tier → BUY
    assert decide("m", mid, None, _smart(), None, CFG, token_age_min=100).verdict == Verdict.BUY


def test_too_young_blocks_buy():
    d = decide("m", _opp_filt(1_000_000, safety=0.9), None, _smart(2), None, CFG, token_age_min=3.0)
    assert d.verdict == Verdict.WATCH
    assert any("maturity" in r for r in d.reasons)


def test_mature_enough_allows_buy():
    d = decide(
        "m", _opp_filt(1_000_000, safety=0.9), None, _smart(2), None, CFG, token_age_min=30.0
    )
    assert d.verdict == Verdict.BUY


# --- opportunity zone: out-of-zone DOWNGRADES to WATCH (not a silent REJECT), unless smart money is in ---


def test_megacap_downgraded_to_watch_without_smart_money():
    d = decide("m", _opp_filt(2_000_000_000, safety=0.8), None, None, None, CFG, token_age_min=100)
    assert d.verdict == Verdict.WATCH  # logged, not hidden
    assert any("opportunity" in r.lower() for r in d.reasons)


def test_dead_coin_no_momentum_downgraded_to_watch():
    d = decide(
        "m", _opp_filt(1_000_000, liq=100_000, vol=1_000), None, None, None, CFG, token_age_min=100
    )
    assert d.verdict == Verdict.WATCH


def test_smartmoney_overrides_opportunity_filter():
    # mega-cap but a PROVEN wallet is in → follow the smart money even out of zone → BUY (smart)
    d = decide(
        "m", _opp_filt(2_000_000_000, safety=0.9), None, _smart(), None, CFG, token_age_min=100
    )
    assert d.verdict == Verdict.BUY and d.tier == "smart"


# --- LLM rug guard (capped score keeps obvious rugs out of BUY, no separate gate needed) ---


def test_llm_scam_flag_caps_score_to_reject():
    llm = LlmSynthesis(
        narrative_strength=0.9,
        community_authenticity=0.9,
        scam_language_flags=["presale dm me", "guaranteed 100x", "anon team"],
    )
    d = decide("m", _opp_filt(1_000_000), None, _smart(), llm, CFG, token_age_min=100)
    assert d.composite_score <= 30
    assert d.verdict == Verdict.REJECT
    assert "llm_rug" in d.veto_flags


def test_single_skeptical_flag_does_not_cap():
    llm = LlmSynthesis(
        narrative_strength=0.7, community_authenticity=0.6, scam_language_flags=["unverified"]
    )
    d = decide("m", _opp_filt(1_000_000), None, None, llm, CFG, token_age_min=100)
    assert d.composite_score > 30


def test_llm_fake_community_caps_score_only_with_real_social_data():
    # low authenticity is a rug signal ONLY when we actually fetched community data (ADR-036)
    llm = LlmSynthesis(narrative_strength=0.8, community_authenticity=0.15)
    fetched = SocialReport(mint="m", tg_unique_speakers=20, chatter_texts=["gm", "lfg"])
    capped = decide("m", _opp_filt(1_000_000), fetched, None, llm, CFG, token_age_min=100)
    assert capped.composite_score <= 30 and "llm_rug" in capped.veto_flags
    # with NO social data fetched (the free-tier reality), the same low authenticity is NOT a rug
    not_capped = decide("m", _opp_filt(1_000_000), None, None, llm, CFG, token_age_min=100)
    assert "llm_rug" not in not_capped.veto_flags
    assert not_capped.composite_score > 30


def test_weights_renormalize_over_available_signals():
    d = decide("m", _passed(0.8), None, None, None, CFG)
    # only "safety" present => its renormalized weight must be 1.0
    assert abs(d.breakdown.weights_used["safety"] - 1.0) < 1e-9
    assert d.composite_score == 80.0


def test_non_buy_has_no_tier():
    d = decide("m", _opp_filt(1_000_000, safety=0.2), None, None, None, CFG, token_age_min=100)
    assert d.verdict in (Verdict.WATCH, Verdict.REJECT)
    assert d.tier == ""
