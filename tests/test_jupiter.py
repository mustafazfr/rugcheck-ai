"""Jupiter honeypot/sell-sim: the round-trip-tax math is pure → unit-tested (ADR-012).
Plus token-intel parsing (ADR-046): the v2 search payload → JupiterTokenInfo, defensive on every field."""

import json
from pathlib import Path

from solscout.data.jupiter import parse_token_info, round_trip_tax_pct

BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "jupiter_token_search.json").read_text())


def test_no_loss_is_zero_tax():
    assert round_trip_tax_pct(10_000_000, 10_000_000) == 0.0


def test_ten_percent_loss():
    assert abs(round_trip_tax_pct(10_000_000, 9_000_000) - 10.0) < 1e-6


def test_total_loss_is_100():
    assert round_trip_tax_pct(10_000_000, 0) == 100.0


def test_zero_input_is_safe():
    assert round_trip_tax_pct(0, 0) == 0.0


def test_gain_clamps_to_zero():
    # a positive round trip (sol_out > sol_in) shouldn't read as negative tax
    assert round_trip_tax_pct(10_000_000, 11_000_000) == 0.0


# — token intel parsing (ADR-046) —

def test_parse_token_info_full_fixture():
    t = parse_token_info(FIXTURE, BONK)
    assert t is not None and t.available
    assert t.verified is True and "verified" in t.tags
    assert t.organic_label in ("high", "medium", "low") and t.organic_score is not None
    assert t.holder_count and t.holder_count > 100_000  # BONK has ~1M holders
    assert t.mint_auth_disabled is True and t.freeze_auth_disabled is True
    assert t.dev_mints is not None and t.dev_wallet  # Jupiter counted the dev's launches — free
    assert t.first_pool_created_at and t.num_traders_24h is not None


def test_parse_token_info_requires_exact_mint_match():
    # v2 search is fuzzy — an answer about a DIFFERENT mint must not be attributed to ours
    assert parse_token_info(FIXTURE, "SomeOtherMint1111111111111111111111111111111") is None


def test_parse_token_info_defensive_on_garbage():
    assert parse_token_info(None, BONK) is None
    assert parse_token_info({"error": "x"}, BONK) is None  # not a list
    assert parse_token_info([], BONK) is None
    assert parse_token_info([{"id": BONK}], BONK).available  # minimal entry → all-None fields, no crash
    t = parse_token_info([{"id": BONK, "audit": {"devMints": "7"}, "holderCount": "12"}], BONK)
    assert t.dev_mints == 7 and t.holder_count == 12  # numeric strings coerced
    assert t.mint_auth_disabled is None  # absent audit key stays unknown, never fabricated
