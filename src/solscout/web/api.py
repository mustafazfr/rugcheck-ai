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
from pydantic import BaseModel
from solders.pubkey import Pubkey

from .. import pipeline
from ..core.config import load
from ..core.credits import CompositeGovernor, CreditGovernor, DailyCap, day_key, month_key
from ..core.db import Db
from ..core.logging import get_logger
from ..data.dexscreener import DexScreenerClient
from ..data.geckoterminal import GeckoTerminalClient
from ..data.goplus import GoPlusClient
from ..data.helius import HeliusClient
from ..data.jupiter import JupiterClient
from ..data.rdap import RdapClient
from ..data.rugcheck import RugCheckClient
from ..data.solana_rpc import SolanaRpcClient
from ..data.telegram_web import TelegramWebClient
from ..data.tweetscout import TweetScoutClient
from ..data.twitter_public import TwitterPublicClient
from . import osint as osint_mod
from . import payments as pay_mod
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
        # ADR-047 PRO pass — validated HERE so a misconfigured deploy degrades to "payments off",
        # never to 500s mid-flight. NON-CUSTODIAL: `payout` is a public address; the only secret is
        # the HMAC token key, and it lives in .env (Golden Rule #3).
        payc = cfg.payments
        payout = (secrets.payout_wallet or payc.payout_wallet).strip()
        app.state.pass_secret = secrets.pass_secret
        app.state.pay_enabled = bool(payc.enabled and payout and secrets.pass_secret)
        if payc.enabled and not app.state.pay_enabled:
            log.error("payments.enabled=true but PAYOUT_WALLET / PASS_SECRET missing — payments stay OFF")
        if app.state.pay_enabled:
            try:
                Pubkey.from_string(payout)
            except ValueError:
                log.error("PAYOUT_WALLET %r is not a valid Solana pubkey — payments stay OFF", payout[:12])
                app.state.pay_enabled = False
        app.state.payout = payout
        # PRO burst bucket is keyed by WALLET (fairer than IP — pass holders may share cafés, not passes)
        app.state.pro_limiter = IpLimiter(payc.pass_burst, rate.per_ip_refill_per_s, rate.max_tracked_ips)
        await db.prune_pay_intents(payc.intent_ttl_s)
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
            # payment verification can point elsewhere (devnet dry-run) without touching the main RPC
            SolanaRpcClient(endpoint=cfg.payments.rpc_url or cfg.solana_rpc_url or None) as pay_rpc,
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
            RdapClient() as rdap,
            TelegramWebClient() as tg,
            TweetScoutClient(secrets.tweetscout_api_key) as ts,
        ):
            app.state.clients = dict(dex=dex, gecko=gecko, rpc=rpc, pay_rpc=pay_rpc, helius=helius,
                                     jup=jup, rc=rc, gp=gp, twp=twp, rdap=rdap, tg=tg, ts=ts)
            app.state.helius_on = bool(secrets.helius_api_key)
            log.info("rugcheck.ai up — helius=%s · payments=%s", app.state.helius_on,
                     "on" if app.state.pay_enabled else "off")
            yield


app = FastAPI(title="rugcheck.ai", version="1.0", lifespan=lifespan)


def _client_ip(request: Request) -> str:
    cfg = getattr(app.state, "cfg", None)
    if cfg is not None and cfg.web.rate.trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _pro_wallet(request: Request) -> str | None:
    """Wallet behind a valid `X-Pass: <wallet>.<token>` header, else None. Pure HMAC check — no db
    hit per request; a token only ever exists because /api/pay/confirm or /restore minted it."""
    secret = getattr(app.state, "pass_secret", "")
    parsed = pay_mod.parse_pass_header(request.headers.get("x-pass"))
    if not secret or parsed is None:
        return None
    wallet, token = parsed
    return wallet if pay_mod.check_pass_token(secret, wallet, token) else None


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
    # PRO pass (ADR-047): a valid X-Pass rides its own, larger bucket keyed by wallet instead of IP
    pro = _pro_wallet(request)
    if pro is not None:
        ok, retry = app.state.pro_limiter.allow("w:" + pro)
    else:
        ok, retry = limiter.allow(_client_ip(request))
    if not ok:
        return _429("burst", f"Too many requests — slow down and retry in ~{retry}s.", retry)
    return await call_next(request)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """ADR-048 hardening. CSP works because nothing is inline anymore (boot.js is external):
    scripts only from us + the pinned unpkg web3.js; styles from us + Google Fonts (inline style
    ATTRIBUTES are part of how app.js renders, hence 'unsafe-inline' on style-src only); images
    https: (Twitter avatars). HSTS only makes sense once we're actually behind TLS → trust_proxy."""
    resp = await call_next(request)
    cfg = getattr(app.state, "cfg", None)
    if cfg is None or not cfg.web.security_headers:
        return resp
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; "
        "connect-src 'self'; media-src 'self'; object-src 'none'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'",
    )
    if cfg.web.rate.trust_proxy:  # behind Caddy/Cloudflare = TLS is on → pin it
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


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
    osint = await osint_mod.gather(
        a, cfg, rc=c["rc"], gp=c["gp"], helius=c["helius"], tw=c["twp"], db=db, jup=c["jup"], rdap=c["rdap"]
    ) or {}
    # ADR-048: no LLM in the product path anymore — the verdict is (and always was) the
    # deterministic engine; dropping the analyst removes the slowest step + the injection surface.
    return build_report(a, cfg, osint=osint, took_ms=round((time.monotonic() - t0) * 1000))


async def _ab_bump(variant: str | None, event: str) -> None:
    """Fire-and-forget A/B counter (ADR-046) — measurement must never fail a check."""
    if variant not in ("a", "b") or not app.state.cfg.web.ab_enabled:
        return
    try:
        await app.state.db.ab_bump(day_key(), variant, event)
    except Exception:
        pass


@app.get("/api/check/{mint}")
async def check(mint: str, request: Request, refresh: bool = False, variant: str | None = None):
    mint = mint.strip()
    if not _B58.match(mint):
        return JSONResponse({"error": "invalid_mint", "detail": "Not a valid Solana mint address."}, status_code=400)

    web = app.state.cfg.web
    db: Db = app.state.db
    cache = app.state.cache
    now = time.monotonic()
    # PRO pass (ADR-047): paid wallets skip the daily fresh-mint ledger and get a snappier refresh
    # guard — but Helius budgets still apply (the governor protects the free tier, pass or not).
    pro = _pro_wallet(request) is not None
    guard = app.state.cfg.payments.pro_refresh_guard_s if pro else web.min_refresh_interval_s
    hit = cache.get(mint)
    # L1 in-process. refresh=1 is honored only past the guard — a cache-busting loop can't
    # force a Helius re-spend every second (ADR-046).
    if hit and (now - hit[0]) < (guard if refresh else web.report_ttl_s):
        await _ab_bump(variant, "check_cached")
        return {**hit[1], "cached": True}
    # L2 persistent (survives restarts/extra workers). Same refresh guard via a shorter TTL.
    l2 = await db.cache_get_report(mint, guard if refresh else web.report_db_ttl_s)
    if l2 is not None:
        cache[mint] = (now, l2)
        await _ab_bump(variant, "check_cached")
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
    if ledger is not None and not pro and not ledger.allow(_client_ip(request), mint, day_key()):
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
    await _ab_bump(variant, "check_fresh")
    return {**report, "cached": False}


@app.get("/api/ab")
async def ab_stats(days: int = 14):
    """A/B design measurement (ADR-046): per-variant check counts over the last `days` UTC days."""
    from datetime import datetime, timedelta, timezone

    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 90)))).strftime("%Y-%m-%d")
    try:
        stats = await app.state.db.ab_stats(since)
    except Exception:
        stats = {}
    return {"since": since, "variants": stats}


# — ADR-047 PRO pass payments (non-custodial; pure verification in web/payments.py) —
_B58SIG = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{64,90}$")  # a 64-byte tx signature in base58


def _pay_off() -> JSONResponse:
    return JSONResponse({"error": "payments_disabled", "detail": "Payments are not enabled."}, status_code=404)


@app.get("/api/pay/status")
async def pay_status():
    """Frontend probe: is the PRO pass purchasable here, and at what price?"""
    if not getattr(app.state, "pay_enabled", False):
        return {"enabled": False}
    payc = app.state.cfg.payments
    return {"enabled": True, "price_sol": payc.price_sol, "payout": app.state.payout,
            "confirm_wait_s": payc.confirm_wait_s}


@app.post("/api/pay/intent")
async def pay_intent():
    """Mint a payment intent: a fresh reference pubkey the buyer's transfer must carry. The matching
    private key is discarded inside `new_intent` — it never signs anything, it's a tracking tag."""
    if not getattr(app.state, "pay_enabled", False):
        return _pay_off()
    payc = app.state.cfg.payments
    intent_id, reference = pay_mod.new_intent()
    await app.state.db.create_pay_intent(intent_id, reference)
    return {"intent_id": intent_id, "reference": reference, "payout": app.state.payout,
            "price_sol": payc.price_sol, "lamports": pay_mod.lamports(payc.price_sol)}


@app.get("/api/pay/blockhash")
async def pay_blockhash():
    """Recent blockhash for the client-built transfer — proxied so the browser never talks RPC."""
    if not getattr(app.state, "pay_enabled", False):
        return _pay_off()
    try:
        bh = await app.state.clients["pay_rpc"].latest_blockhash(app.state.cfg.payments.commitment)
    except Exception:
        bh = None
    if not bh:
        return JSONResponse({"error": "rpc_unavailable", "detail": "Could not fetch a blockhash."}, status_code=502)
    return {"blockhash": bh}


class _ConfirmBody(BaseModel):
    intent_id: str
    sig: str


@app.post("/api/pay/confirm")
async def pay_confirm(body: _ConfirmBody):
    """Verify the broadcast transaction on-chain and grant the pass. Client polls this (~5s) while
    the tx confirms: {"pending": true} = keep waiting; 4xx = definitive no; ok = token issued."""
    if not getattr(app.state, "pay_enabled", False):
        return _pay_off()
    payc = app.state.cfg.payments
    db: Db = app.state.db
    if not _B58SIG.match(body.sig.strip()):
        return JSONResponse({"error": "bad_signature", "detail": "Not a valid transaction signature."}, status_code=400)
    sig = body.sig.strip()
    intent = await db.get_pay_intent(body.intent_id, payc.intent_ttl_s)
    if intent is None:
        return JSONResponse({"error": "unknown_intent", "detail": "Intent expired or unknown — start over."}, status_code=404)
    if intent["status"] == "paid":  # idempotent: re-confirming a paid intent re-issues the same token
        token = pay_mod.mint_pass_token(app.state.pass_secret, intent["wallet"])
        return {"ok": True, "wallet": intent["wallet"], "token": token}
    if await db.pay_sig_used(sig):
        return JSONResponse({"error": "sig_reused", "detail": "That transaction already redeemed a pass."}, status_code=409)
    try:
        tx = await app.state.clients["pay_rpc"].get_transaction(sig, payc.commitment)
    except Exception:
        return JSONResponse({"error": "rpc_unavailable", "detail": "Chain lookup failed — retry shortly."}, status_code=502)
    ok, reason, info = pay_mod.validate_payment_tx(
        tx, reference=intent["reference"], payout=app.state.payout,
        min_lamports=pay_mod.lamports(payc.price_sol),
    )
    if reason == "not_found":  # not on chain (yet) at this commitment — the client keeps polling
        return {"ok": False, "pending": True}
    if not ok:
        log.info("pay_confirm rejected intent=%s reason=%s", body.intent_id[:8], reason)
        return JSONResponse({"error": "payment_invalid", "reason": reason,
                             "detail": "Transaction doesn't match this payment."}, status_code=402)
    wallet = info["wallet"]
    await db.mark_intent_paid(body.intent_id, wallet, sig, info["lamports"])
    await db.grant_pass(wallet, sig)
    log.info("PRO pass granted wallet=%s… lamports=%d", wallet[:8], info["lamports"])
    return {"ok": True, "wallet": wallet, "token": pay_mod.mint_pass_token(app.state.pass_secret, wallet)}


class _RestoreBody(BaseModel):
    wallet: str
    ts: int
    sig_hex: str


@app.post("/api/pay/restore")
async def pay_restore(body: _RestoreBody):
    """Recover a pass on a new device: prove wallet ownership with signMessage, get the token back."""
    if not getattr(app.state, "pay_enabled", False):
        return _pay_off()
    if abs(time.time() - body.ts) > pay_mod.RESTORE_MAX_SKEW_S:
        return JSONResponse({"error": "stale", "detail": "Signature too old — try again."}, status_code=400)
    if not pay_mod.verify_wallet_signature(body.wallet, pay_mod.restore_message(body.wallet, body.ts), body.sig_hex):
        return JSONResponse({"error": "bad_proof", "detail": "Wallet signature didn't verify."}, status_code=401)
    if not await app.state.db.has_pass(body.wallet):
        return JSONResponse({"error": "no_pass", "detail": "No PRO pass on that wallet."}, status_code=404)
    return {"ok": True, "wallet": body.wallet, "token": pay_mod.mint_pass_token(app.state.pass_secret, body.wallet)}


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
