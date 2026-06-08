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
