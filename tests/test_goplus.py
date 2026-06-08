"""GoPlus parsing + flag logic (ADR-041) is pure → required tests. Fails OPEN (unavailable → no flags)."""

from solscout.core.config import GoPlusCfg
from solscout.data.goplus import GoPlusReport, _parse, flags

CFG = GoPlusCfg()


def test_parse_clean_token():
    v = {
        "mintable": {"authority": [], "status": "0"},
        "freezable": {"authority": [], "status": "0"},
        "closable": {"authority": [], "status": "0"},
        "non_transferable": 0,
        "transfer_hook": [],
        "trusted_token": "0",
        "holder_count": "12345",
        "lp_holders": [{"address": "x"}],
        "dex": [{"id": "raydium"}],
        "creators": [],
    }
    r = _parse("m", v)
    assert r.available and not r.mint_authority and not r.freeze_authority
    assert r.holder_count == 12345 and r.lp_holders == 1 and r.dex_listed
    assert flags(r, CFG) == ([], [])


def test_freeze_and_mint_are_hard():
    v = {"mintable": {"status": "1"}, "freezable": {"status": "1"}}
    r = _parse("m", v)
    hard, _ = flags(r, CFG)
    assert "goplus_mintable" in hard and "goplus_freezable" in hard


def test_transfer_hook_is_honeypot():
    r = _parse("m", {"transfer_hook": [{"program": "x"}]})
    assert r.transfer_hook
    assert "goplus_transfer_hook" in flags(r, CFG)[0]


def test_non_transferable_is_honeypot():
    r = _parse("m", {"non_transferable": "1"})
    assert "goplus_non_transferable" in flags(r, CFG)[0]


def test_malicious_creator_flagged():
    v = {"creators": [{"address": "bad", "malicious_address": "1"}]}
    r = _parse("m", v)
    assert r.malicious_creator
    assert "goplus_malicious_creator" in flags(r, CFG)[0]


def test_malicious_metadata_authority():
    v = {"metadata_mutable": {"status": "1", "metadata_upgrade_authority": [{"address": "a", "malicious_address": "1"}]}}
    r = _parse("m", v)
    assert r.malicious_creator and "goplus_malicious_creator" in flags(r, CFG)[0]


def test_high_transfer_fee_hard():
    r = _parse("m", {"transfer_fee": "25"})
    assert r.transfer_fee_pct == 25.0
    assert "goplus_high_transfer_fee" in flags(r, CFG)[0]


def test_trusted_token_overrides():
    v = {"trusted_token": "1", "mintable": {"status": "1"}}  # allow-listed → no flags despite mintable
    r = _parse("m", v)
    assert r.trusted and flags(r, CFG) == ([], [])


def test_unavailable_no_flags():
    assert flags(GoPlusReport(mint="m", available=False), CFG) == ([], [])


def test_closable_is_soft_mutable_is_not():
    v = {"closable": {"status": "1"}, "metadata_mutable": {"status": "1"}}
    r = _parse("m", v)
    hard, soft = flags(r, CFG)
    assert hard == [] and "goplus_closable" in soft
    assert "goplus_mutable_metadata" not in soft  # too common → info-only, not a penalty
