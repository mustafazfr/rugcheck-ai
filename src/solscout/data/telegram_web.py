"""Telegram PUBLIC web-preview reader — zero login, never the user's account (ADR-016).

Fetches `https://t.me/s/<channel>` and parses recent public posts + subscriber count from the HTML.
Only works for public channels with web preview enabled; private groups return empty (graceful).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from .base import BaseClient

_MSG_RE = re.compile(r"tgme_widget_message_text[^>]*>(.*?)</div>", re.S)
_SUBS_RE = re.compile(
    r'counter_value">([^<]+)</span>\s*<span class="counter_type">(subscribers|members)'
)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class TelegramChannel:
    channel: str
    exists: bool = False
    subscribers: int | None = None
    messages: list[str] = field(default_factory=list)


class TelegramWebClient(BaseClient):
    def __init__(self):
        super().__init__(
            "https://t.me",
            timeout=15.0,
            min_interval_s=0.5,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SolScout/0.1)"},
        )

    async def fetch_channel(self, channel: str, limit: int = 30) -> TelegramChannel:
        slug = channel.strip().rstrip("/").split("/")[-1].lstrip("@")
        if not slug:
            return TelegramChannel(channel=channel)
        try:
            page = await self.get_text(f"/s/{slug}")
        except Exception:
            return TelegramChannel(channel=slug, exists=False)
        messages = [_clean(m) for m in _MSG_RE.findall(page)]
        messages = [m for m in messages if m][-limit:]
        subs = _parse_subs(page)
        return TelegramChannel(
            channel=slug,
            exists=bool(messages) or subs is not None,
            subscribers=subs,
            messages=messages,
        )


def _clean(raw: str) -> str:
    text = raw.replace("<br/>", " ").replace("<br>", " ")
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _parse_subs(page: str) -> int | None:
    m = _SUBS_RE.search(page)
    if not m:
        return None
    val = m.group(1).strip().replace(" ", "").replace(",", "")
    try:
        mult = {"k": 1_000, "m": 1_000_000}.get(val[-1].lower())
        return int(float(val[:-1]) * mult) if mult else int(float(val))
    except (ValueError, IndexError):
        return None
