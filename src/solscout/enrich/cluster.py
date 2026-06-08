"""Bundle / sybil-cluster detection (ADR-038) — the Bubblemaps signal, reconstructed FREE from on-chain
funding sources. Pure logic here (unit-tested); the Helius fetching + caching lives in the pipeline.

Idea: holder CONCENTRATION can't see a rug where the supply is split across many wallets that are secretly
ONE entity (a "bundle" of sybils the dev funded). But those sybils were all funded by the same wallet — so
if many of a token's top holders share a common SOL funder, that's coordinated/insider distribution, not
real holders. This is exactly what a Bubblemaps cluster shows.
"""

from __future__ import annotations

from .discovery import is_infra_wallet


def dominant_funder(txs: list[dict], wallet: str) -> str | None:
    """The non-infra wallet that sent `wallet` the most SOL (its funder), from Helius enhanced txns.
    None if no clean funding source is visible. Pure."""
    tally: dict[str, int] = {}
    for t in txs:
        for nt in t.get("nativeTransfers") or []:
            if nt.get("toUserAccount") != wallet:
                continue
            src = nt.get("fromUserAccount")
            amt = int(nt.get("amount") or 0)
            if src and src != wallet and amt > 0 and not is_infra_wallet(src):
                tally[src] = tally.get(src, 0) + amt
    if not tally:
        return None
    return max(tally.items(), key=lambda x: x[1])[0]


def detect_bundle(funder_by_holder: dict[str, str | None], cfg) -> tuple[list[str], int, str | None]:
    """(flags, biggest_cluster_size, funder). A 'bundle' = the largest group of traced holders sharing ONE
    funder is ≥ min_cluster_size, OR (with ≥3 traced) that funder backs ≥ max_funder_share of them. Pure."""
    counts: dict[str, int] = {}
    for f in funder_by_holder.values():
        if f:
            counts[f] = counts.get(f, 0) + 1
    if not counts:
        return [], 0, None
    funder, biggest = max(counts.items(), key=lambda x: x[1])
    traced = sum(1 for f in funder_by_holder.values() if f)
    share = biggest / traced if traced else 0.0
    if biggest >= cfg.min_cluster_size or (biggest >= 3 and share >= cfg.max_funder_share):
        return ["bundled_holders"], biggest, funder
    return [], biggest, funder
