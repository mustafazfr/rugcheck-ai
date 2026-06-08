"""SafetyReport builder (ADR-040) is pure → required tests."""

from types import SimpleNamespace

from solscout.core.config import Config
from solscout.core.models import (
    Decision,
    FilterResult,
    LlmSynthesis,
    MintInfo,
    TokenMarket,
    Verdict,
)
from solscout.web.report import build_report

CFG = Config()


def _analysis(filt, *, market=None, mi=None, decision=None, smart=None, social=None, llm=None,
              holder_count=None, top_holders=None):
    return SimpleNamespace(
        mint="So11111111111111111111111111111111111111112",
        filt=filt,
        market=market,
        mint_info=mi,
        decision=decision or Decision(mint="m", verdict=Verdict.WATCH),
        smart=smart,
        social=social,
        llm=llm,
        ready=True,
        holder_count=holder_count,
        top_holders=top_holders or [],
    )


def _clean_filt(safety=0.85):
    return FilterResult(
        mint="m", passed=True, safety_score=safety,
        metrics={"liquidity_usd": 80_000, "top10_pct": 30.0, "top1_pct": 8.0, "market_cap": 500_000},
    )


def test_clean_token_is_safe():
    mi = MintInfo(mint="m", mint_authority=None, freeze_authority=None, top10_pct=30.0, top1_pct=8.0)
    mk = TokenMarket(mint="m", name="Good", symbol="GOOD", liquidity_usd=80_000, buyers_h24=300, sellers_h24=250)
    r = build_report(_analysis(_clean_filt(), market=mk, mi=mi, holder_count=400,
                               top_holders=[{"owner": "W", "pct": 8.0}]), CFG)
    assert r["level"] == "SAFE" and r["score"] >= 72
    assert r["counts"]["fail"] == 0
    assert any(c["id"] == "freeze_auth" and c["status"] == "pass" for c in r["checks"])
    assert r["token"]["links"]["bubblemaps"].endswith(r["mint"])


def test_freeze_authority_is_critical():
    filt = FilterResult(mint="m", passed=False, hard_flags=["freeze_authority_active"], safety_score=0.0)
    mi = MintInfo(mint="m", freeze_authority="Fxxxx")
    dec = Decision(mint="m", verdict=Verdict.REJECT, veto_flags=["freeze_authority_active"])
    r = build_report(_analysis(filt, mi=mi, decision=dec), CFG)
    assert r["level"] == "CRITICAL" and r["score"] <= 16
    assert any(c["id"] == "freeze_auth" and c["status"] == "fail" for c in r["checks"])


def test_wash_volume_is_danger():
    filt = FilterResult(mint="m", passed=False, hard_flags=["wash_volume"], safety_score=0.0)
    dec = Decision(mint="m", verdict=Verdict.REJECT, veto_flags=["wash_volume"])
    r = build_report(_analysis(filt, decision=dec), CFG)
    assert r["level"] == "DANGER"
    assert any(c["id"] == "wash" and c["status"] == "fail" for c in r["checks"])


def test_rugcheck_score_surfaced():
    filt = _clean_filt()
    filt.metrics["rugcheck_score"] = 12
    r = build_report(_analysis(filt), CFG)
    assert r["rugcheck"]["available"] and r["rugcheck"]["score"] == 12
    assert any(c["id"] == "rc_score" for c in r["checks"])


def test_high_rugcheck_drags_score():
    clean = build_report(_analysis(_clean_filt()), CFG)["score"]
    f2 = _clean_filt()
    f2.metrics["rugcheck_score"] = 80  # risky per RugCheck
    risky = build_report(_analysis(f2), CFG)["score"]
    assert risky < clean


def test_ai_block_and_scam_flag():
    llm = LlmSynthesis(summary="looks fake", narrative_strength=0.2, community_authenticity=0.1,
                       scam_language_flags=["guaranteed 100x"])
    dec = Decision(mint="m", verdict=Verdict.REJECT, veto_flags=["llm_rug"])
    r = build_report(_analysis(_clean_filt(), decision=dec, llm=llm), CFG)
    assert r["ai"]["summary"] == "looks fake"
    assert any(c["id"] == "ai_rug" and c["status"] == "fail" for c in r["checks"])


def test_invalid_mint_check():
    filt = FilterResult(mint="m", passed=False, hard_flags=["not_a_valid_spl_mint"])
    dec = Decision(mint="m", verdict=Verdict.REJECT, veto_flags=["not_a_valid_spl_mint"])
    r = build_report(_analysis(filt, decision=dec), CFG)
    assert r["level"] == "CRITICAL"
    assert any(c["id"] == "mint_valid" for c in r["checks"])
