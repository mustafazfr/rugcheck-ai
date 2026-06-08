"""data — one client per external service. THE ONLY place that touches the network.

Every client implements: retry + exponential backoff (tenacity), per-API rate limiting,
response caching, and a documented degraded path (see docs/BACKUP_PLAN.md fallback table).
No pipeline stage may call an API directly — it goes through a client here.

Clients (free/keyless stack — ADR-036):
    helius.py        RPC, enhanced txns, DAS (holders/meta) — credit-governed
    geckoterminal.py FREE, no key: new_pools (fresh candidates) + trending_pools (discovery winners)
    dexscreener.py   FREE, no key: price, liquidity, socials, promoted lists
    solana_rpc.py    cheap mint authorities/supply on the public RPC (no Helius credits)
    jupiter.py       FREE, no key: round-trip quote = honeypot / sell-simulation guard
    tweetscout.py    Twitter intel (optional; key-gated — degrades gracefully when absent)
    telegram_web.py  zero-login public web-preview chatter
"""
