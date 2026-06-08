"""GeckoTerminal pool parsing is pure → unit-tested (ADR-036). The 'solana_<mint>' id prefix matters."""

from solscout.data.geckoterminal import _parse_pool


def _row(**attrs):
    base = {
        "name": "PEPE / SOL",
        "address": "pool1",
        "reserve_in_usd": "25000",
        "fdv_usd": "300000",
        "market_cap_usd": "250000",
        "volume_usd": {"h24": "60000"},
        "price_change_percentage": {"h24": "120"},
        "pool_created_at": "2026-06-01T00:00:00Z",
    }
    base.update(attrs)
    return {
        "attributes": base,
        "relationships": {
            "base_token": {"data": {"id": "solana_MINTBASE"}},
            "quote_token": {"data": {"id": "solana_So111"}},
            "dex": {"data": {"id": "raydium"}},
        },
    }


def test_parse_pool_extracts_bare_mint_and_market():
    p = _parse_pool(_row(), "solana")
    assert p.mint == "MINTBASE"  # network prefix stripped
    assert p.quote_mint == "So111"
    assert p.liquidity_usd == 25000.0
    assert p.volume_24h == 60000.0
    assert p.market_cap_usd == 250000.0
    assert p.dex == "raydium"
    assert p.age_days() is not None and p.age_days() >= 0


def test_parse_pool_without_base_token_is_none():
    row = _row()
    row["relationships"]["base_token"] = {}
    assert _parse_pool(row, "solana") is None


def test_parse_pool_tolerates_missing_numbers():
    p = _parse_pool(_row(reserve_in_usd=None, volume_usd={}), "solana")
    assert p.liquidity_usd is None and p.volume_24h is None
    assert p.mint == "MINTBASE"  # still usable
