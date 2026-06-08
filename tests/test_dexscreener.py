"""DexScreener mapping — must NOT return unrelated pairs for a token (regression for the $2.4M/$1.001 bug)."""

import pytest

from solscout.data.dexscreener import DexScreenerClient

MINT = "MyMint11111111111111111111111111111111111111"


def _pair(base_addr, liq, price, name="X"):
    return {
        "baseToken": {"address": base_addr, "name": name, "symbol": name},
        "liquidity": {"usd": liq},
        "priceUsd": str(price),
        "dexId": "ray",
        "pairAddress": "P",
    }


@pytest.fixture
def client(monkeypatch):
    c = DexScreenerClient()

    async def fake(data):
        async def _get_json(url, **kw):
            return data

        monkeypatch.setattr(c, "get_json", _get_json)

    c._set = fake
    return c


async def test_ignores_pairs_where_mint_is_not_base(client):
    # DexScreener returned only an unrelated pair (our mint isn't the base) → must return None, not garbage
    await client._set({"pairs": [_pair("SomeOtherToken", 2_400_000, 1.001)]})
    assert await client.get_token(MINT) is None


async def test_picks_our_token_pair_by_deepest_liquidity(client):
    await client._set(
        {
            "pairs": [
                _pair(MINT, 5_000, 0.001, "REAL"),
                _pair(MINT, 50_000, 0.002, "REAL"),
                _pair("Other", 9_999_999, 1.0),
            ]
        }
    )
    m = await client.get_token(MINT)
    assert m is not None
    assert m.liquidity_usd == 50_000  # deepest OF OUR pairs, not the unrelated whale pair
    assert m.price_usd == 0.002


async def test_no_pairs_returns_none(client):
    await client._set({"pairs": []})
    assert await client.get_token(MINT) is None
