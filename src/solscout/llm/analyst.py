"""Web AI analyst (ADR-042) — a local-LLM verdict that reasons over the WHOLE forensic report.

The old commentary was generic ("has basic social links but lacks engagement") because the model only saw
the token name + market + socials — never the actual findings. This analyst feeds the model EVERYTHING the
engine computed (score, level, every failed/warn check, deployer history, fresh-buyer cluster, RugCheck +
GoPlus risks, Twitter authenticity, holder concentration) and asks for a verdict that CITES those concrete
findings. Free + local (Ollama). Tries a bigger model first (`analyst_model`), falls back to
`synthesis_model`, then to a deterministic rule-built summary if Ollama is unreachable — never blocks.

Pure helpers (`_facts`, `_fallback_summary`, `doakes_line`) are unit-tested; the network call is best-effort.
"""

from __future__ import annotations

from ..core.logging import get_logger

log = get_logger("solscout.llm.analyst")

_SYSTEM = (
    "You are a sharp, blunt Solana token security analyst. You are given the CONCRETE findings of an "
    "automated forensic scan (a 0-100 safety score, a verdict level, the exact checks that failed or warned, "
    "the deployer's wallet history, the buyers' wallet history, two independent rug databases, and the "
    "project's Twitter). Write a SHORT verdict (2-4 sentences) that explicitly CITES the specific findings — "
    "name the actual red flags or the actual reasons it looks clean. Be direct and concrete; never generic. "
    "Do NOT invent facts not in the findings. Do NOT give financial advice or say buy/sell. End with one "
    "blunt bottom-line sentence. Plain text, no markdown, no preamble."
)

# Deterministic in-character one-liners (James Doakes — "I see the real you"). Always present, even if the
# LLM is down; the analyst paragraph carries the analysis, these carry the humor (ADR-042).
_DOAKES = {
    "CRITICAL": "Surprise, motherf*****. I see the real you — this one's a trap.",
    "DANGER": "I'm watching you, and you're up to something. Don't.",
    "CAUTION": "Something's off about you. I've got my eye on this one.",
    "SAFE": "...Fine. You check out. For now. I'm still watching.",
}


def doakes_line(level: str) -> str:
    return _DOAKES.get(level, _DOAKES["CAUTION"])


def _checks_by(report: dict, status: str) -> list[str]:
    return [f"{c['label']} — {c['detail']}" for c in (report.get("checks") or []) if c.get("status") == status]


def _facts(report: dict) -> str:
    """Compact, information-dense findings block fed to the model. Pure."""
    tok = report.get("token") or {}
    mk = report.get("market") or {}
    h = report.get("holders") or {}
    tw = report.get("twitter") or {}
    dep = report.get("deployer") or {}
    hi = report.get("holders_intel") or {}
    src = report.get("sources") or {}
    rc = (src.get("rugcheck") or {})
    gp = (src.get("goplus") or {})

    lines = [
        f"TOKEN: {tok.get('name') or '?'} ({tok.get('symbol') or '?'}) on {tok.get('dex') or '?'}",
        f"VERDICT: {report.get('level')} · safety score {report.get('score')}/100 · "
        f"{report.get('counts', {}).get('fail', 0)} failed / {report.get('counts', {}).get('warn', 0)} warning checks",
    ]
    fails = _checks_by(report, "fail")
    warns = _checks_by(report, "warn")
    if fails:
        lines.append("FAILED CHECKS:\n" + "\n".join(f"  - {f}" for f in fails))
    if warns:
        lines.append("WARNINGS:\n" + "\n".join(f"  - {w}" for w in warns))
    if not fails and not warns:
        lines.append("No hard red flags fired across the on-chain + external checks.")

    lines.append(
        "MARKET: liquidity ${:,.0f} · mcap ${:,.0f} · 24h vol ${:,.0f} · age {}min".format(
            mk.get("liquidity_usd") or 0, mk.get("market_cap") or 0, mk.get("volume_24h") or 0,
            mk.get("age_minutes") if mk.get("age_minutes") is not None else "?",
        )
    )
    if h.get("count") is not None or h.get("top10_pct") is not None:
        lines.append(
            f"HOLDERS: {h.get('count', '?')} holders · top wallet {h.get('top1_pct', '?')}% · top-10 {h.get('top10_pct', '?')}%"
        )
    flow = report.get("flow") or {}
    if flow.get("buyers_h24") is not None:
        lines.append(f"FLOW (24h): {flow.get('buyers_h24')} buyers / {flow.get('sellers_h24')} sellers")
    if tw.get("available"):
        lines.append(
            f"TWITTER: @{tw.get('handle')} · {tw.get('followers')} followers · age {tw.get('age_days')}d · "
            f"verified={tw.get('verified')} · authenticity verdict '{tw.get('verdict')}'"
        )
    elif tw.get("linked") is False:
        lines.append("TWITTER: no account linked on DexScreener.")
    if dep.get("wallet"):
        lines.append(
            f"DEPLOYER: prior token launches {dep.get('prior_creations', '?')} · wallet age {dep.get('age_days', '?')}d · "
            f"serial_deployer={dep.get('serial', False)}"
        )
    if hi.get("profiled"):
        lines.append(f"TOP BUYERS: {hi.get('fresh')} of {hi.get('profiled')} are fresh (near-empty) wallets")
    if rc.get("available"):
        lines.append(f"RUGCHECK.XYZ: risk {rc.get('score')}/100 · rugged={rc.get('rugged')} · risks {rc.get('risks') or 'none'}")
    if gp.get("available"):
        lines.append(f"GOPLUS: trusted={gp.get('trusted')} · risks {gp.get('risks') or 'none'}")
    return "\n".join(lines)


def _fallback_summary(report: dict) -> str:
    """Deterministic verdict when the LLM is unreachable — still cites the real findings. Pure."""
    fails = _checks_by(report, "fail")
    level = report.get("level")
    if fails:
        head = "Failed: " + "; ".join(f.split(" — ")[0] for f in fails[:4])
        return f"{head}. Verdict {level} ({report.get('score')}/100) — treat the red flags above as disqualifying."
    warns = _checks_by(report, "warn")
    if warns:
        return (
            f"No hard fails, but warnings: {', '.join(w.split(' — ')[0] for w in warns[:4])}. "
            f"Verdict {level} ({report.get('score')}/100) — proceed only if you understand each."
        )
    return f"No red flags fired across our free on-chain + external checks. Verdict {level} ({report.get('score')}/100). Still DYOR."


async def analyze_report(report: dict, cfg) -> dict:
    """Best-effort local-LLM verdict over the full report. Returns {summary, provider, model, doakes}."""
    level = report.get("level") or "CAUTION"
    out = {"summary": _fallback_summary(report), "provider": "rules", "model": None, "doakes": doakes_line(level)}
    facts = _facts(report)
    try:
        import ollama
    except ImportError:
        return out
    client = ollama.AsyncClient(host=cfg.host)
    for model in [cfg.analyst_model, cfg.synthesis_model]:
        if not model:
            continue
        try:
            resp = await client.chat(
                model=model,
                messages=[{"role": "system", "content": _SYSTEM},
                          {"role": "user", "content": facts}],
                options={"temperature": 0.3},
            )
            text = (resp.get("message") or {}).get("content", "").strip()
            if text:
                out.update(summary=text[:1200], provider="ollama", model=model)
                return out
        except Exception as e:
            log.warning("analyst model %s failed (%s); trying fallback", model, e)
            continue
    return out
