"""Analysis → SafetyReport (ADR-040). PURE (no I/O) → unit-tested.

Translates the trading-oriented `pipeline.Analysis` into a SECURITY product report: an overall 0..100 safety
score (100 = safest), a verdict level, and a broad, categorized list of named checks (the "geniş çaplı
kontrollü" surface). Every signal the engine computes is surfaced as a pass / warn / fail / skip row so the
user sees exactly what was inspected and why.
"""

from __future__ import annotations

from datetime import datetime, timezone

# flag → severity. CRITICAL = a rug / can't-sell / authority trap. DANGER = manipulation / coordinated.
CRITICAL_FLAGS = {
    "not_a_valid_spl_mint",
    "freeze_authority_active",
    "mint_authority_active",
    "honeypot_cannot_sell",
    "rugcheck_rugged",
    "single_wallet_dominant",
    "holder_concentration_extreme",
    "low_liquidity",
    "no_liquidity_data",
    # GoPlus (ADR-041) — independent 2nd source
    "goplus_malicious_creator",
    "goplus_non_transferable",
    "goplus_transfer_hook",
    "goplus_freezable",
    "goplus_mintable",
    "goplus_high_transfer_fee",
    # deep deployer/buyer forensics (ADR-041)
    "deployer_serial_rugger",
}
DANGER_FLAGS = {
    "wash_volume",
    "lopsided_flow",
    "hyper_pump",
    "bundled_holders",
    "rugcheck_high_risk",
    "rugcheck_insiders",
    "too_few_holders",
    "too_few_traders",
    "no_sellers",
    "pump_origin",
    "twitter_handle_reuse",
    "deployer_rugged_before",
    "deployer_fresh_funded",
    "fresh_buyer_cluster",
    "twitter_inauthentic",
    # timeline forensics (ADR-046): pumping many OTHER token CAs / forged identity metadata = active deception
    "twitter_serial_shill",
    "twitter_id_mismatch",
}
SOFT_FLAGS = {
    "high_turnover", "txn_imbalance", "low_float", "rugcheck_elevated", "llm_rug",
    "goplus_closable", "goplus_mutable_metadata", "twitter_weak", "no_twitter", "dev_holds_large",
    # LP-lock is informational only (ADR-043): many legit multi-AMM tokens (BONK) show low measured lock
    # because pools don't all use a lock script. RugCheck's aggregate score/`rugged` is the real LP authority,
    # so an unlocked-LP reading is a soft nudge, never a hard DANGER veto that overrides a clean RugCheck.
    "lp_unlocked",
    # Jupiter consensus (ADR-046): their organic detector disagreeing, or sources disagreeing about
    # authorities, is a nudge — our own deterministic checks remain the hard authority.
    "jupiter_low_organic", "authority_consensus_mismatch",
    # timeline heuristics (ADR-046): suggestive, not proof
    "twitter_no_ca_mention", "twitter_site_mismatch", "twitter_burst_posting",
    "website_brand_new",
}

LEVELS = ("SAFE", "CAUTION", "DANGER", "CRITICAL")


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _usd(v) -> str:
    if v is None:
        return "—"
    if v < 1:
        return f"${v:.8f}".rstrip("0").rstrip(".")
    if v < 1000:
        return f"${v:,.2f}"
    return f"${v:,.0f}"


def build_report(a, cfg, *, osint: dict | None = None, took_ms: int | None = None) -> dict:
    """Build the SafetyReport dict from a pipeline.Analysis (+ optional deep OSINT). Graceful on None subs."""
    filt = a.filt
    market = a.market
    mi = a.mint_info
    dec = a.decision
    osint = osint or {}
    metrics = filt.metrics or {}
    all_flags = set(filt.hard_flags or []) | set(dec.veto_flags or []) | set(osint.get("flags") or [])

    checks = _build_checks(a, cfg, all_flags, osint)
    score, level = _score_and_level(filt, metrics, all_flags)
    summary = _summary(level, all_flags, score)

    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    passes = [c for c in checks if c["status"] == "pass"]

    return {
        "mint": a.mint,
        "ready": a.ready,
        "score": round(score),
        "level": level,
        "summary": summary,
        "counts": {"fail": len(fails), "warn": len(warns), "pass": len(passes), "total": len(checks)},
        "token": {
            "name": (market.name if market else None),
            "symbol": (market.symbol if market else None),
            "dex": (market.dex if market else None),
            "socials": ([{"type": s.type, "url": s.url} for s in market.socials] if market else []),
            "websites": (list(market.websites) if market else []),
            "links": {
                "dexscreener": f"https://dexscreener.com/solana/{a.mint}",
                "solscan": f"https://solscan.io/token/{a.mint}",
                "bubblemaps": f"https://app.bubblemaps.io/sol/token/{a.mint}",
                "rugcheck": f"https://rugcheck.xyz/tokens/{a.mint}",
            },
        },
        "checks": checks,
        "market": _market_block(market, metrics),
        "holders": _holders_block(a, mi),
        "flow": {
            "buyers_h24": (market.buyers_h24 if market else None),
            "sellers_h24": (market.sellers_h24 if market else None),
            "txns_buys_h24": (market.txns_buys_h24 if market else None),
            "txns_sells_h24": (market.txns_sells_h24 if market else None),
        },
        "rugcheck": {
            "score": metrics.get("rugcheck_score"),
            "available": metrics.get("rugcheck_score") is not None,
        },
        "honeypot": {
            "simulated": filt.sell_simulated,
            "sell_ok": filt.sell_ok,
            "round_trip_tax_pct": filt.round_trip_tax_pct,
        },
        "ai": (
            {
                "summary": a.llm.summary,
                "narrative_strength": a.llm.narrative_strength,
                "community_authenticity": a.llm.community_authenticity,
                "scam_flags": list(a.llm.scam_language_flags),
            }
            if a.llm
            else None
        ),
        # deep OSINT (ADR-041) — the project's Twitter, the deployer's wallet, the buyers' wallets, consensus
        "twitter": osint.get("twitter"),
        "website": osint.get("website"),
        "deployer": osint.get("deployer"),
        "holders_intel": osint.get("holders_intel"),
        "sources": osint.get("sources"),
        "goplus": metrics.get("goplus"),
        # ADR-043 — richer overview surface (LP lock, dev holdings, markets) + named insider networks
        "overview": osint.get("overview"),
        "insider_networks": osint.get("insider_networks"),
        "labels": osint.get("labels"),
        "meta": {
            "analyzed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "took_ms": took_ms,
            "engine": "solscout/free",
        },
    }


def _score_and_level(filt, metrics, all_flags) -> tuple[float, str]:
    crit = all_flags & CRITICAL_FLAGS
    dang = all_flags & DANGER_FLAGS
    soft = all_flags & SOFT_FLAGS
    if crit:
        return _clamp(16 - 4 * len(crit), 2, 16), "CRITICAL"
    if dang:
        return _clamp(44 - 7 * len(dang), 18, 44), "DANGER"
    base = (filt.safety_score or 0.0) * 100
    rc = metrics.get("rugcheck_score")
    score = 0.6 * base + 0.4 * (100 - rc) if rc is not None else base
    score -= 8 * len(soft)
    score = _clamp(score)
    level = "SAFE" if score >= 72 else ("CAUTION" if score >= 48 else "DANGER")
    return score, level


def _summary(level: str, all_flags, score: float) -> str:
    crit = sorted(all_flags & CRITICAL_FLAGS)
    dang = sorted(all_flags & DANGER_FLAGS)
    if level == "CRITICAL":
        return f"Critical risk — {', '.join(_pretty(f) for f in crit[:3])}. Treat as a likely rug/honeypot."
    if level == "DANGER":
        return f"High risk — {', '.join(_pretty(f) for f in dang[:3])}. Avoid unless you know exactly why."
    if level == "CAUTION":
        return "Some risk signals present. Read the checks below before trusting this token."
    return "No hard risk signals found across our free on-chain + external checks. Still DYOR."


def _pretty(flag: str) -> str:
    return flag.replace("rc_", "").replace("_", " ")


def _check(cid, label, category, status, detail) -> dict:
    return {"id": cid, "label": label, "category": category, "status": status, "detail": detail}


def _build_checks(a, cfg, all_flags, osint=None) -> list[dict]:
    filt, market, mi = a.filt, a.market, a.mint_info
    metrics = filt.metrics or {}
    osint = osint or {}
    out: list[dict] = []
    has = lambda f: f in all_flags  # noqa: E731

    # — Authorities (on-chain, certain) —
    if mi is not None:
        out.append(_check("mint_auth", "Mint authority renounced", "Authorities",
                          "fail" if mi.mint_authority else "pass",
                          "Dev can mint infinite supply" if mi.mint_authority else "Cannot mint new supply"))
        out.append(_check("freeze_auth", "Freeze authority renounced", "Authorities",
                          "fail" if mi.freeze_authority else "pass",
                          "Dev can FREEZE your tokens (honeypot)" if mi.freeze_authority else "Cannot freeze holders"))
    else:
        out.append(_check("mint_valid", "Valid SPL mint", "Authorities", "fail", "Not a readable SPL mint"))

    # — Liquidity —
    liq = metrics.get("liquidity_usd")
    if has("no_liquidity_data"):
        out.append(_check("liq", "DEX liquidity present", "Liquidity", "fail", "No DEX liquidity / not indexed"))
    elif liq is not None:
        out.append(_check("liq", "Liquidity above floor", "Liquidity",
                          "fail" if has("low_liquidity") else "pass",
                          f"{_usd(liq)} (floor {_usd(cfg.filters.min_liquidity_usd)})"))

    # — Holders / concentration —
    t10, t1 = metrics.get("top10_pct"), metrics.get("top1_pct")
    if t10 is not None:
        out.append(_check("conc10", "Top-10 concentration", "Holders",
                          "fail" if has("holder_concentration_extreme") else ("warn" if t10 > cfg.filters.max_top10_holder_pct else "pass"),
                          f"Top 10 hold {t10:.1f}% (extreme ≥ {cfg.filters.extreme_top10_pct:.0f}%)"))
    if t1 is not None:
        out.append(_check("conc1", "No single dominant wallet", "Holders",
                          "fail" if has("single_wallet_dominant") else "pass",
                          f"Largest non-infra wallet {t1:.1f}% (max {cfg.filters.single_wallet_max_pct:.0f}%)"))
    if a.holder_count is not None:
        out.append(_check("holders", "Enough holders", "Holders",
                          "fail" if has("too_few_holders") else "pass",
                          f"{a.holder_count} non-infra holders (min {cfg.manipulation.min_holders})"))

    # — Activity / two-sided flow —
    if market and market.buyers_h24 is not None and market.sellers_h24 is not None:
        traders = market.buyers_h24 + market.sellers_h24
        out.append(_check("traders", "Active trading", "Activity",
                          "fail" if has("too_few_traders") else "pass",
                          f"{traders} unique traders 24h (min {cfg.manipulation.min_traders})"))
        out.append(_check("sellers", "Token is sellable (has sellers)", "Activity",
                          "fail" if has("no_sellers") else "pass",
                          f"{market.sellers_h24} sellers / {market.buyers_h24} buyers 24h"))

    # — Manipulation —
    out.append(_check("wash", "No wash-trading", "Manipulation",
                      "fail" if has("wash_volume") else ("warn" if has("high_turnover") else "pass"),
                      "Volume vastly exceeds pool size" if (has("wash_volume") or has("high_turnover")) else "Turnover looks organic"))
    out.append(_check("lopsided", "Balanced buy/sell flow", "Manipulation",
                      "fail" if has("lopsided_flow") else "pass",
                      "Almost everyone buying, ~nobody selling" if has("lopsided_flow") else "Two-sided flow"))
    out.append(_check("pump", "No vertical hyper-pump", "Manipulation",
                      "fail" if has("hyper_pump") else "pass",
                      "Young + vertical on a thin pool" if has("hyper_pump") else "No manipulation-shaped spike"))
    if has("txn_imbalance"):
        out.append(_check("txn", "Txn buy/sell balance", "Manipulation", "warn", "Far more buy than sell txns"))
    if has("low_float"):
        out.append(_check("float", "Healthy circulating float", "Manipulation", "warn", "Little supply outside the pool"))

    # — Bundle / insiders —
    out.append(_check("bundle", "No sybil/bundle cluster", "Bundle & Insiders",
                      "fail" if has("bundled_holders") else "pass",
                      "Top holders share one funder (coordinated)" if has("bundled_holders") else "No shared-funder bundle detected"))
    out.append(_check("insiders", "No flagged insiders (RugCheck)", "Bundle & Insiders",
                      "fail" if has("rugcheck_insiders") else "pass",
                      "RugCheck flagged insider top-holders" if has("rugcheck_insiders") else "No insider cluster flagged"))
    if a.smart and a.smart.deployer_rugged_before:
        out.append(_check("deployer", "Deployer has no prior rug", "Bundle & Insiders", "fail", "Creator wallet rugged before"))

    # — External: RugCheck —
    rc = metrics.get("rugcheck_score")
    if rc is not None:
        out.append(_check("rc_score", "RugCheck risk score", "External (RugCheck)",
                          "fail" if (has("rugcheck_high_risk") or has("rugcheck_rugged")) else ("warn" if has("rugcheck_elevated") else "pass"),
                          f"{rc}/100 risk (lower = safer)"))
        if has("rugcheck_rugged"):
            out.append(_check("rc_rugged", "RugCheck 'rugged' flag", "External (RugCheck)", "fail", "RugCheck marks this token rugged"))
        for f in sorted(x for x in all_flags if x.startswith("rc_") and x not in ("rc_score",)):
            out.append(_check(f, f"RugCheck: {_pretty(f)}", "External (RugCheck)", "fail", "Named danger risk"))
    else:
        out.append(_check("rc_score", "RugCheck report", "External (RugCheck)", "skip", "No RugCheck report (degraded)"))

    # — Honeypot sell-sim —
    if filt.sell_simulated:
        out.append(_check("honeypot", "Sell simulation (Jupiter)", "Honeypot",
                          "pass" if filt.sell_ok else "fail",
                          f"Round-trip tax {filt.round_trip_tax_pct:.1f}%" if filt.round_trip_tax_pct is not None else ("Sellable" if filt.sell_ok else "Could not sell")))
    else:
        out.append(_check("honeypot", "Sell simulation (Jupiter)", "Honeypot", "skip", "Not simulated (off by default)"))

    # — External: GoPlus (independent 2nd source, ADR-041) —
    gp = metrics.get("goplus")
    if gp:
        gp_fails = [f for f in all_flags if f.startswith("goplus_")]
        if gp.get("trusted"):
            out.append(_check("gp", "GoPlus Security", "External (GoPlus)", "pass", "On GoPlus's trusted allow-list"))
        else:
            out.append(_check("gp", "GoPlus Security", "External (GoPlus)",
                              "fail" if gp_fails else "pass",
                              (", ".join(gp.get("risks") or []) if gp.get("risks") else "No GoPlus risks flagged")))
        if gp.get("transfer_hook"):
            out.append(_check("gp_hook", "No transfer-hook (Token-2022)", "External (GoPlus)", "fail", "A hook program can block sells"))
        if gp.get("non_transferable"):
            out.append(_check("gp_nt", "Token is transferable", "External (GoPlus)", "fail", "Marked non-transferable = honeypot"))
        if gp.get("malicious_creator"):
            out.append(_check("gp_mal", "Creator not flagged malicious", "External (GoPlus)", "fail", "GoPlus flagged the creator/authority"))

    # — External: Jupiter (independent 3rd source, ADR-046) —
    jp = ((osint.get("sources") or {}).get("jupiter") or {})
    if jp.get("available"):
        lab = (jp.get("organic_label") or "").lower()
        if lab:
            sc = jp.get("organic_score")
            sc_txt = f"organicScore {sc:.0f} ({lab})" if sc is not None else f"organic activity: {lab}"
            corro = (" — corroborates our wash-trade detection" if (lab == "low" and has("wash_volume"))
                     else (" — Jupiter's detector flags inorganic flow we didn't" if lab == "low" else ""))
            out.append(_check("jup_organic", "Jupiter organic-activity score", "External (Jupiter)",
                              "warn" if lab == "low" else "pass", sc_txt + corro))
        tags = jp.get("tags") or []
        out.append(_check("jup_verified", "Jupiter verified / community list", "External (Jupiter)",
                          "pass" if jp.get("verified") else "info",
                          (", ".join(tags) if tags else
                           ("Verified on Jupiter" if jp.get("verified")
                            else "Not on Jupiter's verified list (normal for new tokens)"))))
        if jp.get("auth_consensus"):
            out.append(_check("jup_auth", "Authority consensus (RPC vs Jupiter)", "External (Jupiter)",
                              "warn" if has("authority_consensus_mismatch") else "pass",
                              ("Sources DISAGREE about mint/freeze authority — treat the data as suspect"
                               if has("authority_consensus_mismatch")
                               else "Independent sources agree on the authorities")))

    # — Deployer / creator (ADR-041) —
    dep = (osint.get("deployer") or {})
    if dep.get("wallet"):
        prior = dep.get("prior_creations")
        out.append(_check("dep_serial", "Creator is not a serial deployer", "Creator / deployer",
                          "fail" if has("deployer_serial_rugger") else ("pass" if prior is not None else "info"),
                          (f"Creator launched ~{prior} prior tokens" if prior is not None else "Creator wallet identified")))
        if dep.get("age_days") is not None:
            out.append(_check("dep_age", "Creator wallet has history", "Creator / deployer",
                              "warn" if dep["age_days"] < 7 else "pass",
                              f"Creator wallet ~{dep['age_days']}d old"))
        if dep.get("dev_holdings_pct") is not None:
            pct = dep["dev_holdings_pct"]
            detail = ("Dev wallet holds none of the supply" if pct < 0.01
                      else f"Dev still holds {pct}% of supply")
            out.append(_check("dev_hold", "Dev not over-holding supply", "Creator / deployer",
                              "warn" if has("dev_holds_large") else "pass", detail))

    # — LP lock (ADR-043) — informational; warn (not fail) on a measurably-low lock, skip when unmeasurable —
    ov = (osint.get("overview") or {})
    if ov.get("lp_locked_pct") is not None:
        lp = ov["lp_locked_pct"]
        provs = f" · {ov.get('total_lp_providers')} LP providers" if ov.get("total_lp_providers") else ""
        out.append(_check("lp_lock", "Liquidity lock", "Liquidity",
                          "warn" if has("lp_unlocked") else "pass",
                          f"LP locked {lp:.1f}% (measured across pools){provs}"))

    # — Buyers' wallets (ADR-041) —
    hi = (osint.get("holders_intel") or {})
    if hi.get("profiled"):
        out.append(_check("fresh_buyers", "Top buyers aren't fresh sybils", "Buyers (wallet history)",
                          "fail" if has("fresh_buyer_cluster") else "pass",
                          f"{hi.get('fresh', 0)} fresh / {hi.get('traders', 0)} real-trader of top {hi['profiled']} holders"))

    # — Social / AI —
    if a.llm:
        out.append(_check("ai_rug", "AI scam-language scan (Qwen)", "Social & AI",
                          "fail" if has("llm_rug") else "pass",
                          (", ".join(a.llm.scam_language_flags[:3]) if a.llm.scam_language_flags else "No scam-language tells")))
    tw = (osint.get("twitter") or {})
    if tw.get("available"):
        v = tw.get("verdict")
        out.append(_check("tw", "Twitter/X identity credible", "Social & AI",
                          "fail" if has("twitter_inauthentic") else ("warn" if v in ("weak",) else "pass"),
                          f"@{tw.get('handle')} · {_tw_age(tw.get('age_days'))} · {_compact(tw.get('followers'))} followers"
                          + (" · verified" if tw.get("verified") else "")))
        # — timeline forensics (ADR-046) — only when the timeline was actually readable —
        tl = tw.get("timeline") or {}
        if tl.get("count"):
            oc = tl.get("other_ca_count", 0)
            out.append(_check("tw_shill", "Account isn't a serial token-shiller", "Social & AI",
                              "fail" if has("twitter_serial_shill") else "pass",
                              (f"Recent tweets push {oc} OTHER token CAs — pumps token after token"
                               if has("twitter_serial_shill")
                               else f"{oc} other token CAs in recent tweets")))
            mm = tl.get("mint_mentions", 0)
            out.append(_check("tw_ca", "Account actually posted this token", "Social & AI",
                              "warn" if has("twitter_no_ca_mention") else "pass",
                              (f"This mint never appears in {tl['count']} recent tweets — account may be unrelated/hijacked"
                               if has("twitter_no_ca_mention")
                               else f"Mentions this mint {mm}× in recent tweets" if mm
                               else "Not in recent tweets (normal for an established token)")))
            if tl.get("burst_max_1h") is not None:
                out.append(_check("tw_cadence", "Posting cadence looks human", "Social & AI",
                                  "warn" if has("twitter_burst_posting") else "pass",
                                  (f"{tl['burst_max_1h']} tweets inside one hour — bot-like burst"
                                   if has("twitter_burst_posting")
                                   else f"~{tl.get('per_day') or '?'} tweets/day · last {tl.get('last_tweet_age_days', '?')}d ago")))
        if tw.get("id_joined_mismatch_days") is not None:
            out.append(_check("tw_id", "Account creation date checks out", "Social & AI",
                              "fail" if has("twitter_id_mismatch") else "pass",
                              (f"Snowflake-ID date differs from the claimed join date by {tw['id_joined_mismatch_days']}d — forged/recycled identity"
                               if has("twitter_id_mismatch")
                               else "ID-derived creation date matches the profile")))
        if tw.get("website_match") is not None:
            out.append(_check("tw_site", "Bio website matches the token's site", "Social & AI",
                              "warn" if has("twitter_site_mismatch") else "pass",
                              ("X bio links a DIFFERENT site than the token lists — possibly someone else's account"
                               if has("twitter_site_mismatch") else "Same website on both sides")))
    elif tw.get("linked") is False:
        out.append(_check("tw", "Twitter/X linked", "Social & AI", "warn", "No Twitter/X account linked on DexScreener"))
    if a.social and a.social.handle_reuse_count:
        out.append(_check("handle", "Twitter handle not reused", "Social & AI",
                          "fail" if has("twitter_handle_reuse") else "pass",
                          f"Handle attached to {a.social.handle_reuse_count} other tokens"))

    # — project website domain age (RDAP, ADR-046) —
    ws = (osint.get("website") or {})
    if ws.get("domain"):
        age = ws.get("age_days")
        out.append(_check("site_age", "Project website isn't a throwaway", "Social & AI",
                          "warn" if has("website_brand_new") else ("pass" if age is not None else "skip"),
                          (f"{ws['domain']} registered only {age}d ago — throwaway-domain tell"
                           if has("website_brand_new")
                           else (f"{ws['domain']} registered {_dom_age(age)} ago" if age is not None
                                 else f"{ws['domain']} — registry didn't answer (RDAP)"))))

    return out


def _dom_age(d):
    return f"{d // 365}y" if d >= 365 else f"{d}d"


def _tw_age(d):
    if d is None:
        return "age unknown"
    return f"{d // 365}y old" if d >= 365 else (f"{d}d old")


def _compact(v):
    if v is None:
        return "?"
    if v < 1000:
        return str(v)
    if v < 1_000_000:
        return f"{v / 1000:.1f}K"
    return f"{v / 1_000_000:.1f}M"


def _market_block(market, metrics) -> dict:
    if not market:
        return {}
    age_min = None
    if market.pair_created_at:
        age_min = max(0.0, (datetime.now(timezone.utc) - market.pair_created_at).total_seconds() / 60)
    return {
        "price_usd": market.price_usd,
        "liquidity_usd": market.liquidity_usd,
        "market_cap": market.market_cap,
        "fdv": market.fdv,
        "volume_24h": market.volume_24h,
        "price_change_h24": market.price_change_h24,
        "age_minutes": round(age_min) if age_min is not None else None,
        "dex": market.dex,
    }


def _holders_block(a, mi) -> dict:
    return {
        "count": a.holder_count,
        "top1_pct": (mi.top1_pct if mi else None),
        "top10_pct": (mi.top10_pct if mi else None),
        "distribution": list(a.top_holders or []),
    }
