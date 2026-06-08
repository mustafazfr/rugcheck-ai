"""Smart-money signal feeds the positive gate that can enable BUY → required tests."""

from solscout.core.config import Config
from solscout.enrich import smartmoney

CFG = Config()  # smart_money.watchlist_min_winrate = 0.6


def test_overlap_detects_proven_wallet():
    r = smartmoney.score_watchlist_overlap("m", ["Wgood", "Wother"], {"Wgood": 0.8}, CFG)
    assert r.smart_wallets_in == ["Wgood"]
    assert r.best_wallet_winrate == 0.8
    assert r.score is not None and r.score >= 0.6


def test_below_min_winrate_excluded():
    r = smartmoney.score_watchlist_overlap("m", ["Wlow"], {"Wlow": 0.4}, CFG)
    assert r.smart_wallets_in == []
    assert r.score is None  # absent, not a penalty


def test_one_graduate_proxy_excluded_popcat_protection():
    # the Popcat wallet: only 1 graduate → winrate 0.62 < quality bar 0.70 → NOT counted
    r = smartmoney.score_watchlist_overlap("m", ["Wpopcat"], {"Wpopcat": 0.62}, CFG)
    assert r.smart_wallets_in == []  # filtered out → no gate → no bad buy
    r2 = smartmoney.score_watchlist_overlap("m", ["Wproven"], {"Wproven": 0.70}, CFG)
    assert r2.smart_wallets_in == ["Wproven"]  # proven (>=2 graduates) → counts


def test_no_overlap_score_is_none():
    r = smartmoney.score_watchlist_overlap("m", ["Wa", "Wb"], {"Wc": 0.9}, CFG)
    assert r.smart_wallets_in == [] and r.score is None


def test_empty_owners_returns_none():
    assert smartmoney.score_watchlist_overlap("m", [], {"Wc": 0.9}, CFG) is None


def test_count_prior_creations():
    txs = [
        {"type": "CREATE"},
        {"type": "TRANSFER"},
        {"description": "Created a new token"},
        {"type": "TOKEN_MINT"},
    ]
    assert smartmoney.count_prior_creations(txs) == 3
