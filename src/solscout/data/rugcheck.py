"""RugCheck.xyz client — FREE, no key. Aggregated third-party rug analysis (ADR-039).

The public token-report endpoint needs no key. It gives an independent risk read that complements our own
on-chain checks: an overall risk score, a list of named risks (incl. LP-lock status we don't compute), a
`rugged` flag, and INSIDER NETWORKS (their precomputed bundle/cluster graph — so we can skip our own
credit-spending Helius funder-trace when RugCheck has the answer). Treat as flaky: any error → no report,
and the funnel falls back to our on-chain signals (never blocks).

Docs: https://api.rugcheck.xyz/swagger/index.html
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import BaseClient

BASE = "https://api.rugcheck.xyz"


@dataclass
class RugCheckReport:
    mint: str
    available: bool = False
    rugged: bool = False
    score: int = 0  # score_normalised: 0..100, HIGHER = riskier (BONK≈7, a dead husk≈71)
    total_holders: int | None = None
    insider_holders: int = 0  # top holders RugCheck flagged as insiders
    lp_locked_pct: float | None = None
    creator: str | None = None  # deployer wallet (ADR-041) — for deep deployer forensics
    creator_tokens: int | None = None  # how many tokens this creator has launched (per RugCheck)
    risks: list[tuple[str, str, int]] = field(default_factory=list)  # (name, level, score)
    # ADR-043 — richer overview surface (data already in /report; we just surface it now)
    creator_balance: int | None = None  # dev's CURRENT token holdings (raw) → "dev sold" / "dev holds X%"
    total_lp_providers: int | None = None
    markets_count: int | None = None
    total_market_liquidity: float | None = None
    insider_networks: list[dict] = field(default_factory=list)  # [{id, size, token_amount}] — the Bubblemaps-style clusters
    known_accounts: dict[str, str] = field(default_factory=dict)  # address -> label (e.g. "Pump.fun AMM")

    def danger_risks(self) -> list[str]:
        return [n for (n, lvl, _s) in self.risks if lvl == "danger"]


class RugCheckClient(BaseClient):
    def __init__(self):
        # free + keyless; be polite (the public API is rate-limited). Cache: reports change slowly.
        super().__init__(BASE, timeout=20.0, min_interval_s=0.5, cache_ttl_s=120.0,
                         headers={"Accept": "application/json"})

    async def report(self, mint: str) -> RugCheckReport:
        try:
            d = await self.get_json(f"/v1/tokens/{mint}/report", cache_key=f"rc:{mint}")
        except Exception:
            return RugCheckReport(mint=mint, available=False)
        if not isinstance(d, dict):
            return RugCheckReport(mint=mint, available=False)
        top = d.get("topHolders") or []
        risks = [
            (str(r.get("name") or ""), str(r.get("level") or ""), int(r.get("score") or 0))
            for r in (d.get("risks") or [])
            if isinstance(r, dict)
        ]
        ctoks = d.get("creatorTokens")
        markets = d.get("markets") or []
        lp_top = _f(d.get("lpLockedPct"))
        # LP-lock is per-market; a SINGLE unlocked deep pool is the rug vector (dev pulls liquidity there), so
        # a naive max() ("best pool") would hide it. Compute a LIQUIDITY-WEIGHTED lock across pools instead —
        # an unlocked deep pool drags the number down. Fall back to the global field; `is not None` (not a
        # truthiness check) so a real 0% lock isn't silently overridden (security review).
        lp_weighted = _weighted_lp_lock(markets)
        lp_locked = lp_top if lp_top is not None else lp_weighted
        ka = d.get("knownAccounts") or {}
        return RugCheckReport(
            mint=mint,
            available=True,
            rugged=bool(d.get("rugged")),
            score=int(d.get("score_normalised") or 0),
            total_holders=_i(d.get("totalHolders")),
            insider_holders=sum(1 for h in top if isinstance(h, dict) and h.get("insider")),
            lp_locked_pct=lp_locked,
            creator=d.get("creator") or None,
            creator_tokens=(len(ctoks) if isinstance(ctoks, list) else _i(ctoks)),
            creator_balance=_i(d.get("creatorBalance")),
            total_lp_providers=_i(d.get("totalLPProviders")),
            markets_count=len(markets) or None,
            total_market_liquidity=_f(d.get("totalMarketLiquidity")),
            insider_networks=[
                {"id": str(n.get("id") or "?"), "size": _i(n.get("size")) or 0, "token_amount": _i(n.get("tokenAmount")) or 0}
                for n in (d.get("insiderNetworks") or []) if isinstance(n, dict)
            ][:8],
            known_accounts={k: (v.get("name") or v.get("type") or "") for k, v in ka.items()
                            if isinstance(v, dict)} if isinstance(ka, dict) else {},
            risks=risks,
        )


def _weighted_lp_lock(markets: list) -> float | None:
    """Liquidity-weighted LP-lock % across pools (ADR-043 security fix). Each pool's lock is weighted by its
    USD liquidity so a deep UNLOCKED pool drags the number down — a single rugged pool isn't hidden by a
    locked one (the security review's concern). IMPORTANT: many legit AMMs (Meteora/Orca CLMM) report
    lpLockedPct=0 AND lpMaxSupply=0 because they don't use a burn/lock script at all — that's "lock not
    measurable here", NOT "unlocked". We only count pools that actually expose lock data (lpLockedUSD>0 or a
    real lpMaxSupply), so BONK-style multi-AMM tokens aren't false-flagged. None when no pool is measurable.
    Pure → unit-tested."""
    num = den = 0.0
    measurable = False
    for m in markets:
        if not isinstance(m, dict):
            continue
        lp = m.get("lp") or {}
        pct = _f(lp.get("lpLockedPct"))
        liq = _f(lp.get("baseUSD")) or 0.0
        locked_usd = _f(lp.get("lpLockedUSD")) or 0.0
        max_supply = _f(lp.get("lpMaxSupply")) or 0.0
        if pct is None or liq <= 0:
            continue
        # skip pools with no lock telemetry (pct 0 AND no locked-USD AND no LP supply = "can't tell")
        if pct == 0 and locked_usd <= 0 and max_supply <= 0:
            continue
        measurable = True
        num += pct * liq
        den += liq
    if not measurable or den <= 0:
        return None
    return round(num / den, 2)


def flags(report: RugCheckReport, cfg) -> tuple[list[str], list[str]]:
    """(hard, soft) from a RugCheck report. Pure → unit-tested. Empty when the report is unavailable."""
    hard: list[str] = []
    soft: list[str] = []
    if not cfg.enabled or not report.available:
        return hard, soft
    if report.rugged:
        hard.append("rugcheck_rugged")
    if report.score >= cfg.veto_score:
        hard.append("rugcheck_high_risk")
    # specific critical, named danger-risks (substring match) — e.g. honeypot / can't-sell
    crit = [c.lower() for c in cfg.critical_risks]
    for name in report.danger_risks():
        low = name.lower()
        if any(c in low for c in crit):
            hard.append("rc_" + low.replace(" ", "_")[:24])
    if report.insider_holders >= cfg.max_insider_holders:
        hard.append("rugcheck_insiders")
    if not hard and report.score >= cfg.soft_score:
        soft.append("rugcheck_elevated")
    return list(dict.fromkeys(hard)), soft


def _i(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
