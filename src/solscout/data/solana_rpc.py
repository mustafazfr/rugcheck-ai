"""Solana JSON-RPC reads via raw HTTP (no solana-py needed for the read-only path).

Public endpoint by default; pass a Helius URL for higher limits. We read SPL mint authorities,
supply, and top-holder concentration — the raw inputs for Stage 1 rug filters.
"""

from __future__ import annotations

from ..core.models import MintInfo
from .base import BaseClient

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


class SolanaRpcClient(BaseClient):
    def __init__(self, endpoint: str | None = None, helius_api_key: str = ""):
        if not endpoint:
            endpoint = (
                f"https://mainnet.helius-rpc.com/?api-key={helius_api_key}"
                if helius_api_key
                else PUBLIC_RPC
            )
        # public RPC is rate-limited; throttle politely
        super().__init__("", timeout=20.0, min_interval_s=0.25)
        self._endpoint = endpoint

    async def _rpc(self, method: str, params: list):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        data = await self.post_json(self._endpoint, json=body)
        if "error" in data:
            raise RuntimeError(f"RPC {method} error: {data['error']}")
        return data.get("result")

    async def get_transaction(self, signature: str, commitment: str = "confirmed") -> dict | None:
        """Raw getTransaction (jsonParsed) — used by the ADR-047 payment verifier. None = not (yet)
        on chain at this commitment; the caller treats that as 'pending', not as failure."""
        return await self._rpc(
            "getTransaction",
            [signature, {"encoding": "jsonParsed", "commitment": commitment,
                         "maxSupportedTransactionVersion": 0}],
        )

    async def latest_blockhash(self, commitment: str = "confirmed") -> str | None:
        """Recent blockhash for client-built transactions (proxied so the browser never needs RPC CORS)."""
        res = await self._rpc("getLatestBlockhash", [{"commitment": commitment}])
        return ((res or {}).get("value") or {}).get("blockhash")

    async def get_mint_info(self, mint: str) -> MintInfo | None:
        """Authorities + supply + decimals. None if not a valid SPL mint.
        Holder concentration is computed separately from Helius DAS (see data/helius.py)."""
        res = await self._rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        value = (res or {}).get("value")
        if not value:
            return None
        parsed = (value.get("data") or {}).get("parsed") or {}
        if parsed.get("type") != "mint":
            return None
        info = parsed.get("info") or {}
        return MintInfo(
            mint=mint,
            mint_authority=info.get("mintAuthority"),
            freeze_authority=info.get("freezeAuthority"),
            supply=int(info.get("supply") or 0),
            decimals=int(info.get("decimals") or 0),
            is_initialized=bool(info.get("isInitialized", True)),
        )
