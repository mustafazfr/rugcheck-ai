"""Stage 7 — position lifecycle + rule-based exits. Exit logic is pure => unit-tested.

Exit priority (protect downside first): stop_loss > take_profit > trailing_stop > time_stop.
'smart-money exited' exit is added when the smart-money tracker lands (ADR-008 / config).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..core.config import PositionCfg
from ..core.models import Fill, Position


def open_position(
    fill: Fill, entry_price_usd: float, entry_liquidity_usd: float | None = None
) -> Position:
    return Position(
        mint=fill.mint,
        token_amount=fill.token_amount,
        avg_price_usd=entry_price_usd,
        sol_invested=fill.sol_amount,
        high_water_price=entry_price_usd,
        entry_liquidity_usd=entry_liquidity_usd,
    )


def mark(position: Position, price_usd: float) -> None:
    """High-water tracking. `price_usd` should be the REALIZABLE (impact-adjusted) price so a fake spike on a
    thin pool can't set a fake high-water that later triggers a fictional take-profit/trailing (ADR-037)."""
    if position.high_water_price is None or price_usd > position.high_water_price:
        position.high_water_price = price_usd


def is_rugged(position: Position, liquidity_usd: float | None, cfg: PositionCfg) -> bool:
    """Liquidity collapsed → you can't actually sell. Fires on an absolute floor OR a big drop from entry."""
    if liquidity_usd is None:
        return False
    if liquidity_usd < cfg.rug_liquidity_usd:
        return True
    entry = position.entry_liquidity_usd
    return bool(entry and liquidity_usd < entry * (1 - cfg.rug_liquidity_drop_pct / 100))


def evaluate_exit(
    position: Position,
    price_usd: float,
    age_minutes: float,
    cfg: PositionCfg,
    liquidity_usd: float | None = None,
) -> str | None:
    """Return the exit reason that fires, or None to hold. `price_usd` = REALIZABLE price (ADR-037).
    Rug (liquidity gone) is checked FIRST — bail at the honest near-zero value, don't wait for stop-loss."""
    if is_rugged(position, liquidity_usd, cfg):
        return "rugged"
    avg = position.avg_price_usd
    if avg <= 0:
        return None
    change_pct = 100 * (price_usd - avg) / avg

    if change_pct <= -cfg.stop_loss_pct:
        return "stop_loss"
    if cfg.take_profit_pct and change_pct >= max(cfg.take_profit_pct):
        return "take_profit"
    hw = position.high_water_price or avg
    if hw > avg and price_usd <= hw * (1 - cfg.trailing_stop_pct / 100):
        return "trailing_stop"
    if age_minutes >= cfg.time_stop_minutes:
        return "time_stop"
    return None


def close_position(position: Position, sell_fill: Fill) -> Position:
    position.closed = True
    position.closed_at = datetime.now(timezone.utc)
    position.realized_pnl_sol = round(sell_fill.sol_amount - position.sol_invested, 6)
    return position
