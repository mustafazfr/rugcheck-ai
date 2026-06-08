"""Stage 3 — smart-money + deployer reputation.

Two signals, both hard to fake (real on-chain capital / history):
  - watchlist overlap : do proven-PnL wallets hold this token? (pure, unit-tested)
  - deployer signal    : creator wallet's prior token-creation history via Helius enhanced txns

`deployer_rugged_before` is left None in v1 (labeling a launch as a 'rug' needs outcome tracking — a
later job); `deployer_prior_launches` is real now and already a useful caution signal.
"""

from __future__ import annotations

from ..core.config import Config
from ..core.models import SmartMoneyReport

# Helius enhanced-tx 'type' values that indicate the wallet created/minted a token
_CREATE_TYPES = {"CREATE", "TOKEN_MINT", "INITIALIZE_MINT", "CREATE_RAYDIUM_POOL"}


def score_watchlist_overlap(
    mint: str, top_owners: list[str], watchlist: dict[str, float], cfg: Config
) -> SmartMoneyReport | None:
    """top_owners = holder owner wallets (desc by amount); watchlist = {address: winrate}."""
    if not top_owners:
        return None
    min_wr = cfg.smart_money.watchlist_min_winrate
    smart_in = [w for w in top_owners if w in watchlist and watchlist[w] >= min_wr]
    best = max((watchlist[w] for w in smart_in), default=None)
    # POSITIVE-ONLY signal: presence boosts; absence is None (renormalized out), never a penalty —
    # most legit tokens simply have no tracked smart money yet. Negative holder signals come separately.
    score = min(1.0, 0.6 + 0.1 * len(smart_in)) if smart_in else None
    return SmartMoneyReport(
        mint=mint, smart_wallets_in=smart_in, best_wallet_winrate=best, score=score
    )


def count_prior_creations(txs: list[dict]) -> int:
    n = 0
    for t in txs:
        if str(t.get("type", "")).upper() in _CREATE_TYPES:
            n += 1
        elif "create" in str(t.get("description", "")).lower():
            n += 1
    return n


async def deployer_signal(
    creator: str | None, helius, limit: int = 100
) -> tuple[bool | None, int | None]:
    """(rugged_before, prior_launches). rugged_before is None in v1 (needs outcome labeling)."""
    if not creator or not getattr(helius, "available", False):
        return None, None
    txs = await helius.address_transactions(creator, limit=limit)
    return None, count_prior_creations(txs)
