"""LiveExecutor — REAL on-chain swaps via Jupiter (+ optional Jito). SCAFFOLDED BUT LOCKED.

Status: structure + the mandatory pre-flight safety gate are in place, but the actual transaction
*submission* is deliberately NOT implemented — `buy`/`sell` raise. This guarantees the bot can NEVER
place a real trade by accident, while leaving a clear, reviewed shape to fill in once:
  (1) the paper Edge-Validation Gate (docs/BACKUP_PLAN.md) is cleared, AND
  (2) execution.mode == 'live' AND env SOLSCOUT_ALLOW_LIVE == '1', AND
  (3) HOT_WALLET_PRIVATE_KEY is a DEDICATED low-balance wallet (never the main wallet).

Implementation plan when unlocked (each step tested on a tiny amount first):
  - load Keypair from HOT_WALLET_PRIVATE_KEY (solders); assert it's the configured hot wallet.
  - quote: GET Jupiter /quote (inputMint=WSOL, outputMint=mint, amount, slippageBps).
  - swap: POST /swap → versioned tx; sign; submit via RPC (or wrap in a Jito bundle with tip).
  - confirm; parse the real filled amount; return a Fill(paper=False, tx_sig=...).
The Runner currently refuses live regardless, so this class is not yet wired into the live path.
"""

from __future__ import annotations

from ..core.config import Config, Secrets
from ..core.logging import get_logger
from ..core.models import ExecutionMode, Fill, Position

log = get_logger("solscout.live")


class LiveExecutorLocked(RuntimeError):
    """Raised if anything tries to execute a live trade before the executor is implemented/unlocked."""


class LiveExecutor:
    paper = False

    def __init__(self, cfg: Config, secrets: Secrets):
        self.cfg = cfg
        self.secrets = secrets

    def preflight(self) -> list[str]:
        """Hard gate. Returns blocking reasons; empty list would mean 'allowed' — but see _locked()."""
        reasons: list[str] = []
        if self.cfg.execution.mode != ExecutionMode.LIVE:
            reasons.append("execution.mode != live")
        if not self.secrets.live_allowed:
            reasons.append("SOLSCOUT_ALLOW_LIVE != 1")
        if not self.secrets.hot_wallet_private_key:
            reasons.append("no HOT_WALLET_PRIVATE_KEY (dedicated hot wallet required)")
        return reasons

    def _locked(self):
        raise LiveExecutorLocked(
            "LiveExecutor is scaffolded but intentionally not implemented. Live trading stays OFF until "
            "the paper Edge-Validation Gate is cleared and the executor is implemented + reviewed. "
            f"Preflight: {self.preflight() or 'flags OK, but submission code is absent by design'}"
        )

    async def buy(
        self,
        mint: str,
        sol_amount: float,
        token_price_usd: float,
        sol_usd: float,
        max_slippage_bps: int,
    ) -> Fill:
        self._locked()

    async def sell(
        self, position: Position, token_price_usd: float, sol_usd: float, max_slippage_bps: int
    ) -> Fill:
        self._locked()
