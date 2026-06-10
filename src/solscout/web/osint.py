"""Deep multi-source OSINT for the web report (ADR-041) — "turn the coin upside down".

Runs AFTER `pipeline.analyze` (so we already have market/holders/RugCheck/GoPlus) and gathers the extra
forensics the user asked for, all FREE:
  - twitter      : the project's X account (fxtwitter, keyless) → age/followers/verified → authenticity
  - deployer     : the creator wallet (RugCheck) → Helius history → prior token launches + funding source
  - holders_intel: the top BUYERS' wallets → Helius history → fresh-wallet / trader profile (insider tell)
  - sources      : RugCheck vs GoPlus side-by-side (a two-source consensus, not one point of failure)

Helius pieces are credit-governed (the app caches each report per mint). Best-effort: every sub-check
degrades to "unavailable" without breaking the report. Returns a dict folded into the SafetyReport, plus a
`flags` list that feeds the score.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from ..core.logging import get_logger
from ..data.twitter_public import authenticity, handle_from_url
from ..enrich.cluster import dominant_funder
from ..enrich.discovery import looks_like_trader, wallet_swap_summary
from ..enrich.smartmoney import count_prior_creations

log = get_logger("solscout.web.osint")


def is_fresh_wallet(summary: dict) -> bool:
    """A 'fresh' wallet has almost NO trading history: a near-empty, brand-new account that has barely
    swapped and touched barely any tokens. Many fresh wallets among a token's top holders = a likely
    insider/sybil cluster (the dev funded throwaway wallets to fake distribution). Pure.
    Tightened to AND (ADR-042): a busy single-token degen is not 'fresh' — only genuinely empty wallets are."""
    return summary.get("swaps_total", 0) <= 3 and summary.get("distinct_tokens", 0) <= 2


def first_tx_age_days(txs: list[dict]) -> int | None:
    """Oldest enhanced-tx timestamp → wallet age in days. None if no timestamps. Pure."""
    ts = [int(t["timestamp"]) for t in txs if isinstance(t, dict) and t.get("timestamp")]
    if not ts:
        return None
    oldest = datetime.fromtimestamp(min(ts), tz=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - oldest).days)


async def _wallet_intel(wallet: str, helius, db, ttl_s: float, tx_limit: int) -> dict:
    """ONE summary per wallet carrying everything we derive from its tx history (swap counts, prior token
    creations, dominant funder, age) — persisted to SQLite (ADR-046) so a wallet seen across tokens/restarts
    costs its 10 Helius credits exactly once per TTL. Cache only results from an ALLOWED call (a governor
    skip returns [] too — caching that would poison the row for the whole TTL)."""
    if db is not None:
        hit = await _safe(db.get_wallet_summary(wallet, ttl_s), "wallet-cache")
        if hit is not None:
            return hit
    allowed = helius.can_meter() if hasattr(helius, "can_meter") else True
    txs = await _safe(helius.address_transactions(wallet, limit=tx_limit), "wallet-txns")
    ok = txs is not None
    txs = txs or []
    s = wallet_swap_summary(txs)
    s["prior_creations"] = count_prior_creations(txs)
    s["funder"] = dominant_funder(txs, wallet)
    s["age_days"] = first_tx_age_days(txs)
    s["tx_count"] = len(txs)
    if db is not None and allowed and ok:
        await _safe(db.put_wallet_summary(wallet, s, tx_limit), "wallet-cache-put")
    return s


async def _safe(coro, label):
    try:
        return await coro
    except Exception as e:
        log.warning("osint %s failed: %s", label, e)
        return None


def _twitter_url(market) -> str | None:
    for s in (market.socials if market else []) or []:
        if (s.type or "").lower() == "twitter":
            return s.url
    return None


async def gather(a, cfg, *, rc=None, gp=None, helius=None, tw=None, db=None, jup=None) -> dict:
    out: dict = {"flags": []}
    helius_on = getattr(helius, "available", False)
    wallet_ttl = cfg.web.wallet_summary_ttl_s

    # — Jupiter token intel FIRST (ADR-046): free, fast, cached — a 3rd independent source whose
    #   devMints can replace the 10cr Helius deployer call entirely. —
    jt = await _safe(jup.token_info(a.mint), "jupiter-info") if jup is not None else None
    if jt is not None and not jt.available:
        jt = None
    if jt and (jt.organic_label or "").lower() == "low":
        out["flags"].append("jupiter_low_organic")  # Jupiter's own wash/organic detector disagrees with the hype
    auth_consensus = None
    if jt and a.mint_info is not None:
        ours = {"mint": a.mint_info.mint_authority is None, "freeze": a.mint_info.freeze_authority is None}
        theirs = {"mint": jt.mint_auth_disabled, "freeze": jt.freeze_auth_disabled}
        known = [(ours[k], theirs[k]) for k in ("mint", "freeze") if theirs[k] is not None]
        if known:
            auth_consensus = "agree" if all(o == t for o, t in known) else "mismatch"
            if auth_consensus == "mismatch":
                out["flags"].append("authority_consensus_mismatch")

    # — Twitter / X identity —
    handle = handle_from_url(_twitter_url(a.market))
    if handle and cfg.twitter.enabled and tw is not None:
        p = await _safe(tw.profile(handle), "twitter")
        if p and p.available:
            score, verdict, tflags = authenticity(p, cfg.twitter)
            out["twitter"] = {
                "available": True, "handle": p.handle, "name": p.name, "url": p.url,
                "followers": p.followers, "following": p.following, "tweets": p.tweets,
                "age_days": p.age_days, "verified": p.verified,
                "description": (p.description or "")[:200], "avatar_url": p.avatar_url,
                "score": score, "verdict": verdict, "flags": tflags,
            }
            if verdict == "inauthentic":
                out["flags"].append("twitter_inauthentic")
            elif verdict == "weak":
                out["flags"].append("twitter_weak")
        else:
            out["twitter"] = {"available": False, "handle": handle, "url": f"https://x.com/{handle}"}
            out["flags"].append("twitter_weak")
    else:
        out["twitter"] = {"available": False, "handle": None, "linked": False}
        out["flags"].append("no_twitter")

    # — RugCheck (for creator) + GoPlus, both cached from the analyze pass —
    rc_rep = await _safe(rc.report(a.mint), "rugcheck") if rc else None
    gp_rep = await _safe(gp.token_security(a.mint), "goplus") if gp else None
    creator = rc_rep.creator if (rc_rep and rc_rep.available) else None
    if not creator and jt and jt.dev_wallet:
        creator = jt.dev_wallet  # RugCheck down/missing → Jupiter still knows the dev (free)

    # — Deployer + top-buyer history is the heaviest part: ≈1 + N Helius enhanced-tx calls. These used to run
    #   one-by-one (~17s+ on a fresh token, the bulk of the old ~50s wait). Launch them ALL concurrently up
    #   front — the client still staggers request *starts* by 120ms, but the round-trips overlap (ADR-044).
    #   Each wallet goes through the persistent `wallet_tx_summary` cache (ADR-046): a db hit = 0 credits. —
    # Jupiter-first deployer (ADR-046): when there's no cached summary but Jupiter already counted the
    # dev's prior mints, skip the 10cr Helius call — devMints answers the serial-deployer question free.
    dep_cached = (
        await _safe(db.get_wallet_summary(creator, wallet_ttl), "wallet-cache")
        if (db is not None and creator) else None
    )
    use_jup_dep = (
        cfg.web.jupiter_first_deployer and dep_cached is None
        and jt is not None and jt.dev_mints is not None and bool(creator)
    )
    dep_task = (
        asyncio.create_task(_wallet_intel(creator, helius, db, wallet_ttl, cfg.deployer.tx_limit))
        if (creator and cfg.deployer.enabled and helius_on and not use_jup_dep) else None
    )
    dist = (a.top_holders or [])[: cfg.deployer.holders_intel_top_n]

    async def _profile_holder(h):
        s = await _wallet_intel(h.get("owner"), helius, db, wallet_ttl, cfg.deployer.tx_limit)
        return h, s, is_fresh_wallet(s), looks_like_trader(s)

    # gather() already schedules each profile as a Task (they run concurrently); just await it later.
    buyers_task = (
        asyncio.gather(*(_profile_holder(h) for h in dist))
        if (dist and cfg.deployer.enabled and helius_on) else None
    )

    # — Deployer / creator forensics —
    if dep_task is not None:
        s = await dep_task
        prior = s.get("prior_creations") or 0
        serial = prior >= cfg.deployer.serial_creator_min
        out["deployer"] = {
            "wallet": creator,
            "prior_creations": prior,
            "rugcheck_tokens": (rc_rep.creator_tokens if rc_rep else None),
            "funded_by": s.get("funder"),
            "age_days": s.get("age_days"),
            "serial": serial,
            "links": {"solscan": f"https://solscan.io/account/{creator}"},
        }
        if serial:
            out["flags"].append("deployer_serial_rugger")
    elif use_jup_dep:
        prior = jt.dev_mints
        serial = prior >= cfg.deployer.serial_creator_min
        out["deployer"] = {
            "wallet": creator,
            "prior_creations": prior,
            "rugcheck_tokens": (rc_rep.creator_tokens if rc_rep else None),
            "serial": serial,
            "source": "jupiter",  # counted by Jupiter — saved a 10cr Helius call (funding/age omitted)
            "links": {"solscan": f"https://solscan.io/account/{creator}"},
        }
        if serial:
            out["flags"].append("deployer_serial_rugger")
    elif creator:
        out["deployer"] = {"wallet": creator, "links": {"solscan": f"https://solscan.io/account/{creator}"}}
    else:
        out["deployer"] = {"wallet": None}

    # — dev current holdings → "sold" / "holds X%" (RugCheck creatorBalance vs on-chain supply, ADR-043) —
    if rc_rep and rc_rep.available and rc_rep.creator_balance is not None and out["deployer"].get("wallet"):
        supply = a.mint_info.supply if a.mint_info else 0
        pct = round(100 * rc_rep.creator_balance / supply, 2) if supply else None
        out["deployer"]["dev_holdings_pct"] = pct
        out["deployer"]["dev_sold"] = (rc_rep.creator_balance == 0)
        if pct is not None and pct > 15:
            out["flags"].append("dev_holds_large")

    # — Top buyers' wallet history (fresh-wallet / insider cluster) —
    if buyers_task is not None:
        rows, fresh, traders = [], 0, 0
        for h, s, f, t in await buyers_task:
            fresh += f
            traders += t
            rows.append({
                "owner": h.get("owner"), "pct": h.get("pct"), "fresh": f, "trader": t,
                "swaps": s["swaps_total"], "tokens": s["distinct_tokens"], "net_sol": s["net_sol"],
            })
        out["holders_intel"] = {"profiled": len(rows), "fresh": fresh, "traders": traders, "rows": rows}
        if fresh >= cfg.deployer.fresh_buyer_min:
            out["flags"].append("fresh_buyer_cluster")

    # — token overview + LP lock + markets (ADR-043: surface what RugCheck already gave us) —
    if rc_rep and rc_rep.available:
        supply = a.mint_info.supply if a.mint_info else None
        decimals = a.mint_info.decimals if a.mint_info else 0
        out["overview"] = {
            "supply": (supply / (10 ** decimals)) if supply and decimals else supply,
            "lp_locked_pct": rc_rep.lp_locked_pct,
            "total_lp_providers": rc_rep.total_lp_providers,
            "markets": rc_rep.markets_count,
            "total_liquidity": rc_rep.total_market_liquidity,
        }
        # only a soft nudge, and only when LP-lock was actually MEASURABLE and clearly low (ADR-043)
        if rc_rep.lp_locked_pct is not None and rc_rep.lp_locked_pct < 25:
            out["flags"].append("lp_unlocked")
        # — Insider Networks (named clusters — the distinctive Bubblemaps-style panel) —
        if rc_rep.insider_networks:
            tot = supply or 1
            out["insider_networks"] = [
                {"id": n["id"], "accounts": n["size"],
                 "pct": round(100 * n["token_amount"] / tot, 2) if tot else None}
                for n in rc_rep.insider_networks
            ]
        # — known-account labels for the holder bars (Pump.fun AMM, Streamflow Vault, Raydium, …) —
        if rc_rep.known_accounts:
            out["labels"] = {k: v for k, v in rc_rep.known_accounts.items() if v}

    # — Source consensus (3 independent reads: RugCheck + GoPlus + Jupiter, ADR-046) —
    out["sources"] = {
        "rugcheck": ({"available": True, "score": rc_rep.score, "rugged": rc_rep.rugged,
                      "lp_locked_pct": rc_rep.lp_locked_pct, "total_lp_providers": rc_rep.total_lp_providers,
                      "risks": [n for (n, lvl, _s) in rc_rep.risks if lvl == "danger"][:6]}
                     if rc_rep and rc_rep.available else {"available": False}),
        "goplus": ({"available": True, "trusted": gp_rep.trusted, "risks": gp_rep.risks,
                    "holder_count": gp_rep.holder_count, "lp_holders": gp_rep.lp_holders}
                   if gp_rep and gp_rep.available else {"available": False}),
        "jupiter": ({"available": True, "verified": jt.verified, "tags": jt.tags[:6],
                     "organic_score": jt.organic_score, "organic_label": jt.organic_label,
                     "holder_count": jt.holder_count, "dev_mints": jt.dev_mints,
                     "auth_consensus": auth_consensus}
                    if jt else {"available": False}),
    }
    return out
