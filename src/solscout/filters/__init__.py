"""filters — Stage 1. Cheap, fast, DETERMINISTIC on-chain rug checks. Runs first to
reject ~95% of candidates before any paid API / LLM cost.

Checks (thresholds from config, never hardcoded): mint authority renounced, freeze
authority null, LP burned/locked, top-10 holder %, dev hold %, bundle/sniper cluster %,
liquidity floor.

Output: core.models.FilterResult(passed, hard_flags, metrics). Any hard flag => REJECT.
REQUIRES unit tests (this gates real money).
"""
