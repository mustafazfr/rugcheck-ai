"""RugCheck flag logic (ADR-039) is pure → required tests. Fails OPEN (unavailable → no flags)."""

from solscout.core.config import RugCheckCfg
from solscout.data.rugcheck import RugCheckReport, flags

CFG = RugCheckCfg()  # veto_score 60, soft_score 35, max_insider_holders 3


def _rep(**kw) -> RugCheckReport:
    base = dict(mint="m", available=True, rugged=False, score=10, insider_holders=0, risks=[])
    base.update(kw)
    return RugCheckReport(**base)


def test_unavailable_report_no_flags():
    hard, soft = flags(RugCheckReport(mint="m", available=False), CFG)
    assert hard == [] and soft == []


def test_low_score_clean():
    hard, soft = flags(_rep(score=7), CFG)  # BONK-like
    assert hard == [] and soft == []


def test_rugged_is_hard_veto():
    hard, _ = flags(_rep(rugged=True), CFG)
    assert "rugcheck_rugged" in hard


def test_high_score_is_hard_veto():
    hard, _ = flags(_rep(score=71), CFG)  # WIF-like
    assert "rugcheck_high_risk" in hard


def test_elevated_score_is_soft():
    hard, soft = flags(_rep(score=45), CFG)
    assert hard == [] and "rugcheck_elevated" in soft


def test_insiders_is_hard_veto():
    hard, _ = flags(_rep(score=10, insider_holders=4), CFG)
    assert "rugcheck_insiders" in hard


def test_critical_named_risk_vetoes():
    hard, _ = flags(_rep(score=10, risks=[("Honeypot detected", "danger", 5000)]), CFG)
    assert any(f.startswith("rc_honeypot") for f in hard)


def test_warn_risk_does_not_veto():
    hard, _ = flags(_rep(score=10, risks=[("Mutable metadata", "warn", 100)]), CFG)
    assert hard == []


def test_disabled_returns_nothing():
    hard, soft = flags(_rep(rugged=True, score=99), RugCheckCfg(enabled=False))
    assert hard == [] and soft == []


# — ADR-043: richer overview fields (LP lock per-market, insider networks, known accounts) —

def test_parse_overview_fields():
    from solscout.data.rugcheck import RugCheckClient
    import asyncio
    payload = {
        "score_normalised": 7, "rugged": False, "creator": "DEV", "creatorBalance": 0,
        "totalLPProviders": 80, "totalMarketLiquidity": 2_000_000,
        # equal-liquidity measurable pools at 100% and 0% → liquidity-weighted lock is 50%
        "markets": [{"lp": {"lpLockedPct": 100.0, "lpLockedUSD": 1000, "baseUSD": 1000}},
                    {"lp": {"lpLockedPct": 0.0, "baseUSD": 1000, "lpMaxSupply": 1e9}}],
        "insiderNetworks": [{"id": "damp-fawn-possum", "size": 2888, "tokenAmount": 100}],
        "knownAccounts": {"AddrA": {"name": "Pump.fun AMM", "type": "AMM"}, "AddrB": {"type": "LOCKER"}},
        "topHolders": [], "risks": [],
    }
    c = RugCheckClient()

    async def fake(url, cache_key=None):
        return payload
    c.get_json = fake  # type: ignore
    r = asyncio.run(c.report("m"))
    assert r.lp_locked_pct == 50.0  # liquidity-weighted across the two equal pools
    assert r.total_lp_providers == 80 and r.markets_count == 2
    assert r.creator_balance == 0
    assert r.insider_networks[0]["id"] == "damp-fawn-possum" and r.insider_networks[0]["size"] == 2888
    assert r.known_accounts["AddrA"] == "Pump.fun AMM" and r.known_accounts["AddrB"] == "LOCKER"


# — ADR-043 security fix: liquidity-weighted LP lock (a deep unlocked pool isn't hidden) —

def test_weighted_lp_lock_unlocked_deep_pool_flags():
    from solscout.data.rugcheck import _weighted_lp_lock
    # tiny 100%-locked pool + DEEP pool that IS measurable (has lock telemetry) but 0% locked → low weighted
    markets = [
        {"lp": {"lpLockedPct": 100.0, "lpLockedUSD": 1000, "baseUSD": 1000}},
        {"lp": {"lpLockedPct": 0.0, "baseUSD": 500000, "lpMaxSupply": 9e9}},  # real LP supply, just not locked
    ]
    w = _weighted_lp_lock(markets)
    assert w is not None and w < 50  # naive max() would have said 100 (unsafe)


def test_weighted_lp_lock_all_locked():
    from solscout.data.rugcheck import _weighted_lp_lock
    markets = [{"lp": {"lpLockedPct": 100.0, "lpLockedUSD": 1000, "baseUSD": 1000}},
               {"lp": {"lpLockedPct": 99.0, "lpLockedUSD": 1980, "baseUSD": 2000}}]
    assert _weighted_lp_lock(markets) > 98


def test_weighted_lp_lock_skips_unmeasurable_pools():
    from solscout.data.rugcheck import _weighted_lp_lock
    # Meteora/Orca-style: liquidity present but pct=0 AND no lock telemetry = "can't tell", NOT unlocked
    assert _weighted_lp_lock([{"lp": {"lpLockedPct": 0.0, "baseUSD": 500000}}]) is None


def test_weighted_lp_lock_none_when_no_liquidity():
    from solscout.data.rugcheck import _weighted_lp_lock
    assert _weighted_lp_lock([{"lp": {"lpLockedPct": 50.0, "baseUSD": 0}}]) is None
    assert _weighted_lp_lock([]) is None


def test_real_zero_global_lock_not_overridden():
    # falsy-zero bug fix: a real top-level lpLockedPct of 0 must survive, not be replaced by a per-market value
    from solscout.data.rugcheck import RugCheckClient
    import asyncio
    c = RugCheckClient()

    async def fake(url, cache_key=None):
        return {"score_normalised": 9, "lpLockedPct": 0,
                "markets": [{"lp": {"lpLockedPct": 100.0, "baseUSD": 9999}}], "topHolders": [], "risks": []}
    c.get_json = fake  # type: ignore
    r = asyncio.run(c.report("m"))
    assert r.lp_locked_pct == 0  # not silently bumped to 100 by the per-market pool
