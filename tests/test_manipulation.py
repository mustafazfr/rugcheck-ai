"""Anti-rug/anti-manipulation entry filters (ADR-037) gate real money → required tests."""

from solscout.core.config import ManipulationCfg
from solscout.core.models import TokenMarket
from solscout.filters.manipulation import assess

CFG = ManipulationCfg()


def _mk(**kw) -> TokenMarket:
    base = dict(mint="m", liquidity_usd=120_000, volume_24h=200_000, buyers_h24=400, sellers_h24=350)
    base.update(kw)
    return TokenMarket(**base)


def test_clean_token_no_flags():
    hard, soft = assess(_mk(), age_min=300, float_pct=40, cfg=CFG)
    assert hard == [] and soft == []


def test_lopsided_flow_is_hard_veto():
    # 1696 buyers vs 33 sellers (BARRON) → ~2% seller share → honeypot/one-way pump
    hard, _ = assess(_mk(buyers_h24=1696, sellers_h24=33), age_min=300, float_pct=40, cfg=CFG)
    assert "lopsided_flow" in hard


def test_lopsided_ignored_when_too_few_traders():
    # only 10 unique traders → not enough to judge → no flag (avoid false positives on brand-new pools)
    hard, _ = assess(_mk(buyers_h24=9, sellers_h24=1), age_min=300, float_pct=40, cfg=CFG)
    assert "lopsided_flow" not in hard


def test_wash_volume_hard_on_thin_pool():
    # $1M volume on a $30k pool = 33× turnover, thin → wash hard veto
    hard, _ = assess(_mk(liquidity_usd=30_000, volume_24h=1_000_000), age_min=300, float_pct=40, cfg=CFG)
    assert "wash_volume" in hard


def test_high_turnover_soft_on_deep_pool():
    # same turnover but a deep ($200k) pool → soft note, not a veto
    hard, soft = assess(_mk(liquidity_usd=200_000, volume_24h=6_000_000), age_min=300, float_pct=40, cfg=CFG)
    assert "wash_volume" not in hard and "high_turnover" in soft


def test_hyper_pump_hard_veto():
    # 30 min old, +2000% h24, thin pool → vertical manipulation (SPCX/AMERICA shape)
    hard, _ = assess(_mk(liquidity_usd=40_000, price_change_h24=2000), age_min=30, float_pct=40, cfg=CFG)
    assert "hyper_pump" in hard


def test_txn_imbalance_soft():
    _, soft = assess(_mk(txns_buys_h24=5000, txns_sells_h24=200), age_min=300, float_pct=40, cfg=CFG)
    assert "txn_imbalance" in soft


def test_low_float_soft():
    _, soft = assess(_mk(), age_min=300, float_pct=3.0, cfg=CFG)
    assert "low_float" in soft


def test_missing_flow_data_is_safe():
    # buyers/sellers unknown (no gecko data) → no lopsided flag, no crash
    hard, _ = assess(_mk(buyers_h24=None, sellers_h24=None), age_min=300, float_pct=None, cfg=CFG)
    assert "lopsided_flow" not in hard


def test_disabled_returns_nothing():
    cfg = ManipulationCfg(enabled=False)
    hard, soft = assess(_mk(buyers_h24=1000, sellers_h24=1), age_min=10, float_pct=1, cfg=cfg)
    assert hard == [] and soft == []


# --- ADR-038: real-activity floor (catches WIF 37-holder husk, BARRON 3/0, IRAN 1/0) ---


def test_too_few_holders_rejected():
    # WIF: 37 non-infra holders (< 50) → husk, REJECT
    hard, _ = assess(_mk(), age_min=300, float_pct=40, cfg=CFG, holder_count=37)
    assert "too_few_holders" in hard


def test_too_few_traders_rejected():
    # BARRON: 3 buyers / 0 sellers → 3 traders < 40
    hard, _ = assess(_mk(buyers_h24=3, sellers_h24=0), age_min=300, float_pct=40, cfg=CFG, holder_count=300)
    assert "too_few_traders" in hard


def test_no_sellers_is_honeypot():
    # many buyers, ZERO sellers → can't/won't sell
    hard, _ = assess(_mk(buyers_h24=80, sellers_h24=0), age_min=300, float_pct=40, cfg=CFG, holder_count=300)
    assert "no_sellers" in hard


def test_real_active_token_passes_activity():
    hard, _ = assess(_mk(buyers_h24=400, sellers_h24=300), age_min=300, float_pct=40, cfg=CFG, holder_count=500)
    assert not ({"too_few_holders", "too_few_traders", "no_sellers"} & set(hard))


def test_absolute_seller_floor_catches_near_zero_sellers():
    # ADR-046: 2 sellers among 40 buyers passes the old ==0 check and is below the 50-trader
    # lopsided threshold — but it's the same honeypot shape. The absolute floor catches it.
    hard, _ = assess(_mk(buyers_h24=40, sellers_h24=2), age_min=300, float_pct=40, cfg=CFG)
    assert "no_sellers" in hard


def test_seller_floor_boundary_not_flagged():
    hard, _ = assess(_mk(buyers_h24=40, sellers_h24=3), age_min=300, float_pct=40, cfg=CFG)
    assert "no_sellers" not in hard
