# Data Sources — current (ADR-046)

Every external service the product uses TODAY: what it's for, cost, auth, fallback. Keep in sync with
`src/solscout/data/`. **The whole stack is free; only `HELIUS_API_KEY` (free tier) and optional
`GROQ_API_KEY` (free tier) are set.** All keys in `.env` (gitignored).

## Active (the rugcheck.ai web product)

| Service | Client | Used for | Cost | Auth | Fallback |
|---------|--------|----------|------|------|----------|
| **Helius** | `helius.py` | holders (gPA ~5cr / DAS ~10cr), wallet tx history (10cr) — THE only metered dep; credit-governed (monthly pace + daily web cap) + persistent SQLite caches | free 1M cr/mo | API key | skip → degraded report (concentration neutral, no wallet forensics) |
| **Public Solana RPC** | `solana_rpc.py` | mint authorities / supply (cheap reads) | free | none | any RPC URL via `solana_rpc_url` |
| **DexScreener** | `dexscreener.py` | market (price/liq/vol/txns), socials, websites | free | none | GeckoTerminal |
| **GeckoTerminal** | `geckoterminal.py` | unique buyers/sellers 24h, new/trending pools | free (~30 req/min) | none | DexScreener txn counts |
| **RugCheck.xyz** | `rugcheck.py` | aggregated rug report: score, LP lock, insider networks, creator, known accounts | free | none | GoPlus + our on-chain checks |
| **GoPlus** | `goplus.py` | 2nd security read: transfer-hook/non-transferable (Token-2022 honeypots), malicious creator, transfer fee | free | none | RugCheck + our checks |
| **Jupiter (quote)** | `jupiter.py` | honeypot sell-simulation (buy→sell round-trip quote) | free | none | skip (neutral) |
| **Jupiter (lite-api v2)** | `jupiter.py` `token_info` | 3rd security read: audit booleans, organicScore (wash detector), isVerified/tags, holderCount, **devMints** (replaces the 10cr Helius deployer call when present) | free | none | Helius deployer path |
| **fxtwitter** | `twitter_public.py` | X profile facts: followers, age, verified, snowflake `user_id`, bio website | free | none | "no twitter" soft flag |
| **Twitter syndication SSR** | `twitter_public.py` `timeline` | recent tweet TEXTS → serial CA-shill, cadence, CA-mention checks. **429s hard per IP** — single attempt, 30min cache, fail-open | free | none | timeline section absent |
| **RDAP (rdap.org)** | `rdap.py` | project-website domain age (`registration` event) | free | none | skip (site_age check skipped) |
| **Telegram public web** | `telegram_web.py` | public-channel chatter via `t.me/s/<ch>` — NO login ever (ADR-016) | free | none | skip |
| **Ollama (local)** | `llm/analyst.py`, `llm/synthesize.py` | AI analyst (qwen2.5:14b → qwen2.5:latest) — commentary only, never the verdict | free, local | none | Groq → deterministic rules |
| **Groq** | `llm/analyst.py` | hosted Llama for the PUBLIC deploy (no GPU to self-host); same prompt + injection guard | free tier | API key (optional) | Ollama → rules |

## Optional / dormant

| Service | Status |
|---------|--------|
| **TweetScout** | client exists (`tweetscout.py`), degrades gracefully without a key; would add handle-reuse counts. Not paid for — the free timeline forensics (ADR-046) covers most of the value. |
| **Jito** | live-trading MEV protection — trading paths are NOT used by the web product; LiveExecutor stays locked. |

## Removed (don't re-add without an ADR)

PumpPortal/pump.fun (ADR-036), Cielo, X (Twitter) paid API, Bubblemaps API (we link out to their UI).

## `.env` keys (see `config/.env.example`)

```
HELIUS_API_KEY=          # the only key required for full reports
GROQ_API_KEY=            # optional: public-deploy LLM (empty locally → Ollama)
# everything else optional / trading-only — see .env.example
```

## Production notes (single public server)

- All visitors share the server's ONE IP → fxtwitter/syndication per-IP quotas bite sooner than in dev.
  Everything fails open; expect the tweet-timeline section to be absent more often.
- Helius spend is bounded three ways: monthly pace governor, `web.helius_daily_budget`, per-IP daily
  fresh-mint ledger. Repeat lookups are served from SQLite caches at 0 credits.
