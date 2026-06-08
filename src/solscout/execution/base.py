"""Execution interface + pre-trade RiskGate. Every buy passes the gate; fail-safe = block.

The gate enforces the mandatory safety rails (Golden Rule #4): kill switch, per-trade cap,
max open positions, max total exposure, daily-loss limit, and (for live) the two live-mode flags.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..core import kill_switch
from ..core.config import Config, Secrets
from ..core.models import ExecutionMode, Fill, Position


@dataclass
class PortfolioSnapshot:
    open_positions: int = 0
    total_exposure_sol: float = 0.0
    realized_pnl_today_sol: float = 0.0


@dataclass
class RiskDecision:
    allowed: bool
    reasons: list[str]


class RiskGate:
    def __init__(self, cfg: Config, secrets: Secrets):
        self.cfg = cfg
        self.secrets = secrets

    def pre_trade_check(self, size_sol: float, snap: PortfolioSnapshot) -> RiskDecision:
        ex = self.cfg.execution
        reasons: list[str] = []

        if kill_switch.is_tripped():
            reasons.append(f"kill_switch active: {kill_switch.reason()}")
        if size_sol <= 0:
            reasons.append("non-positive size")
        if size_sol > ex.per_trade_cap_sol + 1e-9:
            reasons.append(f"size {size_sol} > per_trade_cap {ex.per_trade_cap_sol}")
        if snap.open_positions >= ex.max_open_positions:
            reasons.append(f"max_open_positions {ex.max_open_positions} reached")
        if snap.total_exposure_sol + size_sol > ex.max_total_exposure_sol + 1e-9:
            reasons.append(
                f"exposure {snap.total_exposure_sol}+{size_sol} > {ex.max_total_exposure_sol}"
            )
        if snap.realized_pnl_today_sol <= -ex.daily_loss_limit_sol:
            reasons.append(f"daily loss limit hit ({snap.realized_pnl_today_sol} SOL)")

        # live-mode guard: both flags required, plus a hot wallet that is NOT empty
        if ex.mode == ExecutionMode.LIVE:
            if not self.secrets.live_allowed:
                reasons.append("live blocked: SOLSCOUT_ALLOW_LIVE != 1")
            if not self.secrets.hot_wallet_private_key:
                reasons.append("live blocked: no HOT_WALLET_PRIVATE_KEY")

        return RiskDecision(allowed=not reasons, reasons=reasons)


class Executor(Protocol):
    paper: bool

    async def buy(
        self,
        mint: str,
        sol_amount: float,
        token_price_usd: float,
        sol_usd: float,
        max_slippage_bps: int,
    ) -> Fill: ...

    async def sell(
        self, position: Position, token_price_usd: float, sol_usd: float, max_slippage_bps: int
    ) -> Fill: ...
