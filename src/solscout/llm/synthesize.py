"""Stage 4 — local Qwen analysis via Ollama. Returns FEATURES + a human COMMENTARY (never a trade order).

Graceful: if Ollama is unreachable/returns junk → None and the scorer drops the narrative weight.
`analyze_token` runs on the FULL signal set (market + on-chain + socials + chatter) so it produces a
commentary for every analyzed coin, not only when Telegram chatter exists.
"""

from __future__ import annotations

import json

from ..core.config import LlmCfg
from ..core.logging import get_logger
from ..core.models import LlmSynthesis

log = get_logger(__name__)

_SYSTEM = (
    "You are a skeptical but FAIR Solana meme-coin analyst. You get on-chain facts, market stats, and "
    "whatever social presence is available. Judge legitimacy and risk. IMPORTANT: most legit EARLY memes "
    "have little or no social footprint yet — do NOT treat missing socials or an unseen community as a scam "
    "by itself. If you have no community data, set community_authenticity to 0.5 (unknown), not low. Only put "
    "items in scam_language_flags for CONCRETE tells you can actually see — e.g. impersonating a known brand/"
    "ticker, 'guaranteed/1000x/presale/airdrop-dm-me', explicit fake-team claims. An ordinary meme name with "
    "no red phrase gets an EMPTY scam_language_flags. Return STRICT JSON: summary (<=70 words, blunt, cite the "
    "concrete signals), narrative_strength (0..1, how catchy/sticky the meme is), community_authenticity "
    "(0..1; 0.5 when unknown), scam_language_flags (array; [] if none), notable_mentions (array). Never say "
    "buy/sell or give financial advice. Output JSON only."
)


def _context_prompt(ctx: dict) -> str:
    lines = [f"Token: {ctx.get('name') or '?'} ({ctx.get('symbol') or '?'})"]
    oc = ctx.get("onchain") or {}
    lines.append(
        f"On-chain: mint_authority={oc.get('mint_authority') or 'renounced'}, "
        f"freeze_authority={oc.get('freeze_authority') or 'none'}, top10%={oc.get('top10_pct')}"
    )
    mk = ctx.get("market") or {}
    lines.append(
        f"Market: liquidity=${mk.get('liquidity_usd')}, 24h_vol=${mk.get('volume_24h')}, "
        f"mcap=${mk.get('market_cap')}, age_min={mk.get('age_min')}"
    )
    soc = ctx.get("socials") or []
    lines.append("Socials linked: " + (", ".join(soc) if soc else "none provided"))
    tw = ctx.get("twitter") or {}
    if tw.get("handle"):
        lines.append(
            f"Twitter: handle={tw.get('handle')}, age_days={tw.get('age_days')}, "
            f"followers={tw.get('followers')}"
        )
    chat = ctx.get("chatter") or []
    if chat:
        lines.append("Telegram/social messages:\n" + "\n".join(f"- {t}" for t in chat[:30]))
    else:
        lines.append(
            "No community data fetched (we have no social API key) — judge on name/market/on-chain only; "
            "treat community_authenticity as UNKNOWN (0.5), not fake."
        )
    return "\n".join(lines)


async def analyze_token(ctx: dict, cfg: LlmCfg) -> LlmSynthesis | None:
    """Rich per-coin analysis over the whole signal set. Always attempts (if Ollama reachable)."""
    try:
        import ollama
    except ImportError:
        return None
    try:
        client = ollama.AsyncClient(host=cfg.host)
        resp = await client.chat(
            model=cfg.synthesis_model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _context_prompt(ctx)},
            ],
            format="json",
            options={"temperature": 0.2},
        )
        raw = json.loads(resp["message"]["content"])
        return LlmSynthesis(
            summary=str(raw.get("summary", ""))[:600],
            narrative_strength=_unit(raw.get("narrative_strength")),
            community_authenticity=_unit(raw.get("community_authenticity")),
            scam_language_flags=[str(x) for x in (raw.get("scam_language_flags") or [])][:20],
            notable_mentions=[str(x) for x in (raw.get("notable_mentions") or [])][:20],
        )
    except Exception as e:
        log.warning("LLM analysis failed (%s); continuing rule-only", e)
        return None


async def synthesize(
    name: str | None, symbol: str | None, texts: list[str], cfg: LlmCfg
) -> LlmSynthesis | None:
    """Back-compat thin wrapper (chatter-only)."""
    if not texts:
        return None
    return await analyze_token({"name": name, "symbol": symbol, "chatter": texts}, cfg)


def _unit(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0
