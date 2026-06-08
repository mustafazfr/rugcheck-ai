# Data Sources

Every external service the bot integrates: what it's for, cost/limits, auth, and the fallback. Keep this
in sync with the `data/` clients. **All keys go in `.env` (gitignored).** Verify current pricing/limits at
integration time — these move.

| Service | Used for | Stage | Cost / tier | Auth | Fallback |
|---------|----------|-------|-------------|------|----------|
| **Helius** | RPC, enhanced txns, DAS (token meta/holders), **webhooks** | 0,1,3 | generous free tier; paid scales | API key | QuickNode/Triton → public RPC |
| **PumpPortal** | real-time **new-token** + trade WebSocket | 0 | free WS | none / key for trades | Helius/Geyser logs |
| **DexScreener** | price, liquidity, pairs, **socials** (twitter/tg/web) | 2,7 | free, generous | none | Birdeye |
| **RugCheck** | rug report (authorities, LP, risks) | 1 | free tier + API | API key | our own on-chain checks |
| **TweetScout** | **Twitter intel**: account age, follower quality, notable followers, **handle-reuse**, score | 2 | paid tiers | API key | direct X API → skip Twitter |
| **X (Twitter) API** | direct tweets/followers (optional upgrade) | 2 | Basic ~$100/mo … Pro ~$5k/mo | bearer/OAuth | TweetScout |
| **Telegram (public web)** | public-channel chatter via `t.me/s/<channel>` (NO login) | 2 | free | none | dedicated bot / burner acct → skip. **Never the personal account (ADR-016)** |
| **Jupiter** | swap routing/quotes, price API | 6,7 | free | none (key optional) | direct Raydium |
| **Jito** | MEV-protected bundle submission | 6 | tips (on-chain) | none | priority-fee only |
| **GMGN / Cielo** | smart-money wallet discovery + alerts | 3 | free/paid | key (Cielo) | self-computed PnL |
| **Bubblemaps** | holder clustering / bundle visualization | 3 | free/paid | — | self-computed top-holders |
| **Ollama (local)** | LLM: `qwen2.5:latest` (synthesis), `llama3.2:3b` (classify) | 4 | free, local | none | rule-only mode |

## Required `.env` keys (see `config/.env.example`)
```
HELIUS_API_KEY=
RUGCHECK_API_KEY=        # optional
TWEETSCOUT_API_KEY=      # optional (free tier degrades gracefully)
TELEGRAM_API_ID=         # optional
TELEGRAM_API_HASH=       # optional
CIELO_API_KEY=           # optional
# --- live trading ONLY (leave empty for paper) ---
SOLSCOUT_ALLOW_LIVE=     # set to 1 to permit live (with config.execution.mode=live)
HOT_WALLET_PRIVATE_KEY=  # dedicated low-balance wallet, NEVER the main wallet
```

## Notes
- **Handle-reuse** (TweetScout) is the single highest-signal social check — scammers recycle one Twitter
  account across many dead coins. Prioritize wiring this.
- **CA-in-tweet proof:** confirm the linked account actually tweeted the contract address; a borrowed/
  unrelated link is a red flag even on an old, "legit-looking" account.
- **Helius webhooks** are how smart-money tracking stays real-time without polling.
- On `free` cost-tier, the bot runs fully on Helius free + PumpPortal + DexScreener + on-chain + Telethon
  + local LLM, with Twitter signal reduced. See `BACKUP_PLAN.md` cost tiers.
