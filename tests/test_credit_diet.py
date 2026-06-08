"""Credit-diet behaviors (ADR-020): holders are cached so recheck doesn't re-spend; deployer off by default."""

from solscout.core.config import Config
from solscout.data.helius import HeliusClient


def test_helius_holders_are_cached(monkeypatch):
    h = HeliusClient("key", cache_ttl_s=600)
    calls = {"n": 0}

    async def fake_post(url, json=None, **kw):
        calls["n"] += 1
        # force the DAS fallback path (gPA returns nothing) so we exercise one cached network call
        if "getProgramAccounts" in str(json):
            return {"result": []}
        return {"result": {"token_accounts": [{"owner": "W", "amount": "100"}]}}

    monkeypatch.setattr(h, "post_json", fake_post)

    async def run():
        a = await h.token_holders("mintA")
        b = await h.token_holders("mintA")  # served from cache → no new network calls
        return a, b

    import asyncio

    a, b = asyncio.run(run())
    assert a == b
    # gPA (empty) + DAS on the first call; the second is fully cached → no further calls
    assert calls["n"] == 2


def test_check_deployer_off_by_default():
    assert Config().smart_money.check_deployer is False
