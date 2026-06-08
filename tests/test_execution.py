"""RiskGate + PaperExecutor gate real money → required tests."""

from solscout.core import kill_switch
from solscout.core.config import Config, Secrets
from solscout.core.models import ExecutionMode
from solscout.execution.base import PortfolioSnapshot, RiskGate
from solscout.execution.paper import PaperExecutor

SECRETS = Secrets()


def _gate(cfg: Config | None = None) -> RiskGate:
    return RiskGate(cfg or Config(), SECRETS)


def test_normal_buy_allowed():
    assert _gate().pre_trade_check(0.1, PortfolioSnapshot()).allowed


def test_over_per_trade_cap_blocked():
    rd = _gate().pre_trade_check(0.5, PortfolioSnapshot())  # cap 0.25
    assert not rd.allowed


def test_max_open_positions_blocked():
    rd = _gate().pre_trade_check(0.1, PortfolioSnapshot(open_positions=5))
    assert not rd.allowed


def test_exposure_ceiling_blocked():
    rd = _gate().pre_trade_check(0.2, PortfolioSnapshot(total_exposure_sol=1.9))  # 2.1 > 2.0
    assert not rd.allowed


def test_daily_loss_limit_blocked():
    rd = _gate().pre_trade_check(0.1, PortfolioSnapshot(realized_pnl_today_sol=-1.0))
    assert not rd.allowed


def test_kill_switch_blocks():
    kill_switch.trip("test")
    try:
        rd = _gate().pre_trade_check(0.1, PortfolioSnapshot())
        assert not rd.allowed
        assert any("kill_switch" in r for r in rd.reasons)
    finally:
        kill_switch.reset()


def test_live_mode_requires_flags():
    cfg = Config()
    cfg.execution.mode = ExecutionMode.LIVE
    rd = RiskGate(cfg, Secrets()).pre_trade_check(0.1, PortfolioSnapshot())
    assert not rd.allowed
    assert any("SOLSCOUT_ALLOW_LIVE" in r for r in rd.reasons)


async def test_paper_buy_math():
    fill = await PaperExecutor(fee_bps=0).buy(
        "m", 1.0, token_price_usd=2.0, sol_usd=100.0, max_slippage_bps=0
    )
    assert fill.side == "buy" and fill.paper
    assert fill.sol_amount == 1.0
    assert abs(fill.token_amount - 50.0) < 1e-6  # 100 USD / 2.0


async def test_paper_buy_slippage_and_fee():
    fill = await PaperExecutor(fee_bps=100).buy(
        "m", 1.0, token_price_usd=2.0, sol_usd=100.0, max_slippage_bps=10_000
    )
    # eff price 4.0, usd_in 99 → 24.75 tokens
    assert abs(fill.token_amount - 24.75) < 1e-6


async def test_paper_sell_returns_sol():
    from solscout.core.models import Position

    pos = Position(mint="m", token_amount=50.0, avg_price_usd=2.0, sol_invested=1.0)
    fill = await PaperExecutor(fee_bps=0).sell(
        pos, token_price_usd=4.0, sol_usd=100.0, max_slippage_bps=0
    )
    assert fill.side == "sell"
    assert abs(fill.sol_amount - 2.0) < 1e-6  # 50*4=200 USD / 100


# --- ADR-037: liquidity-aware price impact (the fix for fictional +300% fills) ---


def test_impact_factor_curve():
    from solscout.execution.paper import impact_factor

    assert impact_factor(10, 1_000_000) > 0.999  # tiny trade, deep pool → ~no impact
    assert abs(impact_factor(5_000, 10_000) - 0.5) < 1e-9  # value == quote reserve → lose half
    assert impact_factor(100, 0.5) < 0.01  # rugged pool → ~total loss
    assert impact_factor(100, None) == 0.0  # no pool → can't get value out


async def test_sell_into_thin_pool_is_haircut_not_quoted():
    from solscout.core.models import Position

    # position worth $5,000 at mid, but pool liquidity only $10,000 → realize ~half, NOT the full $5k
    pos = Position(mint="m", token_amount=50_000, avg_price_usd=0.05, sol_invested=1.0)
    fill = await PaperExecutor(fee_bps=0).sell(
        pos, token_price_usd=0.1, sol_usd=100.0, max_slippage_bps=0, liquidity_usd=10_000
    )
    # mid value = 50_000*0.1 = $5,000; realized ≈ 5000*0.5 = $2,500 → 25 SOL (not 50)
    assert abs(fill.sol_amount - 25.0) < 0.5


async def test_sell_into_rugged_pool_returns_almost_nothing():
    from solscout.core.models import Position

    pos = Position(mint="m", token_amount=50_000, avg_price_usd=0.05, sol_invested=1.0)
    fill = await PaperExecutor(fee_bps=0).sell(
        pos, token_price_usd=0.1, sol_usd=100.0, max_slippage_bps=0, liquidity_usd=1.0
    )
    assert fill.sol_amount < 0.05  # liquidity gone → the fictional +profit is impossible
