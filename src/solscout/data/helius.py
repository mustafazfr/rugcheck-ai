"""Helius client — the reliable paths on the free tier:

  - token_holders()        : DAS `getTokenAccounts` by mint (indexed; owner+amount). Note: a single page
                             is NOT amount-sorted, so concentration is trustworthy only when we got the
                             FULL holder set (count < page_size) — true for fresh/small tokens, our target.
  - address_transactions() : enhanced tx history for a wallet — basis for smart-money PnL & deployer rep.

(`getTokenLargestAccounts` is intentionally avoided: it returns "account index overloaded" on big tokens
and "not a Token mint" on seconds-old ones.)
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable

from ..core.credits import CreditGovernor
from .base import BaseClient

RPC = "https://mainnet.helius-rpc.com"
API = "https://api.helius.xyz"
# Many new Solana mints are Token-2022. getProgramAccounts(memcmp mint) is ~10x cheaper than DAS
# getTokenAccounts and works on any full RPC node (provider-agnostic); DAS is the fallback for large
# tokens that gPA rejects (classic SPL or huge holder sets).
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
TOKEN_CLASSIC = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


class HeliusClient(BaseClient):
    def __init__(
        self,
        api_key: str,
        cache_ttl_s: float = 600.0,
        governor: CreditGovernor | None = None,
        on_spend: Callable[[float], Awaitable[None]] | None = None,
        cost_per_call: float = 10.0,
        cost_per_gpa: float = 5.0,
    ):
        super().__init__("", timeout=25.0, min_interval_s=0.12)
        self.api_key = api_key
        self._rpc_url = f"{RPC}/?api-key={api_key}" if api_key else ""
        # credit diet: memoize holders per mint so recheck/duplicate passes don't re-spend credits
        self._holders_ttl = cache_ttl_s
        self._holders_cache: dict[str, tuple[float, tuple[list[tuple[str, int]], bool]]] = {}
        # credit governor: pace a monthly quota (ADR-022). If over pace, metered calls are skipped.
        self._gov = governor
        self._on_spend = on_spend
        self._cost = cost_per_call  # DAS getTokenAccounts
        self._gpa_cost = cost_per_gpa  # getProgramAccounts (measured ~4 credits, not 1)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    async def _meter(self, cost: float | None = None) -> bool:
        """Return True if a credit-spending call is allowed under the paced budget; record it if so."""
        cost = self._cost if cost is None else cost
        if self._gov is None:
            return True
        if not self._gov.can_spend(cost):
            return False
        self._gov.note(cost)
        if self._on_spend:
            await self._on_spend(cost)
        return True

    async def _holders_via_gpa(self, mint: str) -> tuple[list[tuple[str, int]], bool] | None:
        """Cheap (~1 credit) provider-agnostic holder fetch via getProgramAccounts on Token-2022.
        Returns None if it can't be used (e.g. token too large / not Token-2022) → caller falls back to DAS."""
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getProgramAccounts",
            "params": [
                TOKEN_2022,
                {"encoding": "jsonParsed", "filters": [{"memcmp": {"offset": 0, "bytes": mint}}]},
            ],
        }
        try:
            d = await self.post_json(self._rpc_url, json=body)
        except Exception:
            return None
        if not isinstance(d, dict) or "error" in d:
            return None
        accts = d.get("result") or []
        if not accts:
            return None  # not a Token-2022 mint (or none yet) → let DAS try
        owners: dict[str, int] = {}
        for a in accts:
            try:
                info = a["account"]["data"]["parsed"]["info"]
                owners[info["owner"]] = owners.get(info["owner"], 0) + int(
                    info["tokenAmount"]["amount"]
                )
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(owners.items(), key=lambda x: -x[1]), True

    async def token_holders(
        self, mint: str, page_size: int = 1000
    ) -> tuple[list[tuple[str, int]], bool]:
        """Returns (owners_sorted_desc_by_amount, complete). `complete` is False if the holder set was
        truncated at page_size. Cached per mint (TTL) — holder sets barely change minute-to-minute and
        DAS calls are credit-expensive."""
        if not self.api_key:
            return [], False
        hit = self._holders_cache.get(mint)
        if hit and (time.monotonic() - hit[0]) < self._holders_ttl:
            return hit[1]

        # 1) cheaper path: getProgramAccounts on Token-2022 (~half a DAS call) — most new mints
        if await self._meter(cost=self._gpa_cost):
            gpa = await self._holders_via_gpa(mint)
            if gpa is not None:
                self._holders_cache[mint] = (time.monotonic(), gpa)
                return gpa

        # 2) fallback: DAS getTokenAccounts (~10 credits) — large/classic tokens gPA can't return
        if not await self._meter(cost=self._cost):
            return [], False
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccounts",
            "params": {"mint": mint, "limit": page_size, "page": 1},
        }
        d = await self.post_json(self._rpc_url, json=body)
        accts = ((d or {}).get("result") or {}).get("token_accounts") or []
        owners: dict[str, int] = {}
        for a in accts:
            o = a.get("owner")
            if o:
                owners[o] = owners.get(o, 0) + int(a.get("amount") or 0)
        result = (sorted(owners.items(), key=lambda x: -x[1]), len(accts) < page_size)
        self._holders_cache[mint] = (time.monotonic(), result)
        return result

    async def address_transactions(self, address: str, limit: int = 100) -> list[dict]:
        if not self.api_key or not address:
            return []
        if not await self._meter():  # paced-budget skip
            return []
        return (
            await self.get_json(
                f"{API}/v0/addresses/{address}/transactions",
                params={"api-key": self.api_key, "limit": limit},
            )
            or []
        )
