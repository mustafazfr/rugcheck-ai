"""Shared orchestration — the single source of truth for what `scan`, `recheck`, `manage`, and the
continuous `run` loop actually do. CLI commands and the run loop both call these, so behavior can't drift.

Rendering is decoupled via an `emit(str)` callback (CLI passes console.print; run passes a logger).
Each function returns a small summary the caller can aggregate.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from . import pipeline
from .core.config import Config, Secrets
from .core.credits import CreditGovernor, month_key
from .core.db import Db
from .core.logging import get_logger
from .core.models import TokenCandidate, Verdict
from .data.dexscreener import DexScreenerClient
from .data.geckoterminal import GeckoTerminalClient
from .data.helius import HeliusClient
from .data.jupiter import JupiterClient
from .data.rugcheck import RugCheckClient
from .data.solana_rpc import SolanaRpcClient
from .data.telegram_web import TelegramWebClient
from .data.tweetscout import TweetScoutClient
from .enrich import discovery
from .execution import paper as paper_impact
from .execution.paper import PaperExecutor
from .execution.runner import ApprovalFn, Runner
from .ingest import grace
from .portfolio import manager

WSOL = "So11111111111111111111111111111111111111112"
log = get_logger("solscout.service")
Emit = Callable[[str], None]
_STYLE = {Verdict.BUY: "bold green", Verdict.WATCH: "bold yellow", Verdict.REJECT: "bold red"}


@dataclass
class Services:
    cfg: Config
    secrets: Secrets
    db: Db
    dex: DexScreenerClient
    gecko: GeckoTerminalClient
    rpc: SolanaRpcClient
    helius: HeliusClient
    jup: JupiterClient
    rc: RugCheckClient
    tg: TelegramWebClient
    ts: TweetScoutClient
    runner: Runner
    sol_usd: float | None

    async def analyze(
        self,
        mint: str,
        creator: str | None,
        watchlist: dict[str, float],
        launch_meta: dict | None = None,
    ):
        return await pipeline.analyze(
            mint,
            self.cfg,
            dex=self.dex,
            rpc=self.rpc,
            helius=self.helius,
            tg=self.tg,
            ts=self.ts,
            jup=self.jup,
            gecko=self.gecko,
            rc=self.rc,
            watchlist=watchlist,
            creator=creator,
            launch_meta=launch_meta,
        )


@asynccontextmanager
async def open_services(cfg: Config, secrets: Secrets, approval: ApprovalFn | None = None):
    """Open every client + DB once and bundle them. Dedupes the big `async with` across all commands."""
    # cheap mint-info → configurable/public RPC (free); Helius reserved only for the DAS holder call (ADR-021)
    async with Db(cfg.storage.db_path) as db:
        mk = month_key()
        gov = CreditGovernor(cfg.helius.monthly_budget_funnel, used=await db.get_credit_usage(mk))

        async def _spend(cost: float) -> None:
            await db.add_credit_usage(mk, cost)

        async with (
            DexScreenerClient() as dex,
            GeckoTerminalClient(network=cfg.geckoterminal.network) as gecko,
            SolanaRpcClient(endpoint=cfg.solana_rpc_url or None) as rpc,
            HeliusClient(
                secrets.helius_api_key,
                cache_ttl_s=cfg.helius.cache_ttl_s,
                governor=gov,
                on_spend=_spend,
                cost_per_call=cfg.helius.cost_per_call,
                cost_per_gpa=cfg.helius.cost_per_gpa,
            ) as helius,
            JupiterClient() as jup,
            RugCheckClient() as rc,
            TelegramWebClient() as tg,
            TweetScoutClient(secrets.tweetscout_api_key) as ts,
        ):
            sol_usd = None
            try:
                sol_usd = await dex.get_price_usd(WSOL)
            except Exception:
                pass
            yield Services(
                cfg,
                secrets,
                db,
                dex,
                gecko,
                rpc,
                helius,
                jup,
                rc,
                tg,
                ts,
                Runner(cfg, secrets, db, approval),
                sol_usd,
            )


async def handle_launch(
    s: Services, cand: TokenCandidate, watchlist: dict[str, float], auto_buy: bool, emit: Emit
) -> str:
    """Analyze one candidate. If not indexed/matured yet → queue for grace re-eval. Else act on the verdict.
    Candidates already cleared a free DexScreener-liquidity pre-screen in the stream (ADR-036), so the first
    Helius credit only goes to tokens with a real DEX market."""
    sym = cand.raw_meta.get("symbol", "?")

    a = await s.analyze(cand.mint, cand.creator, watchlist, launch_meta=cand.raw_meta)
    await s.db.save_candidate(cand)

    # NOT READY = not on DexScreener yet (not indexed) or not matured yet.
    # Don't render a premature REJECT — queue for grace re-eval so DexScreener can index + the token matures.
    if not a.ready:
        if s.cfg.ingest.grace.enabled:
            await s.db.enqueue_reeval(
                cand.mint,
                cand.creator,
                grace.next_due_at(1, s.cfg.ingest.grace),
                sym,
                meta=cand.raw_meta,
            )
            emit(
                f"⏳ [dim]PENDING {sym:8} {cand.mint[:8]}… not ready (indexing/maturing) → re-eval[/]"
            )
        else:
            emit(f"⏳ [dim]PENDING {sym:8} {cand.mint[:8]}…[/]")
        return "PENDING"

    await s.db.save_decision(
        a.decision
    )  # only persist genuine verdicts (token had readable on-chain data)
    v = a.decision.verdict
    flags = ("· " + ",".join(a.decision.veto_flags)) if a.decision.veto_flags else ""
    tier = f"[{a.decision.tier}]" if a.decision.tier else ""
    line = (
        f"[{_STYLE[v]}]{v.value:6}[/]{tier} {sym:8} score={a.decision.composite_score:5.1f} "
        f"[dim]{cand.mint[:8]}… {flags}[/]"
    )
    if auto_buy and v == Verdict.BUY:
        out = await s.runner.maybe_buy(a.decision, a.market, s.sol_usd)
        line += (
            f"  [bold green]✓ {out.reason}[/]"
            if out.acted
            else f"  [dim](no fill: {out.reason})[/]"
        )
    emit(line)
    return v.value


async def process_due_reevals(
    s: Services, watchlist: dict[str, float], max_items: int, emit: Emit
) -> int:
    g = s.cfg.ingest.grace
    due = (await s.db.due_reevals())[:max_items]
    for it in due:
        mint, attempts_done = it["mint"], it["attempts"] + 1
        a = await s.analyze(mint, it["creator"], watchlist, launch_meta=it.get("meta"))
        action = grace.reeval_action(
            not a.ready, attempts_done, g
        )  # still not indexed/matured → keep waiting
        sym = it["symbol"] or "?"
        if action == "terminal":
            await s.db.save_decision(
                a.decision
            )  # now has real on-chain data → log the genuine verdict
            v = a.decision.verdict
            tag = f"[{_STYLE[v]}]{v.value}[/] score={a.decision.composite_score:.1f}"
            if v == Verdict.BUY:
                out = await s.runner.maybe_buy(a.decision, a.market, s.sol_usd)
                tag += f"  [green]✓ {out.reason}[/]" if out.acted else f"  [dim]({out.reason})[/]"
            await s.db.drop_reeval(mint)
            emit(f"  {sym:8} {mint[:8]}… now indexed → {tag}")
        elif action == "reschedule":
            await s.db.reschedule_reeval(
                mint, attempts_done, grace.next_due_at(attempts_done + 1, g)
            )
            emit(f"  [dim]{sym:8} {mint[:8]}… still pending ({attempts_done}/{g.max_attempts})[/]")
        else:
            await s.db.drop_reeval(mint)
            emit(f"  [yellow]{sym:8} {mint[:8]}… never indexed → dropped[/]")
    return len(due)


async def run_discovery_cycle(
    cfg: Config, db: Db, gecko: GeckoTerminalClient, helius, emit: Emit
) -> dict:
    """One discovery pass (ADR-036): GeckoTerminal trending winners → Helius holder sets → record
    (wallet, winner) links → promote wallets recurring across DISTINCT winners. Helius is the DISCOVER
    budget (the caller wires its governor); GeckoTerminal is free. Returns a summary for the heartbeat."""
    d = cfg.discovery
    try:
        pools = await gecko.trending_pools()
    except Exception as e:
        log.warning("discovery: trending fetch failed: %s", e)
        pools = []
    # CLEAN winners only (ADR-037): skip wash-traded / one-way pumps so we don't seed bad wallets.
    winners = [
        p
        for p in pools
        if discovery.is_winner(p.liquidity_usd, p.volume_24h, p.age_days(), d, p.buyers_h24, p.sellers_h24)
    ]

    new_winners, holders_scanned = [], 0
    for p in winners:
        if len(new_winners) >= d.winners_per_cycle:
            break
        if await db.already_mined(p.mint):
            continue
        new_winners.append(p)
        owners_res = await helius.token_holders(p.mint)  # ([], False) if over budget / no key
        owners = [o for o, _ in owners_res[0]] if owners_res and owners_res[0] else []
        excludes = {p.pool_address} if p.pool_address else set()
        cands = discovery.candidate_holders(owners, d.top_holders, excludes)
        await db.record_winner_holders(p.mint, cands)
        await db.mark_mined(p.mint)
        holders_scanned += len(cands)
        if cands:
            emit(f"  [dim]winner {(p.name or p.mint[:8]):16} +{len(cands)} trader-holders[/]")

    # Promote wallets recurring across DISTINCT winners. Vetting is now done ONCE per FINALIST (cheap +
    # PnL-aware) instead of per-holder-per-winner: keep only looks-like-a-trader wallets, store realized SOL.
    recurring = await db.recurring_wallets(
        d.min_distinct_winners, d.max_distinct_winners, d.lookback_days
    )
    promoted = 0
    for wallet, count in recurring:
        net_sol = 0.0
        if d.vet_finalists and getattr(helius, "available", False):
            summary = discovery.wallet_swap_summary(await helius.address_transactions(wallet, limit=100) or [])
            if not discovery.looks_like_trader(summary):
                continue  # drop bots/MMs/exchanges before they reach the watchlist
            net_sol = summary["net_sol"]
        await db.add_wallet(wallet, discovery.winrate_from_winners(count), net_sol)
        promoted += 1
    watchlist = len(await db.list_wallets())
    return {
        "winners": len(winners),
        "new": len(new_winners),
        "holders_scanned": holders_scanned,
        "recurring": len(recurring),
        "promoted": promoted,
        "watchlist": watchlist,
    }


async def manage_open_positions(s: Services, emit: Emit) -> int:
    positions = await s.db.get_open_positions()
    exits = 0
    for p in positions:
        market = None
        try:
            market = await s.dex.get_token(p.mint)  # full market: price AND current liquidity (ADR-037)
        except Exception:
            pass
        if not market or not market.price_usd:
            continue
        price = market.price_usd
        liquidity = market.liquidity_usd
        # REALIZABLE price = what we could actually get out, accounting for our size vs pool depth. A fake
        # spike on a thin/rugged pool yields a realizable price near zero → no fake take-profit/trailing.
        value_at_mid = p.token_amount * price
        realizable = price * paper_impact.impact_factor(value_at_mid, liquidity)
        manager.mark(p, realizable)
        age_min = (datetime.now(timezone.utc) - p.opened_at).total_seconds() / 60
        reason = manager.evaluate_exit(p, realizable, age_min, s.cfg.position, liquidity_usd=liquidity)
        if reason:
            fill = await PaperExecutor().sell(
                p, price, s.sol_usd or 0, s.cfg.execution.max_slippage_bps, liquidity_usd=liquidity
            )
            manager.close_position(p, fill)
            await s.db.save_fill(fill)
            await s.db.close_position(p)
            exits += 1
            color = "green" if p.realized_pnl_sol >= 0 else "red"
            emit(
                f"[{color}]EXIT[/] {p.mint[:10]}… {reason} pnl=[{color}]{p.realized_pnl_sol:+.4f} SOL[/]"
            )
    return exits
