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


# — ADR-046 intelligence pack: zero-cost flags from already-fetched data —

def test_ticker_impersonation_is_danger():
    # claims "USDC" but the mint is NOT the canonical USDC mint → impersonation
    mk = TokenMarket(mint="m", name="USD Coin", symbol="USDC", liquidity_usd=80_000)
    r = build_report(_analysis(_clean_filt(), market=mk), CFG)
    assert r["level"] in ("DANGER", "CRITICAL")
    assert any(c["id"] == "imp" and c["status"] == "fail" for c in r["checks"])


def test_canonical_major_passes_impersonation():
    # the test mint IS canonical WSOL — claiming "SOL" is legit
    mk = TokenMarket(mint="m", name="Wrapped SOL", symbol="SOL", liquidity_usd=80_000)
    r = build_report(_analysis(_clean_filt(), market=mk), CFG)
    assert any(c["id"] == "imp" and c["status"] == "pass" for c in r["checks"])


def test_fdv_mcap_inflated_soft():
    mk = TokenMarket(mint="m", name="X", symbol="XX", liquidity_usd=80_000,
                     fdv=10_000_000, market_cap=1_000_000)  # 10× → inflated
    clean = build_report(_analysis(_clean_filt()), CFG)["score"]
    r = build_report(_analysis(_clean_filt(), market=mk), CFG)
    assert any(c["id"] == "fdv" and c["status"] == "warn" for c in r["checks"])
    assert r["score"] < clean


def test_young_premined_warns():
    from datetime import datetime, timedelta, timezone
    mi = MintInfo(mint="m", mint_authority=None, freeze_authority=None, top1_pct=45.0)
    mk = TokenMarket(mint="m", name="X", symbol="XX", liquidity_usd=80_000,
                     pair_created_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    r = build_report(_analysis(_clean_filt(), market=mk, mi=mi), CFG)
    assert any(c["id"] == "premine" and c["status"] == "warn" for c in r["checks"])


def test_insider_funding_match_is_critical():
    osint = {"flags": ["insider_funding_match"],
             "holders_intel": {"profiled": 8, "fresh": 5, "traders": 1, "rows": [],
                               "funder_match": {"funder": "F1", "buyers": 4}}}
    r = build_report(_analysis(_clean_filt()), CFG, osint=osint)
    assert r["level"] == "CRITICAL"
    assert any(c["id"] == "fund_match" and c["status"] == "fail" for c in r["checks"])


def _young_market(minutes=60, **kw):
    from datetime import datetime, timedelta, timezone
    base = dict(mint="m", name="X", symbol="XX", liquidity_usd=80_000,
                pair_created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes))
    base.update(kw)
    return TokenMarket(**base)


def test_creator_token_factory_graduated():
    # YOUNG token + RugCheck links the creator to 6 tokens (Helius window saw 0) → factory soft flag
    osint = {"flags": [], "deployer": {"wallet": "W", "prior_creations": 0, "rugcheck_tokens": 6}}
    r = build_report(_analysis(_clean_filt(), market=_young_market()), CFG, osint=osint)
    assert any(c["id"] == "dep_factory" and c["status"] == "warn" for c in r["checks"])
    # but when the serial-CRITICAL already fired, the factory flag is skipped (no double count)
    osint2 = {"flags": ["deployer_serial_rugger"],
              "deployer": {"wallet": "W", "prior_creations": 5, "rugcheck_tokens": 6, "serial": True}}
    r2 = build_report(_analysis(_clean_filt(), market=_young_market()), CFG, osint=osint2)
    assert r2["level"] == "CRITICAL"
    assert not any(c["id"] == "dep_factory" and c["status"] == "warn" for c in r2["checks"])


def test_factory_age_gated_majors_exempt():
    """WIF false-CRITICAL regression: an ESTABLISHED token whose dev shows many all-time mints
    (Jupiter devMints — BONK 10, WIF 15) must NOT be flagged; the row stays informational."""
    osint = {"flags": [], "deployer": {"wallet": "W", "prior_creations": 15, "source": "jupiter"}}
    old = _young_market(minutes=60 * 24 * 400)  # ~400 days old
    r = build_report(_analysis(_clean_filt(), market=old), CFG, osint=osint)
    assert r["level"] in ("SAFE", "CAUTION")  # never DANGER/CRITICAL off dev history alone
    row = next(c for c in r["checks"] if c["id"] == "dep_factory")
    assert row["status"] == "info" and "Jupiter all-time count" in row["detail"]
    # same count on a YOUNG token = a real launch-mill tell → soft flag fires
    r2 = build_report(_analysis(_clean_filt(), market=_young_market(minutes=120)), CFG, osint=osint)
    assert any(c["id"] == "dep_factory" and c["status"] == "warn" for c in r2["checks"])


def test_transfer_fee_band_warns():
    filt = _clean_filt()
    filt.metrics["goplus"] = {"trusted": False, "transfer_fee_pct": 5.0, "risks": []}
    r = build_report(_analysis(filt), CFG)
    assert any(c["id"] == "gp_fee" and c["status"] == "warn" for c in r["checks"])
    # above the hard-veto threshold the CRITICAL goplus flag owns it — no warn band
    filt2 = _clean_filt()
    filt2.metrics["goplus"] = {"trusted": False, "transfer_fee_pct": 50.0, "risks": []}
    r2 = build_report(_analysis(filt2), CFG)
    assert not any(c["id"] == "gp_fee" for c in r2["checks"])


def test_insider_network_dominant_is_danger():
    osint = {"flags": ["insider_network_dominant"],
             "insider_networks": [{"id": "n1", "accounts": 12, "pct": 38.0}]}
    r = build_report(_analysis(_clean_filt()), CFG, osint=osint)
    assert r["level"] in ("DANGER", "CRITICAL")
    assert any(c["id"] == "net_dom" and "38.0%" in c["detail"] for c in r["checks"])
