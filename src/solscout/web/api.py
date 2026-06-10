"""rugcheck.ai — FastAPI backend over the free analysis engine (ADR-040).

Opens the data clients ONCE (lifespan) and reuses them for every request. `GET /api/check/{mint}` runs the
full funnel (`pipeline.analyze`) and returns a `SafetyReport`. Helius is credit-governed and results are
cached per mint (web is user-triggered), so a refresh / repeat lookup is free + instant.

Run:  uv run solscout serve   (or)   uv run uvicorn solscout.web.api:app
"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import pipeline
from ..core.config import load
from ..llm import analyst
from ..core.credits import CompositeGovernor, CreditGovernor, DailyCap, day_key, month_key
from ..core.db import Db
from ..core.logging import get_logger
from ..data.dexscreener import DexScreenerClient
from ..data.geckoterminal import GeckoTerminalClient
from ..data.goplus import GoPlusClient
from ..data.helius import HeliusClient
from ..data.jupiter import JupiterClient
from ..data.rugcheck import RugCheckClient
from ..data.solana_rpc import SolanaRpcClient
from ..data.telegram_web import TelegramWebClient
from ..data.tweetscout import TweetScoutClient
from ..data.twitter_public import TwitterPublicClient
from . import osint as osint_mod
from .ratelimit import DailyMintLedger, IpLimiter
from .report import build_report

log = get_logger("solscout.web")
STATIC = Path(__file__).parent / "static"
_B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # base58, no 0/O/I/l


# ADR-046: tiny adapters injecting the SQLite caches into clients/pipeline WITHOUT `data/` importing Db.
class _DbHoldersStore:
    def __init__(self, db: Db, ttl_s: float):
        self._db, self._ttl = db, ttl_s

    async def get(self, mint: str):
        return await self._db.get_holders(mint, self._ttl)

    async def put(self, mint: str, holders, complete: bool) -> None:
        await self._db.put_holders(mint, holders, complete)


class _DbFunderStore:
    def __init__(self, db: Db, ttl_s: float):
        self._db, self._ttl = db, ttl_s

    async def get(self, wallet: str):
        return await self._db.get_funder(wallet, self._ttl)

    async def put(self, wallet: str, funder: str | None) -> None:
        await self._db.put_funder(wallet, funder)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg, secrets = load()
    app.state.cfg = cfg
    app.state.cache = {}  # L1: mint -> (ts, report) — in-process, fastest path
    app.state.inflight = {}  # mint -> Future — singleflight: concurrent same-mint checks share ONE analysis
    async with Db(cfg.storage.db_path) as db:
        app.state.db = db  # ONE shared handle (aiosqlite serializes through its worker thread; WAL is on)
        await db.prune_caches(
            cfg.web.report_db_ttl_s, cfg.web.wallet_summary_ttl_s,
            cfg.web.holders_db_ttl_s, cfg.web.funder_ttl_s,
        )
        app.state.funder_store = _DbFunderStore(db, cfg.web.funder_ttl_s)
        # per-IP abuse control (ADR-046): bursts + fresh-mints/day. Cached reports always serve.
        rate = cfg.web.rate
        app.state.limiter = (
            IpLimiter(rate.per_ip_burst, rate.per_ip_refill_per_s, rate.max_tracked_ips)
            if rate.enabled else None
        )
        app.state.mint_ledger = DailyMintLedger(rate.daily_unique_mints_per_ip) if rate.enabled else None
        mk = month_key()
        # governor = monthly pace AND a daily web sub-budget — both persisted in credit_usage
        # (month TEXT PK takes arbitrary keys, so `web:YYYY-MM-DD` rows need no migration).
        daily = DailyCap(cfg.web.helius_daily_budget, used=await db.get_credit_usage("web:" + day_key()))
        gov = CompositeGovernor(
            CreditGovernor(cfg.helius.monthly_budget_funnel, used=await db.get_credit_usage(mk)),
            daily,
        )

        async def _spend(cost: float) -> None:
            await db.add_credit_usage(mk, cost)
            await db.add_credit_usage("web:" + day_key(), cost)

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
                holders_store=_DbHoldersStore(db, cfg.web.holders_db_ttl_s),
            ) as helius,
            JupiterClient() as jup,
            RugCheckClient() as rc,
            GoPlusClient() as gp,
            TwitterPublicClient() as twp,
            TelegramWebClient() as tg,
            TweetScoutClient(secrets.tweetscout_api_key) as ts,
        ):
            app.state.clients = dict(dex=dex, gecko=gecko, rpc=rpc, helius=helius, jup=jup,
                                     rc=rc, gp=gp, twp=twp, tg=tg, ts=ts)
            app.state.helius_on = bool(secrets.helius_api_key)
            app.state.groq_key = secrets.groq_api_key  # optional; analyst uses it when set, else local Ollama
            log.info("rugcheck.ai up — helius=%s · analyst=%s", app.state.helius_on,
                     "groq" if app.state.groq_key else "ollama")
            yield


app = FastAPI(title="rugcheck.ai", version="1.0", lifespan=lifespan)


def _client_ip(request: Request) -> str:
    cfg = getattr(app.state, "cfg", None)
    if cfg is not None and cfg.web.rate.trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _429(scope: str, detail: str, retry_after_s: int) -> JSONResponse:
    """The rate-limit response shape the frontend renders (`detail` + optional `retry_after_s`)."""
    return JSONResponse(
        {"error": "rate_limited", "scope": scope, "detail": detail, "retry_after_s": retry_after_s},
        status_code=429,
        headers={"Retry-After": str(retry_after_s)},
    )


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    limiter = getattr(app.state, "limiter", None)
    path = request.url.path
    if limiter is None or not path.startswith("/api/") or path == "/api/health":
        return await call_next(request)
    ok, retry = limiter.allow(_client_ip(request))
    if not ok:
        return _429("burst", f"Too many requests — slow down and retry in ~{retry}s.", retry)
    return await call_next(request)


@app.get("/api/health")
async def health():
    on = getattr(app.state, "helius_on", False)
    return {
        "ok": True,
        "providers": {
            "dexscreener": True,
            "geckoterminal": True,
            "rugcheck": True,
            "jupiter": True,
            "helius": on,
            "ollama_ai": True,  # best-effort; degrades if Ollama is down
        },
    }


async def _run_analysis(mint: str) -> dict:
    """The full (expensive) pipeline for one mint. Raises on failure — callers map that to a 502."""
    cfg = app.state.cfg
    c = app.state.clients
    db: Db = app.state.db
    t0 = time.monotonic()
    watchlist = await db.list_wallets()
    a = await pipeline.analyze(
        mint, cfg, dex=c["dex"], rpc=c["rpc"], helius=c["helius"], tg=c["tg"], ts=c["ts"],
        jup=c["jup"], gecko=c["gecko"], rc=c["rc"], gp=c["gp"], watchlist=watchlist, skip_llm=True,
        funder_store=app.state.funder_store,
    )
    # deep OSINT (twitter + deployer + buyer wallets + source consensus) — best-effort, never fatal
    osint = await osint_mod.gather(a, cfg, rc=c["rc"], gp=c["gp"], helius=c["helius"], tw=c["twp"], db=db) or {}
    report = build_report(a, cfg, osint=osint, took_ms=round((time.monotonic() - t0) * 1000))
    # AI analyst (ADR-042): a verdict that reasons over the WHOLE report — runs AFTER everything is
    # assembled so the model cites the actual findings, not just name+market. Free + local; best-effort.
    report["ai"] = await analyst.analyze_report(report, cfg.llm, groq_key=app.state.groq_key)
    return report


@app.get("/api/check/{mint}")
async def check(mint: str, request: Request, refresh: bool = False):
    mint = mint.strip()
    if not _B58.match(mint):
        return JSONResponse({"error": "invalid_mint", "detail": "Not a valid Solana mint address."}, status_code=400)

    web = app.state.cfg.web
    db: Db = app.state.db
    cache = app.state.cache
    now = time.monotonic()
    hit = cache.get(mint)
    # L1 in-process. refresh=1 is honored only past `min_refresh_interval_s` — a cache-busting loop can't
    # force a Helius re-spend every second (ADR-046).
    if hit and (now - hit[0]) < (web.min_refresh_interval_s if refresh else web.report_ttl_s):
        return {**hit[1], "cached": True}
    # L2 persistent (survives restarts/extra workers). Same refresh guard via a shorter TTL.
    l2 = await db.cache_get_report(mint, web.min_refresh_interval_s if refresh else web.report_db_ttl_s)
    if l2 is not None:
        cache[mint] = (now, l2)
        return {**l2, "cached": True}

    # singleflight: if this mint is already being analyzed, await THAT result instead of double-spending
    inflight: dict[str, asyncio.Future] = app.state.inflight
    fut = inflight.get(mint)
    if fut is not None:
        report = await fut
        if report is None:
            return JSONResponse(
                {"error": "analysis_failed", "detail": "Could not analyze this token right now.", "mint": mint},
                status_code=502,
            )
        return {**report, "cached": True}

    # this is genuinely NEW work → charge it to the caller's daily fresh-mint allowance (ADR-046).
    # Cached/in-flight lookups above never reach this point, so normal browsing is unaffected.
    ledger = getattr(app.state, "mint_ledger", None)
    if ledger is not None and not ledger.allow(_client_ip(request), mint, day_key()):
        return _429(
            "daily_mints",
            "Daily fresh-scan limit reached for your IP — already-scanned tokens still work. Resets at 00:00 UTC.",
            3600,
        )

    fut = asyncio.get_running_loop().create_future()
    inflight[mint] = fut
    try:
        report = await _run_analysis(mint)
    except Exception as e:  # never 500 the user — return a graceful error report
        log.warning("check %s failed: %s", mint[:8], e)
        fut.set_result(None)  # wake waiters; None = failed (no unretrieved-exception noise)
        return JSONResponse(
            {"error": "analysis_failed", "detail": "Could not analyze this token right now.", "mint": mint},
            status_code=502,
        )
    finally:
        inflight.pop(mint, None)
    fut.set_result(report)
    cache[mint] = (time.monotonic(), report)
    if report.get("ready", True):  # L2 write — only clean, READY reports persist (PENDING goes stale in minutes)
        await db.cache_put_report(mint, report)
    return {**report, "cached": False}


# — static frontend —
# `no-cache` = the browser MUST revalidate before reusing a cached asset. With the ETag already sent, an
# unchanged file still returns a cheap 304 (no re-download), but an EDITED css/js/gif shows up immediately on
# a normal refresh — no more stale-cache confusion where a change "didn't take" until a hard reload.
class _NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


if STATIC.exists():
    app.mount("/static", _NoCacheStatic(directory=str(STATIC)), name="static")


@app.get("/")
async def index():
    f = STATIC / "index.html"
    if f.exists():
        return FileResponse(str(f), headers={"Cache-Control": "no-cache"})
    return JSONResponse({"service": "rugcheck.ai", "hint": "frontend not built"}, status_code=200)
