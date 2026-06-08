"""Stage 1 filters are deterministic and gate real money → required tests (ADR-036 calibration)."""

from solscout.core.config import FiltersCfg
from solscout.core.models import MintInfo, TokenMarket
from solscout.filters import rug

CFG = FiltersCfg()


def _market(liq: float) -> TokenMarket:
    return TokenMarket(mint="m", liquidity_usd=liq)


def _clean_mint() -> MintInfo:
    return MintInfo(
        mint="m",
        mint_authority=None,
        freeze_authority=None,
        supply=1_000_000_000,
        decimals=6,
        top10_pct=20.0,
        top1_pct=5.0,
    )


def test_clean_token_passes():
    res = rug.run("m", _market(50_000), _clean_mint(), CFG)
    assert res.passed
    assert res.hard_flags == []
    assert res.safety_score > 0.5


def test_freeze_authority_is_rejected():
    mi = _clean_mint()
    mi.freeze_authority = "Freeze1111111111111111111111111111111111111"
    res = rug.run("m", _market(50_000), mi, CFG)
    assert not res.passed
    assert "freeze_authority_active" in res.hard_flags
    assert res.safety_score == 0.0


def test_mint_authority_active_is_rejected():
    mi = _clean_mint()
    mi.mint_authority = "Mint11111111111111111111111111111111111111"
    res = rug.run("m", _market(50_000), mi, CFG)
    assert "mint_authority_active" in res.hard_flags


def test_low_liquidity_is_rejected():
    res = rug.run("m", _market(100), _clean_mint(), CFG)
    assert "low_liquidity" in res.hard_flags


def test_no_market_is_no_liquidity_data():
    res = rug.run("m", None, _clean_mint(), CFG)
    assert "no_liquidity_data" in res.hard_flags


# --- concentration: NOT a blanket veto anymore; only EXTREME, trustworthy concentration (ADR-036) ---


def test_moderate_concentration_passes_but_lowers_safety():
    # top-10 = 50% (above the 35% knee, below the 90% extreme) → NO veto, just a lower safety score
    mi = _clean_mint()
    mi.top10_pct, mi.top1_pct = 50.0, 12.0
    res = rug.run("m", _market(50_000), mi, CFG)
    assert res.passed
    assert "holder_concentration_extreme" not in res.hard_flags
    # a cleaner token (top-10 = 20%) should score higher on safety than this 50% one
    cleaner = rug.run("m", _market(50_000), _clean_mint(), CFG)
    assert cleaner.safety_score > res.safety_score


def test_extreme_top10_concentration_is_rejected():
    mi = _clean_mint()
    mi.top10_pct = 95.0  # top-10 holds ~everything → genuine dump risk
    res = rug.run("m", _market(50_000), mi, CFG)
    assert "holder_concentration_extreme" in res.hard_flags
    assert not res.passed


def test_single_wallet_dominant_is_rejected():
    mi = _clean_mint()
    mi.top1_pct = 60.0  # one non-infra wallet can dump the whole market
    res = rug.run("m", _market(50_000), mi, CFG)
    assert "single_wallet_dominant" in res.hard_flags


def test_untrusted_concentration_is_neutral():
    # top1/top10 None (partial holder set, not trustworthy) → no concentration flag at all
    mi = MintInfo(mint="m", top10_pct=None, top1_pct=None)
    res = rug.run("m", _market(50_000), mi, CFG)
    assert "holder_concentration_extreme" not in res.hard_flags
    assert "single_wallet_dominant" not in res.hard_flags
    assert res.passed


def test_invalid_mint_rejected():
    res = rug.run("m", _market(50_000), None, CFG)
    assert not res.passed
    assert "not_a_valid_spl_mint" in res.hard_flags
