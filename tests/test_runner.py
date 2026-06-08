"""Runner auto-buy orchestration gates real money → required tests (in-memory SQLite)."""

import pytest

from solscout.core.config import Config, Secrets
from solscout.core.db import Db
from solscout.core.models import Decision, ExecutionMode, TokenMarket, Verdict
from solscout.execution.runner import Runner

SECRETS = Secrets()
MARKET = TokenMarket(mint="m", price_usd=0.01, liquidity_usd=200_000)  # exitable pool (ADR-037)
SOL_USD = 100.0


def _buy(score=80.0, size=0.2, mint="m") -> Decision:
    return Decision(mint=mint, verdict=Verdict.BUY, composite_score=score, position_size_sol=size)


@pytest.fixture
async def db():
    async with Db(":memory:") as d:
        yield d


async def test_paper_auto_buy_opens_position(db):
    r = Runner(Config(), SECRETS, db)
    out = await r.maybe_buy(_buy(), MARKET, SOL_USD)
    assert out.acted
    assert len(await db.get_open_positions()) == 1
    assert await db.holds("m")


async def test_non_buy_does_nothing(db):
    r = Runner(Config(), SECRETS, db)
    out = await r.maybe_buy(Decision(mint="m", verdict=Verdict.WATCH), MARKET, SOL_USD)
    assert not out.acted and out.reason == "watch"


async def test_no_double_buy_when_already_holding(db):
    r = Runner(Config(), SECRETS, db)
    await r.maybe_buy(_buy(), MARKET, SOL_USD)
    out = await r.maybe_buy(_buy(), MARKET, SOL_USD)
    assert not out.acted and out.reason == "already holding"


async def test_risk_gate_blocks_oversized(db):
    r = Runner(Config(), SECRETS, db)
    out = await r.maybe_buy(_buy(size=99.0), MARKET, SOL_USD)  # > per_trade_cap
    assert not out.acted and out.reason.startswith("risk:")
    assert len(await db.get_open_positions()) == 0


async def test_live_mode_refuses(db):
    cfg = Config()
    cfg.execution.mode = ExecutionMode.LIVE
    out = await Runner(cfg, SECRETS, db).maybe_buy(_buy(), MARKET, SOL_USD)
    assert not out.acted and "live executor not implemented" in out.reason


async def test_semi_auto_declined_by_default(db):
    cfg = Config()
    cfg.execution.mode = ExecutionMode.SEMI_AUTO
    # no approval callback => decline (fail-safe)
    out = await Runner(cfg, SECRETS, db, approval=None).maybe_buy(_buy(score=90), MARKET, SOL_USD)
    assert not out.acted and out.reason == "approval declined"


async def test_semi_auto_below_threshold_skips(db):
    cfg = Config()
    cfg.execution.mode = ExecutionMode.SEMI_AUTO  # semi_auto_min_score = 75
    out = await Runner(cfg, SECRETS, db).maybe_buy(_buy(score=60), MARKET, SOL_USD)
    assert not out.acted and "below semi_auto_min_score" in out.reason


async def test_semi_auto_approved_buys(db):
    cfg = Config()
    cfg.execution.mode = ExecutionMode.SEMI_AUTO

    async def approve(_decision):
        return True

    out = await Runner(cfg, SECRETS, db, approval=approve).maybe_buy(
        _buy(score=90), MARKET, SOL_USD
    )
    assert out.acted
    assert await db.holds("m")
