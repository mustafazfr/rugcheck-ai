"""SolScout CLI (Typer).

`report <MINT>` is the read-only analysis path — wired end-to-end and always safe (no trading).
`scan`/`watch`/`backtest` land as their stages are implemented.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable, TypeVar

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .core.config import load
from .core.db import Db
from .core.logging import get_logger
from .core.models import (
    Decision,
    FilterResult,
    LlmSynthesis,
    MintInfo,
    SmartMoneyReport,
    SocialReport,
    TokenCandidate,
    TokenMarket,
    Verdict,
)
from . import analytics, dashboard as dashboard_mod, pipeline, service
from .core import credits
from .data.dexscreener import DexScreenerClient
from .data.geckoterminal import GeckoTerminalClient
from .data.helius import HeliusClient
from .data.jupiter import JupiterClient
from .data.rugcheck import RugCheckClient
from .data.solana_rpc import SolanaRpcClient
from .data.telegram_web import TelegramWebClient
from .data.tweetscout import TweetScoutClient
from .enrich import cluster, discovery
from .execution.base import PortfolioSnapshot, RiskGate
from .execution.paper import PaperExecutor
from .ingest.stream import stream_candidates
from .portfolio import manager

WSOL = service.WSOL

app = typer.Typer(
    add_completion=False, help="SolScout — Solana meme-coin intelligence & paper-first trading bot."
)
console = Console()
log = get_logger("solscout")

T = TypeVar("T")
_VERDICT_STYLE = {
    Verdict.BUY: "bold green",
    Verdict.WATCH: "bold yellow",
    Verdict.REJECT: "bold red",
}


async def _safe(coro: Awaitable[T], label: str) -> T | None:
    try:
        return await coro
    except Exception as e:
        log.warning("%s fetch failed: %s", label, e)
        return None


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Launch rugcheck.ai — the web app (FastAPI + forensic frontend over the free analysis engine)."""
    import uvicorn

    console.print(
        f"[bold]rugcheck[/][bold green].ai[/] → [link]http://{host}:{port}[/]  [dim](Ctrl-C to stop)[/]"
    )
    uvicorn.run("solscout.web.api:app", host=host, port=port, reload=reload, log_level="info")


@app.command()
def report(mint: str) -> None:
    """Full read-only analysis report for one token mint. Always safe (no trading)."""
    asyncio.run(_report(mint))


async def _report(mint: str) -> None:
    cfg, secrets = load()
    async with (
        Db(cfg.storage.db_path) as db,
        DexScreenerClient() as dex,
        GeckoTerminalClient(network=cfg.geckoterminal.network) as gecko,
        SolanaRpcClient(endpoint=cfg.solana_rpc_url or None) as rpc,
        HeliusClient(secrets.helius_api_key, cache_ttl_s=cfg.helius.cache_ttl_s) as helius,
        JupiterClient() as jup,
        RugCheckClient() as rc,
        TelegramWebClient() as tg,
        TweetScoutClient(secrets.tweetscout_api_key) as ts,
    ):
        watchlist = await db.list_wallets()
        a = await pipeline.analyze(
            mint, cfg, dex=dex, rpc=rpc, helius=helius, tg=tg, ts=ts, jup=jup, gecko=gecko, rc=rc,
            watchlist=watchlist,
        )
        await db.save_candidate(TokenCandidate(mint=mint, raw_meta={"via": "report"}))
        await db.save_decision(a.decision)

    _render(mint, a.market, a.mint_info, a.filt, a.smart, a.social, a.llm, a.decision)


def _render(
    mint,
    market: TokenMarket | None,
    mi: MintInfo | None,
    filt: FilterResult,
    smart: SmartMoneyReport | None,
    social: SocialReport | None,
    llm: LlmSynthesis | None,
    d: Decision,
) -> None:
    title = f"{market.name} ({market.symbol})" if market and market.name else mint
    console.print(Panel.fit(f"[bold]{title}[/bold]\n[dim]{mint}[/dim]", title="SolScout report"))

    mk = Table(title="Market (DexScreener)", show_header=False, expand=False)
    if market:
        mk.add_row("price", _usd(market.price_usd))
        mk.add_row("liquidity", _usd(market.liquidity_usd))
        mk.add_row("market cap", _usd(market.market_cap))
        mk.add_row("24h volume", _usd(market.volume_24h))
        mk.add_row("dex", str(market.dex))
        mk.add_row("socials", ", ".join(f"{s.type}" for s in market.socials) or "—")
    else:
        mk.add_row("status", "[yellow]no DEX pair (brand-new or unlisted)[/yellow]")
    console.print(mk)

    oc = Table(title="On-chain (SPL mint)", show_header=False)
    if mi:
        oc.add_row("mint authority", _authority(mi.mint_authority))
        oc.add_row("freeze authority", _authority(mi.freeze_authority))
        oc.add_row(
            "supply", f"{mi.supply / (10**mi.decimals):,.0f}" if mi.decimals else str(mi.supply)
        )
        oc.add_row("top-10 holders", f"{mi.top10_pct}%" if mi.top10_pct is not None else "—")
    else:
        oc.add_row("status", "[red]not a valid SPL mint / unreadable[/red]")
    console.print(oc)

    fl = Table(title="Stage 1 — rug filters", show_header=False)
    fl.add_row("passed", "[green]yes[/green]" if filt.passed else "[red]no[/red]")
    fl.add_row("hard flags", ", ".join(filt.hard_flags) or "—")
    fl.add_row("safety score", f"{filt.safety_score:.2f}")
    rc_score = filt.metrics.get("rugcheck_score")
    if rc_score is not None:
        rc_color = "green" if rc_score < 35 else ("yellow" if rc_score < 60 else "red")
        fl.add_row("RugCheck risk", f"[{rc_color}]{rc_score}/100[/] (lower=safer)")
    console.print(fl)

    if smart:
        sm = Table(title="Stage 3 — smart-money", show_header=False)
        sm.add_row(
            "watchlist wallets in", ", ".join(w[:6] + "…" for w in smart.smart_wallets_in) or "none"
        )
        sm.add_row(
            "best winrate", f"{smart.best_wallet_winrate:.0%}" if smart.best_wallet_winrate else "—"
        )
        sm.add_row("score", f"{smart.score:.2f}" if smart.score is not None else "—")
        console.print(sm)

    if social:
        so = Table(title="Stage 2 — social", show_header=False)
        so.add_row("twitter", social.twitter_handle or "—")
        so.add_row(
            "twitter age (days)",
            str(social.twitter_age_days) if social.twitter_age_days is not None else "—",
        )
        so.add_row(
            "telegram chatter", f"{len(social.chatter_texts)} msgs" if social.chatter_texts else "—"
        )
        so.add_row("flags", ", ".join(social.flags) or "—")
        so.add_row(
            "social score", f"{social.social_score:.2f}" if social.social_score is not None else "—"
        )
        console.print(so)

    if llm:
        body = llm.summary or "(no summary)"
        body += f"\n\n[dim]narrative={llm.narrative_strength:.2f} · community={llm.community_authenticity:.2f}[/]"
        if llm.scam_language_flags:
            body += "\n[red]scam-language: " + ", ".join(llm.scam_language_flags) + "[/]"
        console.print(Panel(body, title="LLM read (Qwen)"))

    style = _VERDICT_STYLE[d.verdict]
    dec = Table(title="Decision (deterministic)", show_header=False)
    dec.add_row("verdict", f"[{style}]{d.verdict.value}[/{style}]")
    dec.add_row("score", f"{d.composite_score:.1f} / 100")
    if d.veto_flags:
        dec.add_row("veto", "[red]" + ", ".join(d.veto_flags) + "[/red]")
    for r in d.reasons:
        dec.add_row("·", r)
    console.print(dec)

    if d.verdict == Verdict.BUY:
        tier_note = (
            "a proven watchlist wallet is in → SMART tier, full size"
            if d.tier == "smart"
            else "no smart money yet → QUALITY tier, reduced size"
        )
        console.print(
            f"[dim]BUY tier: [b]{d.tier}[/] ({tier_note}). PAPER unless live flags set.[/dim]"
        )
    console.print(
        "[dim]Read-only analysis · not financial advice. Smart-money fills the watchlist automatically via "
        "`solscout discover` (free, on-chain). Quality-tier BUYs don't need it — they're measured on paper.[/dim]"
    )


def _usd(v) -> str:
    return f"${v:,.6f}" if v is not None and v < 1 else (f"${v:,.0f}" if v is not None else "—")


def _authority(a) -> str:
    return "[green]renounced ✓[/green]" if not a else f"[red]ACTIVE ✗[/red] {a[:8]}…"


@app.command("paper-buy")
def paper_buy(mint: str, sol: float = 0.1) -> None:
    """Force a PAPER buy at live price — exercises RiskGate + executor + position mgmt. No real funds."""
    asyncio.run(_paper_buy(mint, sol))


async def _paper_buy(mint: str, sol_amount: float) -> None:
    cfg, secrets = load()
    async with DexScreenerClient() as dex:
        token_price, sol_usd = await asyncio.gather(
            _safe(dex.get_price_usd(mint), "token-price"),
            _safe(dex.get_price_usd(WSOL), "sol-price"),
        )
    if not token_price or not sol_usd:
        console.print("[red]Could not fetch live prices (token or SOL).[/red]")
        return

    gate = RiskGate(cfg, secrets)
    rd = gate.pre_trade_check(sol_amount, PortfolioSnapshot())
    if not rd.allowed:
        console.print("[red]Blocked by RiskGate:[/red] " + "; ".join(rd.reasons))
        return

    fill = await PaperExecutor().buy(
        mint, sol_amount, token_price, sol_usd, cfg.execution.max_slippage_bps
    )
    pos = manager.open_position(fill, fill.price_usd)
    async with Db(cfg.storage.db_path) as db:
        await db.save_fill(fill)
        await db.open_position(pos)

    t = Table(title=f"PAPER buy · {mint[:8]}…", show_header=False)
    t.add_row("mode", f"[green]{cfg.execution.mode.value}[/green] (no real funds)")
    t.add_row("SOL spent", f"{fill.sol_amount}")
    t.add_row("SOL/USD", _usd(sol_usd))
    t.add_row("fill price", _usd(fill.price_usd))
    t.add_row("tokens", f"{fill.token_amount:,.2f}")
    t.add_row("position value", _usd(pos.token_amount * pos.avg_price_usd))
    console.print(t)
    console.print(
        "[dim]Position opened & fill persisted. Exit rules: see config.position (SL/TP/trailing/time).[/dim]"
    )


@app.command("watchlist-add")
def watchlist_add(address: str, winrate: float = 0.7) -> None:
    """Add a proven-PnL wallet to the smart-money watchlist (feeds Stage 3 / the positive gate)."""
    asyncio.run(_wl_add(address, winrate))


async def _wl_add(address: str, winrate: float) -> None:
    cfg, _ = load()
    async with Db(cfg.storage.db_path) as db:
        await db.add_wallet(address, winrate)
    console.print(f"[green]watchlist +[/] {address} (winrate {winrate:.0%})")


@app.command("watchlist-list")
def watchlist_list() -> None:
    """Show the smart-money watchlist."""
    asyncio.run(_wl_list())


async def _wl_list() -> None:
    cfg, _ = load()
    async with Db(cfg.storage.db_path) as db:
        wl = await db.list_wallets()
    if not wl:
        console.print("[dim]watchlist empty — add proven wallets with `watchlist-add <ADDR>`[/]")
        return
    t = Table(title="Smart-money watchlist")
    t.add_column("wallet")
    t.add_column("winrate", justify="right")
    for a, w in wl.items():
        t.add_row(a, f"{w:.0%}")
    console.print(t)


@app.command()
def holders(mint: str, n: int = 10) -> None:
    """Top holder owner wallets for a mint (via Helius DAS). Needs HELIUS_API_KEY."""
    asyncio.run(_holders(mint, n))


async def _holders(mint: str, n: int) -> None:
    cfg, secrets = load()
    async with HeliusClient(secrets.helius_api_key) as helius:
        if not helius.available:
            console.print("[red]needs HELIUS_API_KEY in .env[/]")
            return
        owners, complete = await helius.token_holders(mint)
    if not owners:
        console.print("[yellow]no holders resolved (token too new or not indexed yet)[/]")
        return
    t = Table(
        title=f"Top holders · {mint[:8]}… {'(complete set)' if complete else '(partial page)'}"
    )
    t.add_column("owner")
    t.add_column("amount", justify="right")
    for o, amt in owners[:n]:
        t.add_row(o, f"{amt:,}")
    console.print(t)


@app.command()
def bubble(mint: str, top: int = 12) -> None:
    """Holder CLUSTER / bundle map for a mint — top holders + their SOL funders + a bundle verdict (the
    Bubblemaps signal, free via Helius). A bundle = many top holders funded by ONE wallet = sybil/insider
    distribution that holder-concentration can't see. Prints the official Bubblemaps + Solscan links too."""
    asyncio.run(_bubble(mint, top))


async def _bubble(mint: str, top: int) -> None:
    cfg, secrets = load()
    async with HeliusClient(secrets.helius_api_key, cache_ttl_s=cfg.helius.cache_ttl_s) as helius:
        if not helius.available:
            console.print("[red]needs HELIUS_API_KEY in .env[/]")
            return
        owners, complete = await helius.token_holders(mint)
        holders = [(o, a) for o, a in owners if not discovery.is_infra_wallet(o)]
        if not holders:
            console.print("[yellow]no holders resolved (token too new / not indexed)[/]")
            return
        total = sum(a for _, a in holders) or 1
        funder_by: dict[str, str | None] = {}
        for o, _a in holders[:top]:
            txs = await _safe(helius.address_transactions(o, limit=cfg.cluster.tx_limit), "txns") or []
            funder_by[o] = cluster.dominant_funder(txs, o)
    flags, size, funder = cluster.detect_bundle(funder_by, cfg.cluster)

    t = Table(title=f"Holder clusters · {mint[:8]}… ({'full' if complete else 'partial'} set · "
                    f"{len(holders)} non-infra holders)")
    for col in ("#", "holder", "% supply", "funded by"):
        t.add_column(col)
    for i, (o, a) in enumerate(holders[:top], 1):
        f = funder_by.get(o)
        same = f and f == funder
        fl = f"[red]{f[:8]}…[/]" if same else (f"{f[:8]}…" if f else "—")
        t.add_row(str(i), o[:8] + "…", f"{100 * a / total:.1f}%", fl)
    console.print(t)
    if flags:
        console.print(f"[bold red]⚠ BUNDLE detected:[/] {size} of the top {top} holders share one funder "
                      f"([red]{(funder or '?')[:8]}…[/]) → coordinated/sybil distribution. The funnel would "
                      f"[bold red]REJECT[/] this.")
    else:
        console.print(f"[green]✓ no bundle[/] — largest shared-funder cluster among the top {top}: {size}")
    console.print(f"[dim]Bubblemaps: https://app.bubblemaps.io/sol/token/{mint}\n"
                  f"Solscan holders: https://solscan.io/token/{mint}#holders[/]")


@app.command("wallet-check")
def wallet_check(wallet: str, limit: int = 100) -> None:
    """Rough recent-trading summary for a wallet — a manual vetting aid before `watchlist-add` (ADR-017)."""
    asyncio.run(_wallet_check(wallet, limit))


async def _wallet_check(wallet: str, limit: int) -> None:
    cfg, secrets = load()
    async with HeliusClient(secrets.helius_api_key) as helius:
        if not helius.available:
            console.print("[red]needs HELIUS_API_KEY in .env[/]")
            return
        txs = await _safe(helius.address_transactions(wallet, limit=limit), "helius-txns") or []
    s = discovery.wallet_swap_summary(txs, wallet)
    t = Table(title=f"wallet activity · {wallet[:8]}… (rough, last {limit} txs)", show_header=False)
    t.add_row("swaps (total / measured)", f"{s['swaps_total']} / {s['swaps_measured']}")
    t.add_row("SOL out (buys)", f"{s['sol_out']:.3f}")
    t.add_row("SOL in (sells)", f"{s['sol_in']:.3f}")
    t.add_row("net SOL (rough)", f"{s['net_sol']:+.3f}")
    t.add_row("distinct tokens", str(s["distinct_tokens"]))
    console.print(t)
    console.print(
        "[dim]Rough realized-flow from measurable swaps only; ignores current holdings. A VETTING "
        "aid for `watchlist-add` — not an automated PnL (ADR-017).[/dim]"
    )


@app.command()
def discover(duration: int = 0) -> None:
    """Autonomously fill the watchlist for FREE — no input, no funded key (ADR-036). Pulls GeckoTerminal's
    trending Solana winners, fetches each one's holder set (Helius), and learns which wallets RECUR across
    many DISTINCT unrelated winners = smart money. Backward-looking (fills in minutes) AND compounds.
    Great alongside `make up`. duration=0 = forever (Ctrl-C)."""
    try:
        asyncio.run(_discover(duration))
    except KeyboardInterrupt:
        console.print("\n[yellow]discover stopped[/]")


async def _discover(duration: int) -> None:
    cfg, secrets = load()
    d = cfg.discovery
    console.print(
        f"[bold cyan]Smart-money discovery[/] (free · no key) · trending winners every {d.cycle_s}s · "
        f"promote at ≥{d.min_distinct_winners} distinct winners · "
        f"{'∞' if duration == 0 else str(duration) + 's'} · [dim]Ctrl-C to stop[/]"
    )
    deadline = time.time() + duration if duration else None
    mk = credits.month_key()
    async with (
        Db(cfg.storage.db_path) as db,
        GeckoTerminalClient(network=cfg.geckoterminal.network) as gecko,
    ):
        gov = credits.CreditGovernor(
            cfg.helius.monthly_budget_discover, used=await db.get_credit_usage(mk)
        )

        async def _spend(cost: float) -> None:
            await db.add_credit_usage(mk, cost)

        async with HeliusClient(
            secrets.helius_api_key,
            cache_ttl_s=cfg.helius.cache_ttl_s,
            governor=gov,
            on_spend=_spend,
            cost_per_call=cfg.helius.cost_per_call,
            cost_per_gpa=cfg.helius.cost_per_gpa,
        ) as helius:
            cycles = 0
            while not deadline or time.time() < deadline:
                summary = await service.run_discovery_cycle(cfg, db, gecko, helius, console.print)
                cycles += 1
                used = await db.get_credit_usage(mk)
                console.print(
                    f"[dim]DISCOVER #{cycles} · winners={summary['winners']} (new {summary['new']}) · "
                    f"holders+{summary['holders_scanned']} · recurring={summary['recurring']} · "
                    f"promoted={summary.get('promoted', summary['recurring'])} → "
                    f"[b]watchlist={summary['watchlist']}[/b] · credits {used:,.0f}/{cfg.helius.monthly_budget_discover:,.0f}[/]"
                )
                if deadline and time.time() + d.cycle_s > deadline:
                    break
                await asyncio.sleep(d.cycle_s)
    console.print(
        "[bold]discover finished[/] — leave it running (or via `make up`) and the watchlist keeps "
        "filling, hands-free."
    )


async def _vet_holders(helius, mint: str, top: int):
    """Pull a token's holders, keep the trader-like ones (drops exchanges/LPs/inactive). Free + on-chain."""
    owners, _complete = await helius.token_holders(mint)
    out = []
    for owner, _amt in owners[:top]:
        txs = await _safe(helius.address_transactions(owner, limit=100), "txns") or []
        s = discovery.wallet_swap_summary(txs)
        if discovery.looks_like_trader(s):
            out.append((owner, s))
    out.sort(key=lambda x: -x[1]["net_sol"])
    return out


@app.command("find-wallets")
def find_wallets(mint: str, top: int = 25, add: bool = False, winrate: float = 0.65) -> None:
    """Explore candidate smart-money wallets from a SEED token's holders (vet via wallet-check, drop
    exchanges/inactive). Use a RECENT WINNER as the seed — big established coins yield mostly exchanges.
    --add seeds the survivors into the watchlist (rough/unverified — see ADR-017)."""
    asyncio.run(_find_wallets(mint, top, add, winrate))


async def _find_wallets(mint: str, top: int, add: bool, winrate: float) -> None:
    cfg, secrets = load()
    async with HeliusClient(secrets.helius_api_key) as helius:
        if not helius.available:
            console.print("[red]needs HELIUS_API_KEY[/]")
            return
        console.print(
            f"[cyan]Vetting top {top} holders of {mint[:8]}…[/] [dim](calls Helius per wallet)[/]"
        )
        candidates = await _vet_holders(helius, mint, top)

    if not candidates:
        console.print(
            "[yellow]No trader-like wallets found among these holders.[/] "
            "[dim]Big/established coins are mostly held by exchanges & LPs — try a RECENT winner "
            "as the seed, or paste wallets from GMGN/Cielo.[/]"
        )
        return

    t = Table(title=f"Candidate wallets from {mint[:8]}… (rough, unverified)")
    for col in ("wallet", "net SOL", "swaps", "tokens"):
        t.add_column(col)
    for w, s in candidates:
        t.add_row(
            w, f"[green]{s['net_sol']:+.3f}[/]", str(s["swaps_measured"]), str(s["distinct_tokens"])
        )
    console.print(t)

    if add:
        async with Db(cfg.storage.db_path) as db:
            for w, s in candidates:
                await db.add_wallet(w, winrate, s["net_sol"])
        console.print(
            f"[green]added {len(candidates)} wallet(s) to the watchlist[/] "
            f"[dim](winrate placeholder {winrate}; real performance will tell over paper runs)[/]"
        )
    else:
        console.print("[dim]re-run with --add to seed these into the watchlist.[/]")


@app.command()
def scan(limit: int = 5, auto_buy: bool = True) -> None:
    """Autonomously stream live DEX candidates through the FULL funnel + act on BUYs (paper by default)."""
    asyncio.run(_scan(limit, auto_buy))


async def _semi_auto_approve(decision: Decision) -> bool:
    """Console approval for semi_auto mode. Default = decline (fail-safe)."""
    ans = console.input(
        f"[bold yellow]Approve BUY[/] {decision.mint[:10]}… "
        f"score={decision.composite_score:.1f}, size={decision.position_size_sol} SOL? [y/N] "
    )
    return ans.strip().lower() in ("y", "yes")


async def _scan(limit: int, auto_buy: bool) -> None:
    cfg, secrets = load()
    console.print(
        f"[cyan]Autonomous scan[/] · next {limit} DEX candidates → full funnel · "
        f"mode=[bold]{cfg.execution.mode.value}[/] · auto_buy={auto_buy}"
    )
    counts = {"BUY": 0, "WATCH": 0, "REJECT": 0, "PENDING": 0}
    async with service.open_services(cfg, secrets, approval=_semi_auto_approve) as s:
        watchlist = await s.db.list_wallets()
        async for cand in stream_candidates(
            s.gecko,
            s.dex,
            poll_s=cfg.geckoterminal.poll_s,
            min_liquidity_usd=cfg.ingest.min_liquidity_to_analyze,
            new_pools_pages=cfg.geckoterminal.new_pools_pages,
            limit=limit,
        ):
            cat = await service.handle_launch(s, cand, watchlist, auto_buy, console.print)
            counts[cat] = counts.get(cat, 0) + 1
        qn = await s.db.queue_size()
    console.print("[bold]scan done[/] · " + " · ".join(f"{k}={v}" for k, v in counts.items()))
    console.print(
        "[dim]New listings can be PENDING (not indexed/matured yet). They're queued for grace-period "
        f"re-eval — run [b]solscout recheck[/b] later (queue size: {qn}). Buys are PAPER unless live.[/dim]"
    )


@app.command()
def run(
    duration: int = 0, recheck_every: int = 60, manage_every: int = 90, auto_buy: bool = True
) -> None:
    """Continuous unattended loop (paper): stream launches + periodic recheck + position management.
    duration=0 runs forever (Ctrl-C to stop)."""
    try:
        asyncio.run(_run(duration, recheck_every, manage_every, auto_buy))
    except KeyboardInterrupt:
        console.print("\n[yellow]stopped[/]")


async def _run(duration: int, recheck_every: int, manage_every: int, auto_buy: bool) -> None:
    cfg, secrets = load()
    console.print(
        f"[bold cyan]SolScout run[/] · mode=[bold]{cfg.execution.mode.value}[/] · auto_buy={auto_buy} "
        f"· recheck/{recheck_every}s · manage/{manage_every}s · "
        f"{'∞' if duration == 0 else str(duration) + 's'} · [dim]Ctrl-C to stop[/]"
    )
    stop = asyncio.Event()

    console.print(
        f"[dim]· ingest source: [b]GeckoTerminal new_pools + DexScreener promoted[/] "
        f"(free/keyless, liq≥${cfg.ingest.min_liquidity_to_analyze:,.0f}) · "
        f"discovery runs separately (`solscout discover`)[/]"
    )

    async def ingest_loop(s):
        # The stream polls (no WS to drop) but we still guard with reconnect + a per-minute analysis cap
        # that bounds Helius credit spend (ADR-020): only this many tokens get the full funnel per minute.
        cap = s.cfg.ingest.max_analyses_per_min
        window_start = time.monotonic()
        analyzed = 0
        while not stop.is_set():
            try:
                async for cand in stream_candidates(
                    s.gecko,
                    s.dex,
                    poll_s=cfg.geckoterminal.poll_s,
                    min_liquidity_usd=cfg.ingest.min_liquidity_to_analyze,
                    new_pools_pages=cfg.geckoterminal.new_pools_pages,
                ):
                    if stop.is_set():
                        break
                    now = time.monotonic()
                    if now - window_start >= 60:
                        window_start, analyzed = now, 0
                    if analyzed >= cap:
                        continue  # over the per-minute budget — skip (don't spend Helius on it)
                    try:
                        wl = await s.db.list_wallets()
                        cat = await service.handle_launch(s, cand, wl, auto_buy, console.print)
                        if cat not in ("SKIP", "PENDING"):  # only count real Helius-funnel work
                            analyzed += 1
                    except Exception as e:
                        log.warning("ingest: %s", e)
            except Exception as e:
                log.warning("ingest stream dropped (%s) — reconnecting in 5s", e)
            if not stop.is_set():
                console.print("[dim]· ingest stream reconnecting…[/]")
                await asyncio.sleep(5)

    async def periodic(s, fn, every: int, label: str):
        while not stop.is_set():
            await asyncio.sleep(every)
            if stop.is_set():
                break
            try:
                await fn(s)
            except Exception as e:
                log.warning("%s: %s", label, e)

    async def do_recheck(s):
        wl = await s.db.list_wallets()
        n = await service.process_due_reevals(s, wl, 50, console.print)
        if n:
            console.print(f"[dim]· rechecked {n} queued[/]")

    async def do_manage(s):
        await service.manage_open_positions(s, console.print)

    async def do_summary(s):
        # rolling FUNNEL line — so progress is VISIBLE instead of a wall of REJECTs (ADR-036 observability)
        since = (
            datetime.now(timezone.utc) - timedelta(seconds=cfg.run.summary_every_s)
        ).isoformat()
        d = await s.db.recent_decision_summary(since)
        wl = len(await s.db.list_wallets())
        qn = await s.db.queue_size()
        used = await s.db.get_credit_usage(credits.month_key())
        flags = " ".join(f"{k}={v}" for k, v in d["top_flags"]) or "—"
        console.print(
            f"[bold cyan]FUNNEL {cfg.run.summary_every_s}s[/] · analyzed={d['total']} "
            f"| [green]BUY {d['buy_smart'] + d['buy_quality']}[/] (smart {d['buy_smart']}/quality {d['buy_quality']}) "
            f"· [yellow]WATCH {d['watch']}[/] · [red]REJECT {d['reject']}[/] [{flags}] "
            f"| watchlist={wl} queue={qn} credits={used:,.0f}/{cfg.helius.monthly_budget_funnel:,.0f}"
        )

    async with service.open_services(cfg, secrets, approval=_semi_auto_approve) as s:
        tasks = [
            asyncio.create_task(ingest_loop(s)),
            asyncio.create_task(periodic(s, do_recheck, recheck_every, "recheck")),
            asyncio.create_task(periodic(s, do_manage, manage_every, "manage")),
            asyncio.create_task(periodic(s, do_summary, cfg.run.summary_every_s, "summary")),
        ]
        try:
            if duration > 0:
                await asyncio.sleep(duration)
            else:
                await asyncio.gather(*tasks)
        finally:
            stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    console.print("[bold]run finished[/]")


@app.command()
def stats() -> None:
    """Show paper-trading performance: PnL, win-rate, verdict distribution, health notes."""
    cfg, _ = load()
    s = analytics.compute_stats(cfg.storage.db_path)
    for n in s.health_notes:
        console.print(f"[yellow]⚠ {n}[/]")
    kpi = Table(title="SolScout — paper performance", show_header=False)
    kpi.add_row(
        "decision span", f"{(s.first_decision or '—')[:19]} → {(s.last_decision or '—')[:19]}"
    )
    kpi.add_row("launches seen", str(s.candidates_total))
    kpi.add_row(
        "decisions",
        f"{s.decisions_total}  (BUY {s.verdicts.get('BUY', 0)} · "
        f"WATCH {s.verdicts.get('WATCH', 0)} · REJECT {s.verdicts.get('REJECT', 0)})",
    )
    kpi.add_row("BUY tiers", f"smart {s.buy_smart} · quality {s.buy_quality}")
    kpi.add_row("paper buys / sells", f"{s.buys} / {s.sells}")
    kpi.add_row("open positions", f"{s.open_count}  (exposure {s.open_exposure_sol:.3f} SOL)")
    pnl_color = (
        "green" if s.realized_pnl_total > 0 else ("red" if s.realized_pnl_total < 0 else "white")
    )
    kpi.add_row(
        "closed trades",
        f"{s.wins}W / {s.losses}L"
        + (f"  win-rate {s.win_rate}%" if s.win_rate is not None else ""),
    )
    kpi.add_row(
        "realized PnL",
        f"[{pnl_color}]{s.realized_pnl_total:+.4f} SOL[/]"
        + (f"  (avg {s.avg_pnl:+.4f})" if s.avg_pnl is not None else ""),
    )
    kpi.add_row("best / worst", f"[green]{s.best_pnl:+.4f}[/] / [red]{s.worst_pnl:+.4f}[/]")
    kpi.add_row("watchlist / queue", f"{s.watchlist_size} / {s.queue_size}")
    budget = cfg.helius.monthly_budget_funnel + cfg.helius.monthly_budget_discover
    pct = 100 * s.credits_used_month / budget if budget else 0
    kpi.add_row(
        "Helius credits (est., month)", f"{s.credits_used_month:,.0f} / {budget:,.0f}  ({pct:.1f}%)"
    )
    console.print(kpi)

    # tier edge table — the honest "which edge actually works on paper?" view (ADR-036)
    if any(v["trades"] for v in s.tier_pnl.values()):
        tt = Table(title="Edge by tier (closed paper trades)")
        for col in ("tier", "trades", "wins", "win-rate", "realized PnL"):
            tt.add_column(col)
        for tier in ("smart", "quality"):
            v = s.tier_pnl.get(tier, {"trades": 0, "wins": 0, "pnl": 0.0})
            wr = f"{100 * v['wins'] / v['trades']:.0f}%" if v["trades"] else "—"
            color = "green" if v["pnl"] > 0 else ("red" if v["pnl"] < 0 else "white")
            tt.add_row(
                tier, str(v["trades"]), str(v["wins"]), wr, f"[{color}]{v['pnl']:+.4f} SOL[/]"
            )
        console.print(tt)

    console.print(
        "[dim]Credits shown are an ESTIMATE — align to your Helius dashboard with "
        "`solscout credits-sync <real_number>`. Run `solscout dashboard` for the live web view.[/]"
    )


@app.command("credits-sync")
def credits_sync(used: int) -> None:
    """Align the credit meter to your REAL Helius dashboard number (e.g. `credits-sync 15351`).
    Our meter is an ESTIMATE; sync it occasionally so the governor paces against reality."""
    cfg, _ = load()
    asyncio.run(_credits_sync(cfg, used))


async def _credits_sync(cfg, used: int) -> None:
    async with Db(cfg.storage.db_path) as db:
        await db.set_credit_usage(credits.month_key(), float(used))
    budget = cfg.helius.monthly_budget_funnel + cfg.helius.monthly_budget_discover
    console.print(
        f"[green]✓ credit meter set to {used:,} (this month)[/] · pacing budget {budget:,.0f}"
    )
    console.print(
        "[dim]The governor now paces against this number. Re-sync from the Helius dashboard anytime.[/]"
    )


@app.command()
def dashboard(port: int = 8787) -> None:
    """Serve a local web dashboard (auto-refreshing) of everything we measure. Ctrl-C to stop."""
    cfg, _ = load()
    console.print(
        f"[bold cyan]SolScout dashboard[/] → [link]http://localhost:{port}[/]  [dim](Ctrl-C to stop)[/]"
    )
    try:
        dashboard_mod.serve(cfg.storage.db_path, port)
    except KeyboardInterrupt:
        console.print("\n[yellow]dashboard stopped[/]")


@app.command()
def queue() -> None:
    """Show the grace-period re-evaluation queue (PENDING launches awaiting re-check)."""
    asyncio.run(_queue())


async def _queue() -> None:
    cfg, _ = load()
    async with Db(cfg.storage.db_path) as db:
        items = await db.due_reevals(now=datetime.now(timezone.utc).replace(year=2100))  # all
        size = await db.queue_size()
    if not items:
        console.print("[dim]re-eval queue empty[/]")
        return
    t = Table(title=f"Grace re-eval queue ({size})")
    for col in ("symbol", "mint", "attempts"):
        t.add_column(col)
    for it in items:
        t.add_row(it["symbol"] or "?", it["mint"][:12] + "…", str(it["attempts"]))
    console.print(t)


@app.command()
def recheck(max_items: int = 50) -> None:
    """Re-run the funnel on due queued launches (now indexed/liquid). Run periodically (cron/loop)."""
    asyncio.run(_recheck(max_items))


async def _recheck(max_items: int) -> None:
    cfg, secrets = load()
    async with service.open_services(cfg, secrets, approval=_semi_auto_approve) as s:
        watchlist = await s.db.list_wallets()
        n = await service.process_due_reevals(s, watchlist, max_items, console.print)
    console.print(
        "[dim]nothing due for re-eval[/]" if n == 0 else f"[bold]recheck done[/] · processed {n}"
    )


@app.command()
def positions(detail: bool = False) -> None:
    """Show open paper positions, marked against live price. --detail = full per-position breakdown
    (full mint + explorer links, age, exit distances, why it was bought) without touching the DB."""
    asyncio.run(_positions(detail))


async def _positions(detail: bool = False) -> None:
    cfg, _ = load()
    pcfg = cfg.position
    async with Db(cfg.storage.db_path) as db, DexScreenerClient() as dex:
        open_pos = await db.get_open_positions()
        pnl_today = await db.realized_pnl_today()
        if not open_pos:
            console.print(f"[dim]no open positions · realized PnL today: {pnl_today:+.4f} SOL[/]")
            return
        if not detail:
            t = Table(title="Open paper positions")
            for col in ("mint", "invested SOL", "entry", "now", "unrealized"):
                t.add_column(col)
            for p in open_pos:
                price = await _safe(dex.get_price_usd(p.mint), "price") or p.avg_price_usd
                chg = 100 * (price - p.avg_price_usd) / p.avg_price_usd if p.avg_price_usd else 0
                color = "green" if chg >= 0 else "red"
                t.add_row(
                    p.mint[:10] + "…",
                    f"{p.sol_invested:.4f}",
                    _usd(p.avg_price_usd),
                    _usd(price),
                    f"[{color}]{chg:+.1f}%[/]",
                )
            console.print(t)
            console.print(
                f"[dim]realized PnL today: {pnl_today:+.4f} SOL · `positions --detail` for full breakdown[/]"
            )
            return

        # --detail: full per-position card (no DB needed)
        for p in open_pos:
            price = await _safe(dex.get_price_usd(p.mint), "price") or p.avg_price_usd
            chg = 100 * (price - p.avg_price_usd) / p.avg_price_usd if p.avg_price_usd else 0
            hw = p.high_water_price or p.avg_price_usd
            from_hw = 100 * (price - hw) / hw if hw else 0
            age = (datetime.now(timezone.utc) - p.opened_at).total_seconds() / 60
            color = "green" if chg >= 0 else "red"
            buy = await _safe(db.last_buy_decision(p.mint), "decision")
            t = Table(title=f"{p.mint}", show_header=False)
            t.add_row("explorer", f"solscan.io/token/{p.mint}  ·  dexscreener.com/solana/{p.mint}")
            t.add_row("invested", f"{p.sol_invested:.4f} SOL  ({p.token_amount:,.0f} tokens)")
            t.add_row(
                "entry / now", f"{_usd(p.avg_price_usd)} → {_usd(price)}  [{color}]{chg:+.1f}%[/]"
            )
            t.add_row("age", f"{age:.0f} min  (time-stop at {pcfg.time_stop_minutes} min)")
            t.add_row("stop-loss", f"at {-pcfg.stop_loss_pct:.0f}%  (now {chg:+.1f}%)")
            t.add_row("take-profit", f"at +{max(pcfg.take_profit_pct):.0f}%  (now {chg:+.1f}%)")
            t.add_row(
                "trailing",
                f"{-pcfg.trailing_stop_pct:.0f}% off high  (now {from_hw:+.1f}% off high)",
            )
            if buy:
                comps = ", ".join(
                    f"{k}={v:.2f}" for k, v in (buy.breakdown.components or {}).items()
                )
                t.add_row(
                    "bought because",
                    f"score {buy.composite_score:.1f} · {comps}"
                    + (f"  [green]· {buy.tier} tier[/]" if buy.tier else ""),
                )
            console.print(t)
        console.print(f"[dim]realized PnL today: {pnl_today:+.4f} SOL[/]")


@app.command()
def manage() -> None:
    """Mark open positions against live price and apply exit rules (paper sells). Run on a schedule."""
    asyncio.run(_manage())


async def _manage() -> None:
    cfg, secrets = load()
    async with service.open_services(cfg, secrets) as s:
        if not await s.db.get_open_positions():
            console.print("[dim]no open positions to manage[/]")
            return
        exits = await service.manage_open_positions(s, console.print)
    console.print(f"[bold]manage done[/] · exits={exits}")


@app.command()
def watch(limit: int = 0) -> None:
    """Stream live DEX candidates (GeckoTerminal + DexScreener, free) — read-only preview, no funnel/trading.
    limit=0 runs forever (Ctrl-C to stop)."""
    asyncio.run(_watch(limit))


async def _watch(limit: int) -> None:
    cfg, _ = load()
    console.print(f"[cyan]Streaming live DEX candidates[/] (limit={limit or '∞'}) … Ctrl-C to stop")
    count = 0
    async with (
        Db(cfg.storage.db_path) as db,
        GeckoTerminalClient(network=cfg.geckoterminal.network) as gecko,
        DexScreenerClient() as dex,
    ):
        async for cand in stream_candidates(
            gecko,
            dex,
            poll_s=cfg.geckoterminal.poll_s,
            min_liquidity_usd=cfg.ingest.min_liquidity_to_analyze,
            new_pools_pages=cfg.geckoterminal.new_pools_pages,
            limit=limit,
        ):
            await db.save_candidate(cand)
            m = cand.raw_meta
            count += 1
            console.print(
                f"🆕 [cyan]{m.get('symbol', '?')}[/] · {m.get('dex', '?')} · "
                f"liq≈${m.get('liq', 0):,.0f} · [dim]{cand.mint}[/]"
            )
    console.print(
        f"[green]ingested {count} candidates[/] → saved. "
        "[dim]Run `solscout scan` / `solscout run` for the full funnel + paper trading.[/dim]"
    )


@app.command()
def backtest(fixtures: str = "fixtures/") -> None:
    """Replay stored candidates through the scoring engine. [not yet implemented]"""
    console.print(f"[yellow]backtest[/] will replay {fixtures} through scoring.")


if __name__ == "__main__":
    app()
