"""ADR-046 persistent web caches: report / wallet-summary / holders / funder roundtrips, TTL expiry,
the funder NULL-hit-vs-miss distinction, and prune housekeeping (in-memory SQLite)."""

import pytest

from solscout.core.db import Db


@pytest.fixture
async def db():
    async with Db(":memory:") as d:
        yield d


# — report cache (L2) —

async def test_report_roundtrip_and_ttl(db):
    rep = {"mint": "M1", "score": 42, "level": "CAUTION", "checks": [{"id": "liq", "status": "warn"}]}
    assert await db.cache_get_report("M1", ttl_s=900) is None
    await db.cache_put_report("M1", rep)
    assert await db.cache_get_report("M1", ttl_s=900) == rep
    # TTL is decided at READ time: an absurdly small ttl makes the same row a miss
    assert await db.cache_get_report("M1", ttl_s=-1) is None


async def test_report_overwrite(db):
    await db.cache_put_report("M1", {"score": 10})
    await db.cache_put_report("M1", {"score": 90})
    assert (await db.cache_get_report("M1", ttl_s=900))["score"] == 90


# — wallet tx summaries —

async def test_wallet_summary_roundtrip(db):
    s = {"swaps_total": 7, "distinct_tokens": 3, "net_sol": -1.5,
         "prior_creations": 2, "funder": "F1", "age_days": 12, "tx_count": 60}
    assert await db.get_wallet_summary("W1", ttl_s=1000) is None
    await db.put_wallet_summary("W1", s, tx_limit=100)
    assert await db.get_wallet_summary("W1", ttl_s=1000) == s
    assert await db.get_wallet_summary("W1", ttl_s=-1) is None  # expired


# — holders cache —

async def test_holders_roundtrip_preserves_order_and_complete(db):
    holders = [("A", 500), ("B", 300), ("C", 1)]
    assert await db.get_holders("M1", ttl_s=1000) is None
    await db.put_holders("M1", holders, complete=True)
    got, complete = await db.get_holders("M1", ttl_s=1000)
    assert got == holders and complete is True
    await db.put_holders("M2", holders, complete=False)
    assert (await db.get_holders("M2", ttl_s=1000))[1] is False


# — funder cache: a cached None ("traced, none found") is STILL a hit —

async def test_funder_null_hit_vs_miss(db):
    hit, f = await db.get_funder("W1", ttl_s=1000)
    assert hit is False and f is None  # never traced → miss
    await db.put_funder("W1", None)  # traced, no dominant funder found
    hit, f = await db.get_funder("W1", ttl_s=1000)
    assert hit is True and f is None  # cached-None = hit (don't re-spend)
    await db.put_funder("W2", "FUNDER")
    assert await db.get_funder("W2", ttl_s=1000) == (True, "FUNDER")
    assert (await db.get_funder("W2", ttl_s=-1))[0] is False  # expired → miss


# — prune deletes only expired rows —

async def test_prune_caches(db):
    await db.cache_put_report("M1", {"score": 1})
    await db.put_wallet_summary("W1", {"swaps_total": 0}, tx_limit=10)
    await db.put_holders("M1", [("A", 1)], complete=True)
    await db.put_funder("W1", "F")
    # everything still fresh → prune with generous TTLs keeps all
    await db.prune_caches(1000, 1000, 1000, 1000)
    assert await db.cache_get_report("M1", ttl_s=1000) is not None
    # prune with ttl -1 (everything is "older than now+1") deletes all
    await db.prune_caches(-1, -1, -1, -1)
    assert await db.cache_get_report("M1", ttl_s=1000) is None
    assert await db.get_wallet_summary("W1", ttl_s=1000) is None
    assert await db.get_holders("M1", ttl_s=1000) is None
    assert (await db.get_funder("W1", ttl_s=1000))[0] is False
