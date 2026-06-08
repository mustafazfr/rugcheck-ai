# Backup Plan & Risk Contingencies

Two kinds of risk: **(A) money/strategy risk** — the bot loses funds; **(B) operational risk** — a
dependency fails. Both are planned for here. The single most important control is the **Edge-Validation
Gate**: we don't risk real money until paper results justify it.

---

## A. Money / strategy risk

### Edge-Validation Gate (must clear before ANY live trade)
Live mode stays locked until **all** of these hold over a forward (not backtested) paper run:

- ≥ **200** paper decisions executed, over ≥ **3 weeks** of real wall-clock time (covers regime changes).
- Paper **net PnL > 0** *after* modeled fees + priority fees + realistic slippage.
- **Win rate or expectancy** beats a dumb baseline (e.g. "buy every Stage-1 survivor equally").
- **Max drawdown** within tolerance defined in config.
- Decision log reviewed: no single lucky outlier carrying the result.

If the gate isn't cleared, the strategy has **no proven edge** → keep iterating on paper. This is the
honest stop that prevents donating money to the market.

### Mandatory live safety rails (never remove — Golden Rule #4)
- **Hot-wallet isolation:** trade only from a dedicated wallet holding a small, losable balance. The
  main wallet's key never touches this system.
- **Per-trade cap** and **daily-loss limit** (hard stop → kill switch trips, no more buys that day).
- **Max open positions** and **max exposure** ceilings.
- **Global kill switch** (`core/kill_switch.py`): one flag halts all buys instantly; trips
  automatically on daily-loss breach or repeated client errors.
- **Time-stop & smart-money-exit:** meme coins decay fast; exit on timer or when the copied wallet sells.
- Fail-safe default = no-trade (ADR-010).

### Strategy fallbacks
- If smart-money + social signals both unavailable for a token → it cannot reach BUY (WATCH at best).
- If win rate degrades live → auto-revert to paper (a live circuit-breaker: N consecutive losers ⇒ pause).

---

## B. Operational / dependency fallbacks

Treat every API as flaky and rate-limited. Each `data/` client implements: retry+backoff, rate-limit,
cache, and a documented degraded path.

| Capability | Primary | Fallback 1 | Fallback 2 / Degraded |
|------------|---------|------------|------------------------|
| RPC / on-chain reads | Helius | QuickNode / Triton | public `api.mainnet-beta` (rate-limited) |
| New-token stream | PumpPortal WS | Helius/Geyser logs | poll DexScreener "new pairs" |
| Wallet activity push | Helius webhooks | poll wallet sigs on interval | — |
| Rug analysis | own on-chain checks | RugCheck API | on-chain only (RugCheck optional) |
| Price / liquidity | DexScreener | Birdeye | Jupiter price API |
| Twitter intel | TweetScout | direct X API (if budget) | **skip Twitter**, use TG + on-chain only |
| Telegram | Telethon (user session) | bot API (limited) | skip TG signal |
| Smart-money data | self-computed PnL (Helius history) | GMGN / Cielo | manual seed watchlist |
| Holder clustering | self-computed (largest accounts) | Bubblemaps | top-holder % only |
| Swap routing | Jupiter | direct Raydium/pump curve | — |
| MEV protection | Jito bundle | high priority fee only | — |
| LLM | Ollama `qwen2.5` | `llama3.2:3b` | rule-only score (drop narrative weight) |

**Graceful degradation principle:** a missing signal lowers confidence, it never crashes the funnel.
The score engine renormalizes weights over available signals and records which were missing.

### Cost-tier profiles (config-selectable)
- **`free`** — Helius free + PumpPortal + DexScreener + on-chain rug checks + Telethon + local LLM.
  No paid Twitter (TweetScout skipped or free quota). Fully functional, weaker social signal.
- **`standard`** — adds TweetScout + Birdeye + Helius paid tier.
- **`pro`** — adds direct X API + Nansen-grade wallet data. (Only worth it if the edge is proven.)

Start on `free`. Don't pay for data until the Edge-Validation Gate is within reach.

---

## C. Environment / build risk
- **Python 3.14 incompatibility** (ADR-002): if `uv python install 3.12` is unavailable, fall back to
  `pyenv` + `venv` on 3.11/3.12. Never run on system 3.14 until Solana lib wheels exist.
- **Ollama not running:** clients health-check Ollama on startup; if down, `report`/`scan` run in
  rule-only mode (narrative weight dropped) rather than failing.
- **Secrets:** all in `.env` (gitignored). A pre-commit/secret-scan check is recommended before any push.

## D. Legal / ToS
- Prefer official/paid APIs over scraping. Twitter scraping violates ToS and risks bans → that's why we
  use TweetScout (ADR-007).
- Trading crypto and running a personal bot is legal in most jurisdictions, but **this is not financial
  advice**, the user trades their own funds at their own risk, and tax/reporting is the user's responsibility.
- No market manipulation, wash trading, or coordinated pump schemes — the bot is a *reactive* analyzer,
  never an instigator.
