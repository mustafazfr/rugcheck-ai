"""LiveExecutor must stay LOCKED — it can never place a real trade until implemented/unlocked."""

import pytest

from solscout.core.config import Config, Secrets
from solscout.core.models import ExecutionMode, Position
from solscout.execution.live import LiveExecutor, LiveExecutorLocked


def _live_cfg() -> Config:
    cfg = Config()
    cfg.execution.mode = ExecutionMode.LIVE
    return cfg


async def test_buy_is_locked_even_with_all_flags():
    # even if someone sets every flag, submission is absent by design → raises, never trades
    cfg = _live_cfg()
    secrets = Secrets(solscout_allow_live="1", hot_wallet_private_key="dummy")
    ex = LiveExecutor(cfg, secrets)
    assert ex.preflight() == []  # flags look "ok"
    with pytest.raises(LiveExecutorLocked):
        await ex.buy("m", 0.1, 0.01, 100.0, 300)


async def test_sell_is_locked():
    ex = LiveExecutor(_live_cfg(), Secrets(solscout_allow_live="1", hot_wallet_private_key="d"))
    with pytest.raises(LiveExecutorLocked):
        await ex.sell(
            Position(mint="m", token_amount=1, avg_price_usd=1, sol_invested=1), 1.0, 100.0, 300
        )


def test_preflight_reports_missing_flags():
    reasons = LiveExecutor(Config(), Secrets()).preflight()
    assert any("mode != live" in r for r in reasons)
    assert any("SOLSCOUT_ALLOW_LIVE" in r for r in reasons)
