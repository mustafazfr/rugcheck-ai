"""Base async HTTP client: shared retry + backoff + light rate limiting + TTL cache.

Every data/ client subclasses this. No pipeline stage may bypass it and call the network directly.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.logging import get_logger

log = get_logger(__name__)

_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)


class BaseClient:
    def __init__(
        self,
        base_url: str = "",
        *,
        timeout: float = 15.0,
        min_interval_s: float = 0.0,  # simple per-client throttle between requests
        cache_ttl_s: float = 0.0,  # 0 disables caching
        headers: dict[str, str] | None = None,
    ):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, headers=headers or {})
        self._min_interval = min_interval_s
        self._last_call = 0.0
        self._lock = asyncio.Lock()
        self._cache_ttl = cache_ttl_s
        self._cache: dict[str, tuple[float, Any]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    @retry(
        retry=retry_if_exception_type(_RETRYABLE),
        wait=wait_exponential(multiplier=0.4, max=6),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        await self._throttle()
        resp = await self._client.request(method, url, **kw)
        resp.raise_for_status()
        return resp

    async def get_json(self, url: str, *, cache_key: str | None = None, **kw) -> Any:
        if cache_key and self._cache_ttl > 0:
            hit = self._cache.get(cache_key)
            if hit and (time.monotonic() - hit[0]) < self._cache_ttl:
                return hit[1]
        data = (await self._request("GET", url, **kw)).json()
        if cache_key and self._cache_ttl > 0:
            self._cache[cache_key] = (time.monotonic(), data)
        return data

    async def post_json(self, url: str, *, json: Any = None, **kw) -> Any:
        return (await self._request("POST", url, json=json, **kw)).json()

    async def get_text(self, url: str, **kw) -> str:
        return (await self._request("GET", url, **kw)).text
