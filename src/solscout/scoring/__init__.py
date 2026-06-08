"""scoring — Stage 5. The DETERMINISTIC "worth buying?" decision engine. NOT the LLM.

Layer A — hard veto rules: any breach => REJECT regardless of score (freeze authority,
    un-renounced mint, unlocked LP, handle-reuse over limit, liquidity floor, kill switch,
    risk/portfolio limits).
Layer B — weighted composite 0-100 over {safety, smart_money, social, narrative}; weights
    from config, renormalized over available signals.

Decision: BUY (>= buy_threshold AND a positive gate met) | WATCH | REJECT, with position size.
Fail-safe: any error/missing-critical-data => REJECT/WATCH, never BUY.
The full Decision object is persisted for audit + backtest. REQUIRES unit tests.
"""
