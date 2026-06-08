"""Position exit rules gate real money → required tests."""

from solscout.core.config import PositionCfg
from solscout.core.models import Fill, Position
from solscout.portfolio import manager

CFG = PositionCfg()  # stop_loss 35, take_profit [50,100,300], trailing 25, time_stop 240


def _pos() -> Position:
    fill = Fill(mint="m", side="buy", sol_amount=1.0, token_amount=50.0, price_usd=2.0)
    return manager.open_position(fill, entry_price_usd=2.0)


def test_open_position():
    p = _pos()
    assert p.avg_price_usd == 2.0 and p.sol_invested == 1.0 and p.high_water_price == 2.0


def test_stop_loss_fires():
    assert (
        manager.evaluate_exit(_pos(), price_usd=1.2, age_minutes=1, cfg=CFG) == "stop_loss"
    )  # -40%


def test_take_profit_fires():
    assert (
        manager.evaluate_exit(_pos(), price_usd=8.0, age_minutes=1, cfg=CFG) == "take_profit"
    )  # +300%


def test_trailing_stop_fires():
    p = _pos()
    manager.mark(p, 4.0)  # high-water +100%
    assert (
        manager.evaluate_exit(p, price_usd=3.0, age_minutes=1, cfg=CFG) == "trailing_stop"
    )  # -25% off HW


def test_time_stop_fires():
    assert manager.evaluate_exit(_pos(), price_usd=2.0, age_minutes=240, cfg=CFG) == "time_stop"


def test_hold_when_nothing_triggers():
    p = _pos()
    manager.mark(p, 2.5)
    assert manager.evaluate_exit(p, price_usd=2.5, age_minutes=10, cfg=CFG) is None


def test_close_computes_pnl():
    p = _pos()
    sell = Fill(mint="m", side="sell", sol_amount=2.0, token_amount=50.0, price_usd=4.0)
    closed = manager.close_position(p, sell)
    assert closed.closed and abs(closed.realized_pnl_sol - 1.0) < 1e-9  # 2.0 out - 1.0 in


# --- ADR-037: rug exit (liquidity collapse) fires FIRST, even if price still looks high ---


def _pos_liq(entry_liq: float) -> Position:
    fill = Fill(mint="m", side="buy", sol_amount=1.0, token_amount=50.0, price_usd=2.0)
    return manager.open_position(fill, entry_price_usd=2.0, entry_liquidity_usd=entry_liq)


def test_rug_exit_on_absolute_floor():
    # price still "+50%" but liquidity collapsed below the floor → rugged, not take_profit
    p = _pos_liq(80_000)
    assert manager.evaluate_exit(p, price_usd=3.0, age_minutes=1, cfg=CFG, liquidity_usd=500) == "rugged"


def test_rug_exit_on_big_drop_from_entry():
    # liquidity above the absolute floor but dropped 80% from entry → still a rug
    p = _pos_liq(100_000)
    assert manager.evaluate_exit(p, price_usd=2.0, age_minutes=1, cfg=CFG, liquidity_usd=15_000) == "rugged"


def test_healthy_liquidity_does_not_rug():
    p = _pos_liq(100_000)
    assert manager.evaluate_exit(p, price_usd=2.0, age_minutes=5, cfg=CFG, liquidity_usd=90_000) is None


def test_no_liquidity_data_skips_rug_check():
    # unknown liquidity (None) must NOT false-trigger a rug
    assert manager.is_rugged(_pos_liq(100_000), None, CFG) is False
