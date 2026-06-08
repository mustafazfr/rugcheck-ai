"""wallet_swap_summary parses Helius enhanced txns → rough SOL flow. Pure → unit-tested (ADR-017).
Plus the recurring-holders discovery pure functions (ADR-036)."""

from solscout.core.config import DiscoveryCfg
from solscout.enrich.discovery import (
    INFRA_WALLETS,
    WSOL,
    candidate_holders,
    is_infra_wallet,
    is_winner,
    looks_like_trader,
    winrate_from_winners,
    wallet_swap_summary,
)

DCFG = DiscoveryCfg()


def _summary(**kw) -> dict:
    base = {
        "swaps_total": 5,
        "swaps_measured": 3,
        "sol_out": 1.0,
        "sol_in": 1.5,
        "net_sol": 0.5,
        "distinct_tokens": 4,
    }
    base.update(kw)
    return base


def test_trader_filter_accepts_active_net_positive():
    assert looks_like_trader(_summary())


def test_trader_filter_rejects_net_negative():
    assert not looks_like_trader(_summary(net_sol=-0.2, sol_in=0.8))


def test_trader_filter_rejects_single_token():
    assert not looks_like_trader(_summary(distinct_tokens=1))


def test_trader_filter_rejects_exchange_like_high_volume():
    # transfer-dominated wallet (exchange/bridge): huge swaps_total
    assert not looks_like_trader(_summary(swaps_total=500))


def test_buy_and_sell_flow():
    buy = {
        "type": "SWAP",
        "events": {
            "swap": {"nativeInput": {"amount": "1000000000"}, "tokenOutputs": [{"mint": "TOK"}]}
        },
    }
    sell = {
        "type": "SWAP",
        "events": {
            "swap": {"nativeOutput": {"amount": "1500000000"}, "tokenInputs": [{"mint": "TOK"}]}
        },
    }
    transfer = {"type": "TRANSFER"}
    s = wallet_swap_summary([buy, sell, transfer])
    assert s["swaps_total"] == 2
    assert s["swaps_measured"] == 2
    assert s["sol_out"] == 1.0  # SOL spent buying
    assert s["sol_in"] == 1.5  # SOL received selling
    assert s["net_sol"] == 0.5  # extracted more than spent
    assert s["distinct_tokens"] == 1


def test_swap_without_events_is_unmeasured():
    s = wallet_swap_summary([{"type": "SWAP"}])
    assert s["swaps_total"] == 1 and s["swaps_measured"] == 0
    assert s["net_sol"] == 0.0


def test_wsol_excluded_from_token_count():
    swap = {
        "type": "SWAP",
        "events": {
            "swap": {
                "nativeInput": {"amount": "1000000000"},
                "tokenOutputs": [{"mint": WSOL}, {"mint": "REALTOK"}],
            }
        },
    }
    assert wallet_swap_summary([swap])["distinct_tokens"] == 1


# --- recurring-holders discovery (ADR-036) ---


def test_is_winner_requires_liquidity_volume_and_youth():
    assert is_winner(25_000, 60_000, 3, DCFG)  # liquid, traded, young → yes
    assert not is_winner(5_000, 60_000, 3, DCFG)  # too illiquid
    assert not is_winner(25_000, 1_000, 3, DCFG)  # ~no volume (dead)
    assert not is_winner(25_000, 60_000, 99, DCFG)  # too old (early holders no longer sharp)
    assert is_winner(25_000, 60_000, None, DCFG)  # unknown age → don't reject on it


def test_is_winner_rejects_manipulated():
    # wash-traded: volume 66× the pool → not a real winner (don't mine its holders)
    assert not is_winner(30_000, 2_000_000, 3, DCFG)
    # one-way flow: ~no sellers → manipulated
    assert not is_winner(30_000, 100_000, 3, DCFG, buyers=1000, sellers=10)
    # healthy two-way flow passes
    assert is_winner(30_000, 100_000, 3, DCFG, buyers=500, sellers=400)


def test_is_infra_wallet_drops_known_programs_and_pool():
    raydium_auth = next(iter(INFRA_WALLETS))
    assert is_infra_wallet(raydium_auth)
    assert is_infra_wallet("POOLVAULT", {"POOLVAULT"})  # the token's own pool vault
    assert not is_infra_wallet("SomeRandomTraderWallet")
    assert is_infra_wallet("")  # empty → treat as infra (skip)


def test_candidate_holders_filters_and_caps():
    raydium_auth = next(iter(INFRA_WALLETS))
    owners = [raydium_auth, "TRADER_A", "POOL", "TRADER_B", "TRADER_C"]
    out = candidate_holders(owners, top_holders=4, pool_excludes={"POOL"})
    # within the first 4 (raydium_auth, TRADER_A, POOL, TRADER_B): infra + pool dropped
    assert out == ["TRADER_A", "TRADER_B"]


def test_winrate_grows_with_distinct_winners_and_caps():
    assert winrate_from_winners(1) == 0.60  # below the promote bar anyway
    assert winrate_from_winners(2) == 0.66
    assert winrate_from_winners(3) == 0.72
    assert winrate_from_winners(10) == 0.80  # capped
