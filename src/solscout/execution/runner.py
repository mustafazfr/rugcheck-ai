"""Auto-buy orchestration — ties Decision → RiskGate → Executor → persisted Position.

Used by `scan` so the bot acts on its own BUY verdicts. Modes (config.execution.mode):
  - paper      : auto-fill on BUY (PaperExecutor), zero risk.
  - semi_auto  : only at/above semi_auto_min_score, ask for explicit approval (console callback) first.
  - live       : LiveExecutor — NOT yet implemented; runner refuses and stays safe.
Every path goes through RiskGate first; any block / decline / error => no fill (fail-safe).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from ..core.config import Config, Secrets
from ..core.db import Db
from ..core.logging import get_logger
from ..core.models import Decision, ExecutionMode, TokenMarket, Verdict
from ..portfolio import manager
from .base import PortfolioSnapshot, RiskGate
from .paper import PaperExecutor

log = get_logger("solscout.runner")

# returns True to approve a semi_auto buy. Default (None) => decline (fail-safe).
ApprovalFn = Callable[[Decision], Awaitable[bool]]


@dataclass
class TradeOutcome:
    acted: bool
    reason: str


class Runner:
    def __init__(self, cfg: Config, secrets: Secrets, db: Db, approval: ApprovalFn | None = None):
        self.cfg = cfg
        self.secrets = secrets
        self.db = db
        self.gate = RiskGate(cfg, secrets)
        self.approval = approval
        self.paper = PaperExecutor()

    async def _snapshot(self) -> PortfolioSnapshot:
        positions = await self.db.get_open_positions()
        return PortfolioSnapshot(
            open_positions=len(positions),
            total_exposure_sol=sum(p.sol_invested for p in positions),
            realized_pnl_today_sol=await self.db.realized_pnl_today(),
        )

    async def maybe_buy(
        self, decision: Decision, market: TokenMarket | None, sol_usd: float | None
    ) -> TradeOutcome:
        if decision.verdict != Verdict.BUY:
            return TradeOutcome(False, decision.verdict.value.lower())
        if not market or not market.price_usd or not sol_usd:
            return TradeOutcome(False, "no live price")
        if await self.db.holds(decision.mint):
            return TradeOutcome(False, "already holding")

        mode = self.cfg.execution.mode
        if mode == ExecutionMode.LIVE:
            return TradeOutcome(False, "live executor not implemented — staying safe")

        # ADR-037: a BUY needs a real, EXITABLE pool — never enter something we couldn't sell out of.
        liquidity = market.liquidity_usd
        if not liquidity or liquidity < self.cfg.filters.min_liquidity_for_buy_usd:
            return TradeOutcome(False, f"liquidity too thin to exit (${liquidity or 0:,.0f})")

        size = decision.position_size_sol
        # never bet more than max_pool_share_pct of the pool — a bigger trade can't fill at quote. Shrink it.
        max_size_sol = (self.cfg.execution.max_pool_share_pct / 100) * liquidity / sol_usd
        if size > max_size_sol:
            size = round(max_size_sol, 4)
        if size <= 0:
            return TradeOutcome(False, "position shrunk to ~0 by pool-share cap")

        rd = self.gate.pre_trade_check(size, await self._snapshot())
        if not rd.allowed:
            return TradeOutcome(False, "risk: " + "; ".join(rd.reasons))

        if mode == ExecutionMode.SEMI_AUTO:
            if decision.composite_score < self.cfg.execution.semi_auto_min_score:
                return TradeOutcome(
                    False, f"below semi_auto_min_score ({self.cfg.execution.semi_auto_min_score})"
                )
            approved = await self.approval(decision) if self.approval else False
            if not approved:
                return TradeOutcome(False, "approval declined")

        fill = await self.paper.buy(
            decision.mint, size, market.price_usd, sol_usd,
            self.cfg.execution.max_slippage_bps, liquidity_usd=liquidity,
        )
        position = manager.open_position(fill, fill.price_usd, entry_liquidity_usd=liquidity)
        await self.db.save_fill(fill)
        await self.db.open_position(position)
        log.info(
            "paper buy %s · %.4f SOL @ %.8f", decision.mint[:8], fill.sol_amount, fill.price_usd
        )
        return TradeOutcome(True, f"paper-bought {fill.sol_amount:.4f} SOL")
