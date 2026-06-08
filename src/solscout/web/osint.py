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


async def gather(a, cfg, *, rc=None, gp=None, helius=None, tw=None) -> dict:
    out: dict = {"flags": []}
    helius_on = getattr(helius, "available", False)

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

    # — Deployer / creator forensics —
    if creator and cfg.deployer.enabled and helius_on:
        txs = await _safe(helius.address_transactions(creator, limit=cfg.deployer.tx_limit), "deployer-txns") or []
        prior = count_prior_creations(txs)
        serial = prior >= cfg.deployer.serial_creator_min
        out["deployer"] = {
            "wallet": creator,
            "prior_creations": prior,
            "rugcheck_tokens": (rc_rep.creator_tokens if rc_rep else None),
            "funded_by": dominant_funder(txs, creator),
            "age_days": first_tx_age_days(txs),
            "serial": serial,
            "links": {"solscan": f"https://solscan.io/account/{creator}"},
        }
        if serial:
            out["flags"].append("deployer_serial_rugger")
    elif creator:
        out["deployer"] = {"wallet": creator, "links": {"solscan": f"https://solscan.io/account/{creator}"}}
    else:
        out["deployer"] = {"wallet": None}

    # — Top buyers' wallet history (fresh-wallet / insider cluster) —
    dist = (a.top_holders or [])[: cfg.deployer.holders_intel_top_n]
    if dist and cfg.deployer.enabled and helius_on:
        rows, fresh, traders = [], 0, 0
        for h in dist:
            owner = h.get("owner")
            txs = await _safe(helius.address_transactions(owner, limit=cfg.deployer.holder_tx_limit), "holder-txns") or []
            s = wallet_swap_summary(txs)
            f, t = is_fresh_wallet(s), looks_like_trader(s)
            fresh += f
            traders += t
            rows.append({
                "owner": owner, "pct": h.get("pct"), "fresh": f, "trader": t,
                "swaps": s["swaps_total"], "tokens": s["distinct_tokens"], "net_sol": s["net_sol"],
            })
        out["holders_intel"] = {"profiled": len(rows), "fresh": fresh, "traders": traders, "rows": rows}
        if fresh >= cfg.deployer.fresh_buyer_min:
            out["flags"].append("fresh_buyer_cluster")

    # — Source consensus —
    out["sources"] = {
        "rugcheck": ({"available": True, "score": rc_rep.score, "rugged": rc_rep.rugged,
                      "risks": [n for (n, lvl, _s) in rc_rep.risks if lvl == "danger"][:6]}
                     if rc_rep and rc_rep.available else {"available": False}),
        "goplus": ({"available": True, "trusted": gp_rep.trusted, "risks": gp_rep.risks,
                    "holder_count": gp_rep.holder_count, "lp_holders": gp_rep.lp_holders}
                   if gp_rep and gp_rep.available else {"available": False}),
    }
    return out
