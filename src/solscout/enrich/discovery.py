"""Smart-money discovery (ADR-036) — FREE, on-chain-correct, no paid PnL API.

Thesis: random wallets do NOT repeat across *unrelated* winning tokens. A wallet that keeps showing up
in the holder sets of many DISTINCT recent winners is accumulating winners early = smart money. We
reconstruct the signal Cielo/GMGN sell, using only GeckoTerminal (free trending winners) + Helius holder
sets (governed). Backward-looking (fills the watchlist in minutes) AND compounds forward.

This module is PURE (no I/O) so it is unit-tested; the streaming orchestration lives in `cli discover`.
The `wallet_swap_summary` / `looks_like_trader` helpers below are the optional per-wallet vetting aid
(also used by the `wallet-check` CLI) — a rough realized-flow read, NOT a profitability guarantee (ADR-017).
"""

from __future__ import annotations

WSOL = "So11111111111111111111111111111111111111112"

# Infra/LP/program owners that show up in holder sets but are NOT traders. Excluded from discovery AND
# from concentration math (so an LP vault isn't mistaken for a whale). NON-pump infra only (pump.fun is gone).
INFRA_WALLETS: frozenset[str] = frozenset(
    {
        "11111111111111111111111111111111",  # System Program
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token program
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022 program
        "1nc1nerator11111111111111111111111111111111",  # burn / incinerator
        "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j",  # Raydium Authority V4
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium Liquidity Pool V4
        "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # Raydium CLMM
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",  # Orca Whirlpools
        "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP",  # Orca v1 pool authority
        "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB",  # Meteora pools
        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",  # Meteora DLMM
        "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",  # Jupiter Aggregator v6
        "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX",  # OpenBook / Serum
        "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9",  # Binance hot wallet
        "9un5wqE3q4oCjyrDkwsdD48KteCJitQX5978Vh7KKxHo",  # Binance hot wallet 2
        "2ojv9BAiHUrvsm9gxDe7fJSzbNZSJcxZvf8dqmWGHG8S",  # Coinbase hot wallet
        "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS",  # Coinbase hot wallet 2
        "GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7npE",  # Coinbase 3
    }
)

# A wallet's winrate proxy grows with how many DISTINCT winners it held early. Conservative + capped —
# this is a heuristic, not a measured PnL. Tune in tests/config, not magic-numbered in logic elsewhere.
_WINRATE = {2: 0.66, 3: 0.72, 4: 0.76}


def winrate_from_winners(distinct_winners: int) -> float:
    """Winrate proxy from the number of DISTINCT winners a wallet recurs across (capped at 0.80)."""
    if distinct_winners >= 5:
        return 0.80
    return _WINRATE.get(distinct_winners, 0.60)


def is_infra_wallet(owner: str, extra: set[str] | frozenset[str] | None = None) -> bool:
    """True if this owner is a known program / AMM / LP / exchange wallet (not a trader). `extra` lets the
    caller add the specific pool/pair address of the token being mined."""
    if not owner:
        return True
    return owner in INFRA_WALLETS or (extra is not None and owner in extra)


def is_winner(
    liquidity_usd: float | None,
    volume_24h: float | None,
    age_days: float | None,
    cfg,
    buyers: int | None = None,
    sellers: int | None = None,
) -> bool:
    """A pool worth mining for smart money: real liquidity + real recent volume + young enough that its
    early holders were sharp — AND not manipulated (ADR-037), so we don't seed wash-traders into the
    watchlist. `cfg` = DiscoveryCfg."""
    if not liquidity_usd or liquidity_usd < cfg.winner_min_liquidity_usd:
        return False
    if not volume_24h or volume_24h < cfg.winner_min_volume_24h_usd:
        return False
    if age_days is not None and age_days > cfg.winner_max_age_days:
        return False
    # wash / one-way (manipulated) pumps are NOT real winners — their holders aren't smart money
    if volume_24h / liquidity_usd > cfg.winner_max_vol_liq_ratio:
        return False
    if buyers is not None and sellers is not None and (buyers + sellers) > 0:
        if sellers / (buyers + sellers) < cfg.winner_min_seller_share:
            return False
    return True


def candidate_holders(
    owners: list[str], top_holders: int, pool_excludes: set[str] | frozenset[str] | None = None
) -> list[str]:
    """From a winner's owner list (desc by amount), keep the top-N trader-like wallets: drop infra/LP/CEX
    and the pool's own vault. Pure → unit-tested."""
    out: list[str] = []
    for owner in owners[:top_holders]:
        if not is_infra_wallet(owner, pool_excludes):
            out.append(owner)
    return out


# --------------------------------------------------------------------------------------------------
# Optional per-wallet vetting aid (rough realized SOL-flow from CLEAN swap legs only). ADR-017.
# Used by `wallet-check` and by discovery's optional `vet_finalists`. NOT a profitability guarantee.
# --------------------------------------------------------------------------------------------------
def wallet_swap_summary(txs: list[dict], wallet: str | None = None) -> dict:
    """Rough trading summary from Helius enhanced txns. SOL amounts come ONLY from events.swap native
    legs (clean), so noisy/rent transfers are excluded. net_sol>0 ≈ extracted more SOL than spent."""
    sol_out = 0  # lamports put into swaps (buying)
    sol_in = 0  # lamports taken out of swaps (selling)
    measured = 0
    swaps_total = 0
    mints: set[str] = set()

    for t in txs:
        if t.get("type") != "SWAP":
            continue
        swaps_total += 1
        sw = (t.get("events") or {}).get("swap") or {}
        ain = int((sw.get("nativeInput") or {}).get("amount") or 0)
        aout = int((sw.get("nativeOutput") or {}).get("amount") or 0)
        if ain or aout:
            measured += 1
            sol_out += ain
            sol_in += aout
        for grp in ("tokenInputs", "tokenOutputs"):
            for ti in sw.get(grp) or []:
                m = ti.get("mint")
                if m and m != WSOL:
                    mints.add(m)

    return {
        "swaps_total": swaps_total,
        "swaps_measured": measured,
        "sol_out": round(sol_out / 1e9, 4),
        "sol_in": round(sol_in / 1e9, 4),
        "net_sol": round((sol_in - sol_out) / 1e9, 4),
        "distinct_tokens": len(mints),
    }


def looks_like_trader(summary: dict) -> bool:
    """Heuristic: keep active, multi-token, net-positive swappers; drop one-offs and likely
    exchanges/MMs/LPs (transfer-heavy with few clean swaps, or a single pair). Rough seeds only."""
    return (
        summary["swaps_measured"] >= 2
        and summary["net_sol"] > 0
        and 2 <= summary["distinct_tokens"] <= 30
        and summary["swaps_total"] <= 60
    )
