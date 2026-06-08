"""GoPlus Security client — FREE, no key. A SECOND, independent rug source next to RugCheck (ADR-041).

`api.gopluslabs.io/api/v1/solana/token_security` returns an independent on-chain security read: mint/freeze/
close authorities, transfer-hook & non-transferable (Token-2022 honeypot vectors), transfer-fee (tax), a
`malicious_address` flag on creators / metadata authority (GoPlus's known-bad list), LP holders (lock),
holder count, and a `trusted_token` allow-list. Two independent sources = a consensus, not one point of
failure. Flaky third party → fail OPEN (no report → our own + RugCheck signals stand).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .base import BaseClient

BASE = "https://api.gopluslabs.io"


@dataclass
class GoPlusReport:
    mint: str
    available: bool = False
    trusted: bool = False  # GoPlus allow-list (e.g. USDC/established) → strong pass
    mint_authority: bool = False
    freeze_authority: bool = False
    closable: bool = False  # dev can close token accounts
    non_transferable: bool = False  # tokens can't move = honeypot
    transfer_hook: bool = False  # a hook program can block/tax transfers = honeypot vector
    transfer_fee_pct: float | None = None
    malicious_creator: bool = False  # GoPlus flagged a creator / metadata authority as malicious
    metadata_mutable: bool = False
    holder_count: int | None = None
    lp_holders: int | None = None
    dex_listed: bool = False
    risks: list[str] = field(default_factory=list)  # human-readable danger list


class GoPlusClient(BaseClient):
    def __init__(self):
        super().__init__(BASE, timeout=20.0, min_interval_s=0.4, cache_ttl_s=120.0,
                         headers={"accept": "application/json"})

    async def token_security(self, mint: str) -> GoPlusReport:
        try:
            d = await self.get_json(
                f"/api/v1/solana/token_security?contract_addresses={mint}", cache_key=f"gp:{mint}"
            )
        except Exception:
            return GoPlusReport(mint=mint, available=False)
        res = (d or {}).get("result") or {}
        v = res.get(mint) or (next(iter(res.values())) if isinstance(res, dict) and res else None)
        if not isinstance(v, dict):
            return GoPlusReport(mint=mint, available=False)
        return _parse(mint, v)


def _on(node) -> bool:
    """A GoPlus authority node {authority:[...],status:'0'|'1'} is ACTIVE when status=='1'."""
    return isinstance(node, dict) and str(node.get("status")) == "1"


def _malicious_in(node) -> bool:
    """Any address in a creators/authority list flagged malicious_address==1."""
    items = node if isinstance(node, list) else (node.get("metadata_upgrade_authority") if isinstance(node, dict) else None)
    for a in items or []:
        if isinstance(a, dict) and str(a.get("malicious_address")) == "1":
            return True
    return False


def _parse(mint: str, v: dict) -> GoPlusReport:
    risks: list[str] = []
    r = GoPlusReport(mint=mint, available=True)
    r.trusted = str(v.get("trusted_token")) == "1"
    r.mint_authority = _on(v.get("mintable"))
    r.freeze_authority = _on(v.get("freezable"))
    r.closable = _on(v.get("closable"))
    r.non_transferable = str(v.get("non_transferable")) == "1"
    th = v.get("transfer_hook")
    r.transfer_hook = (isinstance(th, list) and len(th) > 0) or _on(v.get("transfer_hook_upgradable"))
    r.metadata_mutable = _on((v.get("metadata_mutable") or {}))
    r.malicious_creator = _malicious_in(v.get("creators")) or _malicious_in(v.get("metadata_mutable"))
    tf = v.get("transfer_fee")
    try:
        r.transfer_fee_pct = float(tf) if tf not in (None, "", []) and not isinstance(tf, (list, dict)) else None
    except (TypeError, ValueError):
        r.transfer_fee_pct = None
    try:
        r.holder_count = int(v.get("holder_count")) if v.get("holder_count") is not None else None
    except (TypeError, ValueError):
        r.holder_count = None
    lp = v.get("lp_holders")
    r.lp_holders = len(lp) if isinstance(lp, list) else None
    r.dex_listed = bool(v.get("dex"))

    if r.mint_authority:
        risks.append("mint authority active")
    if r.freeze_authority:
        risks.append("freeze authority active")
    if r.non_transferable:
        risks.append("non-transferable (honeypot)")
    if r.transfer_hook:
        risks.append("transfer hook (can block sells)")
    if r.closable:
        risks.append("accounts closable by authority")
    if r.malicious_creator:
        risks.append("creator flagged malicious")
    r.risks = risks
    return r


def flags(report: GoPlusReport, cfg) -> tuple[list[str], list[str]]:
    """(hard, soft) from a GoPlus report. Pure → unit-tested. Empty when unavailable or trusted."""
    hard: list[str] = []
    soft: list[str] = []
    if not cfg.enabled or not report.available or report.trusted:
        return hard, soft
    if report.malicious_creator:
        hard.append("goplus_malicious_creator")
    if report.non_transferable:
        hard.append("goplus_non_transferable")
    if report.transfer_hook:
        hard.append("goplus_transfer_hook")
    if report.mint_authority:
        hard.append("goplus_mintable")
    if report.freeze_authority:
        hard.append("goplus_freezable")
    if report.transfer_fee_pct is not None and report.transfer_fee_pct > cfg.max_transfer_fee_pct:
        hard.append("goplus_high_transfer_fee")
    if report.closable:
        soft.append("goplus_closable")
    # NOTE: metadata_mutable is extremely common on Solana memecoins (low signal) → surfaced as an info check
    # in the report, but NOT a score penalty (it was wrongly dropping established coins like BONK to CAUTION).
    return list(dict.fromkeys(hard)), soft
