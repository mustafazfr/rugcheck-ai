"""Bundle / sybil-cluster detection (ADR-038) — the Bubblemaps signal. Pure → required tests."""

from solscout.core.config import ClusterCfg
from solscout.enrich.cluster import detect_bundle, dominant_funder
from solscout.enrich.discovery import INFRA_WALLETS

CFG = ClusterCfg()  # min_cluster_size 4, max_funder_share 0.35


def _tx(frm, to, amt):
    return {"nativeTransfers": [{"fromUserAccount": frm, "toUserAccount": to, "amount": amt}]}


def test_dominant_funder_picks_biggest_sol_source():
    txs = [_tx("FUNDER", "W", 1000), _tx("OTHER", "W", 10), _tx("FUNDER", "W", 5000)]
    assert dominant_funder(txs, "W") == "FUNDER"


def test_dominant_funder_ignores_infra_and_self():
    infra = next(iter(INFRA_WALLETS))
    txs = [_tx(infra, "W", 9999), _tx("W", "W", 9999)]
    assert dominant_funder(txs, "W") is None


def test_dominant_funder_none_when_no_incoming():
    assert dominant_funder([_tx("X", "OTHER", 100)], "W") is None


def test_bundle_by_cluster_size():
    fb = {f"H{i}": "BUNDLER" for i in range(4)}  # 4 top holders, ONE funder
    flags, size, funder = detect_bundle(fb, CFG)
    assert "bundled_holders" in flags and size == 4 and funder == "BUNDLER"


def test_bundle_by_share():
    # 3 of 8 traced share one funder = 37.5% ≥ 0.35
    fb = {"H1": "B", "H2": "B", "H3": "B", "H4": "x", "H5": "y", "H6": "z", "H7": "q", "H8": "r"}
    flags, size, _ = detect_bundle(fb, CFG)
    assert "bundled_holders" in flags and size == 3


def test_clean_distribution_no_bundle():
    fb = {f"H{i}": f"funder{i}" for i in range(8)}  # all distinct funders
    flags, size, _ = detect_bundle(fb, CFG)
    assert flags == [] and size == 1


def test_no_funders_no_bundle():
    assert detect_bundle({"H1": None, "H2": None}, CFG) == ([], 0, None)
