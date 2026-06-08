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
        return RugCheckReport(
            mint=mint,
            available=True,
            rugged=bool(d.get("rugged")),
            score=int(d.get("score_normalised") or 0),
            total_holders=_i(d.get("totalHolders")),
            insider_holders=sum(1 for h in top if isinstance(h, dict) and h.get("insider")),
            lp_locked_pct=_f(d.get("lpLockedPct")),
            creator=d.get("creator") or None,
            creator_tokens=(len(ctoks) if isinstance(ctoks, list) else _i(ctoks)),
            risks=risks,
        )


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
