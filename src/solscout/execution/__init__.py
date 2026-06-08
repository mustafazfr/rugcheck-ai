"""execution — Stage 6. Place trades. PAPER by default.

Executor interface: buy(mint, size, max_slippage) -> Fill ; sell(...) -> Fill
    paper.py   PaperExecutor — simulates fill vs live quote + slippage/fee model. DEFAULT.
    live.py    LiveExecutor — Jupiter route (+ optional Jito bundle). Constructed ONLY when
               config.execution.mode == 'live' AND env SOLSCOUT_ALLOW_LIVE == '1'.

Pre-trade gate (live): kill switch, daily-loss limit, max open positions, per-trade cap,
hot-wallet-is-not-main check. Paper and live persist Fills identically => shared analytics.
NEVER weaken the safety rails (Golden Rule #4).
"""
