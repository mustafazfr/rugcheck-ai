"""rugcheck.ai — FastAPI backend over the free analysis engine (ADR-040).

Opens the data clients ONCE (lifespan) and reuses them for every request. `GET /api/check/{mint}` runs the
full funnel (`pipeline.analyze`) and returns a `SafetyReport`. Helius is credit-governed and results are
cached per mint (web is user-triggered), so a refresh / repeat lookup is free + instant.

Run:  uv run solscout serve   (or)   uv run uvicorn solscout.web.api:app
"""

from __future__ import annotations

import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import pipeline
from ..core.config import load
from ..llm import analyst
from ..core.credits import CreditGovernor, month_key
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
from .report import build_report

log = get_logger("solscout.web")
STATIC = Path(__file__).parent / "static"
_B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # base58, no 0/O/I/l
_CACHE_TTL = 300.0  # per-mint report cache (seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg, secrets = load()
    app.state.cfg = cfg
    app.state.cache = {}  # mint -> (ts, report)
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
            GoPlusClient() as gp,
            TwitterPublicClient() as twp,
            TelegramWebClient() as tg,
            TweetScoutClient(secrets.tweetscout_api_key) as ts,
        ):
            app.state.clients = dict(dex=dex, gecko=gecko, rpc=rpc, helius=helius, jup=jup,
                                     rc=rc, gp=gp, twp=twp, tg=tg, ts=ts)
            app.state.helius_on = bool(secrets.helius_api_key)
            log.info("rugcheck.ai up — helius=%s", app.state.helius_on)
            yield


app = FastAPI(title="rugcheck.ai", version="1.0", lifespan=lifespan)


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


@app.get("/api/check/{mint}")
async def check(mint: str, refresh: bool = False):
    mint = mint.strip()
    if not _B58.match(mint):
        return JSONResponse({"error": "invalid_mint", "detail": "Not a valid Solana mint address."}, status_code=400)

    cache = app.state.cache
    hit = cache.get(mint)
    if hit and not refresh and (time.monotonic() - hit[0]) < _CACHE_TTL:
        return {**hit[1], "cached": True}

    cfg = app.state.cfg
    c = app.state.clients
    t0 = time.monotonic()
    try:
        async with Db(cfg.storage.db_path) as db:
            watchlist = await db.list_wallets()
        a = await pipeline.analyze(
            mint, cfg, dex=c["dex"], rpc=c["rpc"], helius=c["helius"], tg=c["tg"], ts=c["ts"],
            jup=c["jup"], gecko=c["gecko"], rc=c["rc"], gp=c["gp"], watchlist=watchlist, skip_llm=True,
        )
        # deep OSINT (twitter + deployer + buyer wallets + source consensus) — best-effort, never fatal
        osint = await osint_mod.gather(a, cfg, rc=c["rc"], gp=c["gp"], helius=c["helius"], tw=c["twp"]) or {}
        report = build_report(a, cfg, osint=osint, took_ms=round((time.monotonic() - t0) * 1000))
        # AI analyst (ADR-042): a verdict that reasons over the WHOLE report — runs AFTER everything is
        # assembled so the model cites the actual findings, not just name+market. Free + local; best-effort.
        report["ai"] = await analyst.analyze_report(report, cfg.llm)
    except Exception as e:  # never 500 the user — return a graceful error report
        log.warning("check %s failed: %s", mint[:8], e)
        return JSONResponse(
            {"error": "analysis_failed", "detail": "Could not analyze this token right now.", "mint": mint},
            status_code=502,
        )
    cache[mint] = (time.monotonic(), report)
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
