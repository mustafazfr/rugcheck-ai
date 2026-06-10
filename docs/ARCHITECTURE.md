# Architecture

## The product today — rugcheck.ai web app (ADR-040…046)

The shipped product is a **web forensics report**, not the trading funnel. One request runs:

```
GET /api/check/{mint}
  → per-IP rate limit (web/ratelimit.py)          429 {error, scope, retry_after_s}
  → L1 in-process cache → L2 SQLite cache          repeat checks = 0 Helius credits, restart-safe
  → singleflight (concurrent same-mint = 1 run)
  → pipeline.analyze        market · authorities · holders · manipulation · RugCheck · GoPlus
  → web/osint.gather        Jupiter token intel · X profile + tweet timeline · RDAP site age ·
                            deployer + top-buyer wallet summaries (all SQLite-cached, 36h)
  → web/report.build_report flags → severity tiers → 0-100 score + level + ~35 checks   [pure]
  → llm/analyst             Groq (key set) | Ollama | rules — commentary only, injection-guarded
  → cache + A/B counter → JSON
```

Cost model: the only metered dependency is the Helius free tier — bounded by a monthly pace
governor, a daily web sub-budget (`web.helius_daily_budget`) and a per-IP daily fresh-mint ledger.
Everything else (DexScreener, GeckoTerminal, RugCheck, GoPlus, Jupiter, fxtwitter, syndication,
RDAP, public RPC, Groq free tier) is keyless/free. See `docs/DATA_SOURCES.md`.

---

## Heritage: the trading funnel (the engine the product reuses)

SolScout is a **staged funnel**. Each stage enriches or rejects a token. Cheap, deterministic,
on-chain checks run first so we discard ~95% of candidates before spending API quota or LLM time.
The trading stages (S5–S7) are dormant in the web product but still tested and runnable.

Every stage is implemented as `input dataclass → output dataclass` so any stage can be unit-tested
and replayed from stored fixtures.

```
                    ┌─────────────────────────────────────────────────────────────┐
   new mints  ─────▶│ S0 INGEST                                                     │
   wallet acts ────▶│   PumpPortal WS (new tokens) · Helius webhooks (smart wallets)│
                    └───────────────┬─────────────────────────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────────────────────────┐
                    │ S1 HARD FILTERS  (on-chain, cheap, deterministic)             │
                    │   mint authority · freeze authority · LP burned/locked ·      │
                    │   top-holder concentration · bundle/sniper cluster · dev %    │──▶ REJECT (most)
                    └───────────────┬─────────────────────────────────────────────┘
                                    ▼ survivors only
                    ┌─────────────────────────────────────────────────────────────┐
                    │ S2 ENRICH — social / OSINT                                    │
                    │   socials (DexScreener) · Twitter intel (TweetScout):         │
                    │   account age, follower quality, notable followers,           │
                    │   HANDLE-REUSE history, CA-in-tweet proof · Telegram stats     │
                    └───────────────┬─────────────────────────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────────────────────────┐
                    │ S3 SMART-MONEY                                                │
                    │   any tracked profitable wallet holding/buying? ·             │
                    │   holder clustering / fresh-wallet ratio                      │
                    └───────────────┬─────────────────────────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────────────────────────┐
                    │ S4 LLM SYNTHESIS (Qwen, local)                                │
                    │   ingest chatter + signals → human report + STRUCTURED JSON   │
                    │   {narrative_strength, scam_language_flags[], summary, ...}    │
                    └───────────────┬─────────────────────────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────────────────────────┐
                    │ S5 SCORE + DECIDE  (deterministic — NOT the LLM)              │
                    │   hard red-flag rules → weighted composite 0-100 →            │
                    │   risk/portfolio limits → BUY / WATCH / REJECT                 │
                    └───────────────┬─────────────────────────────────────────────┘
                                    ▼ BUY only
                    ┌─────────────────────────────────────────────────────────────┐
                    │ S6 EXECUTE   PAPER by default | live behind 2 flags           │
                    │   Jupiter route → (Jito bundle) → fill → record               │
                    └───────────────┬─────────────────────────────────────────────┘
                                    ▼
                    ┌─────────────────────────────────────────────────────────────┐
                    │ S7 POSITION MGMT   TP / SL / trailing / time-stop / exit      │
                    └─────────────────────────────────────────────────────────────┘
```

---

## Stage 0 — Ingest

**Free/keyless candidate stream (ADR-036 — pump.fun is removed):**

1. **GeckoTerminal `new_pools`** — fresh DEX listings, liquidity pre-screened before any Helius credit.
2. **DexScreener promoted/boosted tokens** — paid-promotion listings worth screening.
3. *(web product)* whatever mint the user pastes.

Output: `TokenCandidate{mint, source, discovered_at, raw_meta}`.

## Stage 1 — Hard filters (on-chain, deterministic)

Cheapest, fastest, highest-precision rejections. All from RPC/DAS — no paid social calls yet.

| Check | Reject when | Notes |
|-------|-------------|-------|
| Mint authority | not renounced (can mint ∞ supply) | unless config allows pump.fun-style curves |
| Freeze authority | not null (dev can freeze your tokens) | classic honeypot rug |
| LP status | not burned/locked | dev can pull liquidity |
| Top-10 holder % | > `max_top10_pct` | concentration = dump risk |
| Dev/creator hold % | > `max_dev_pct` | |
| Bundle/sniper cluster | > `max_bundle_pct` bought in first N blocks from one funder | coordinated launch |
| Liquidity floor | < `min_liquidity_usd` | too thin to enter/exit |

Output: `FilterResult{passed: bool, hard_flags: [...], metrics: {...}}`. Any hard flag ⇒ REJECT, funnel stops.

## Stage 2 — Enrich (social / OSINT)

Only for Stage-1 survivors. This is the "what kind of project is this" layer — all FREE (ADR-041/046):

- **Socials discovery:** DexScreener / token metadata → Twitter handle, Telegram, website.
- **Twitter authenticity (fxtwitter + syndication SSR, keyless):**
  - account **age / followers / verified** → deterministic authenticity verdict
  - **snowflake-ID forgery check:** the user-id encodes the REAL creation date — a mismatch with the
    claimed join date = recycled/forged identity
  - **tweet-timeline forensics:** recent tweet TEXTS → serial CA-shilling (pushes other token mints),
    **CA-in-tweet proof** (did the account actually post this mint? — young tokens only),
    posting-cadence bot tells, bio-website ↔ token-website match
  - *(TweetScout remains an optional paid upgrade for handle-reuse counts; not required)*
- **Website domain age (RDAP, keyless):** a site registered days before launch = throwaway tell.
- **Telegram (public web, NO login):** subscriber count + recent public messages.

Output: `SocialReport{twitter_age_days, handle_reuse_count, follower_quality, notable_followers,
ca_tweet_verified, tg_unique_speakers, raw_texts_for_llm, social_flags: [...]}`.

## Stage 3 — Smart-money

- **Watchlist hit:** is any wallet from our proven-PnL watchlist holding or buying? (strongest single
  positive signal — real capital, can't be faked).
- **Holder clustering:** Bubblemaps-style or self-computed — fresh-wallet ratio, funded-from-same-source
  clusters (insider bundles).

Output: `SmartMoneyReport{smart_wallets_in: [...], best_wallet_winrate, fresh_wallet_pct, cluster_flags}`.

## Stage 4 — LLM synthesis (Qwen, local)

The LLM's **only** job: turn unstructured chatter + the structured signals above into (a) a
human-readable report and (b) a **strict JSON** object validated by pydantic. It must return
structured fields, never a trade instruction.

```jsonc
{
  "summary": "one-paragraph human read",
  "narrative_strength": 0.0-1.0,        // how compelling/sticky is the meme/story
  "scam_language_flags": ["guaranteed", "presale dm me", ...],
  "community_authenticity": 0.0-1.0,    // organic vs astroturfed, from text patterns
  "notable_mentions": [...]
}
```

Use `llama3.2:3b` for cheap per-message classification at volume; `qwen2.5` for the final synthesis.

## Stage 5 — Score + decide (DETERMINISTIC)

This is the "alınmaya değer mi / is it worth buying" filter. **Two layers:**

**Layer A — Hard rules (veto).** Any of these ⇒ REJECT regardless of score:
`freeze_authority != null` · `mint_authority != null (non-curve)` · `LP not locked` ·
`handle_reuse_count >= max` · `liquidity < floor` · `kill_switch active` · portfolio/risk limit hit.

**Layer B — Weighted composite (0–100).** Only computed if Layer A passes. Weights live in
`config.yaml` (never hardcoded). Starting point:

```
score =  w_safety   * onchain_safety_score      # 0.30  (holder spread, LP depth, no bundles)
       + w_smart    * smart_money_score          # 0.30  (watchlist hits × winrate)  ← heaviest, hardest to fake
       + w_social   * social_authenticity_score  # 0.25  (age, real followers, CA proof, NO reuse)
       + w_narrative* llm_narrative_score         # 0.15  (LLM narrative_strength × community_authenticity)
```

**Decision:**
- `score ≥ buy_threshold` **and** smart-money or social gate met → **BUY** (size per risk rules)
- `watch_threshold ≤ score < buy_threshold` → **WATCH** (alert, re-evaluate, no trade)
- else → **REJECT**

Position size = `min(per_trade_cap, bankroll * risk_fraction)`, scaled down by score and liquidity.
The whole decision object is logged for audit & backtesting.

## Stage 6 — Execute

- **Interface:** `Executor.buy(mint, size, max_slippage) -> Fill`. Two implementations behind it:
  `PaperExecutor` (simulates fill against live quote + slippage/fee model) and `LiveExecutor`.
- **Default = PaperExecutor.** `LiveExecutor` is only constructed when `execution.mode == live` AND
  `SOLSCOUT_ALLOW_LIVE=1`. It uses Jupiter for routing and optionally Jito bundles for MEV protection,
  with priority-fee config. Pre-trade it re-checks: kill switch, daily-loss limit, max open positions,
  per-trade cap, and that the wallet is the designated hot wallet (not main).
- Every fill (paper or live) is persisted identically, so paper and live share analytics.

## Stage 7 — Position management

- Rules-based exits from config: take-profit ladder, hard stop-loss, trailing stop, time-stop
  (meme coins decay fast), and "smart-money exited" trigger (if the wallet we copied sells, we exit).
- Continuously marks positions against DexScreener/Jupiter price; persists PnL.

---

## Cross-cutting

- **`data/` clients:** retry + exponential backoff + per-API rate limiter + response cache. Single
  choke point for every external call.
- **Persistence:** SQLite tables — `candidates`, `filter_results`, `social_reports`, `decisions`,
  `fills`, `positions`, `wallets_watchlist`. Everything is replayable.
- **Kill switch:** `core/kill_switch.py` — a flag (file/db) checked before every execution; trips
  automatically on daily-loss-limit breach or repeated client failures.
- **Observability:** structured logging + optional Telegram alerts on BUY/WATCH/exit/kill.

---

## Extensions (ADR-012–015)

- **Honeypot / sell-simulation (Stage 1+6):** simulate a sell (Jupiter both-way quote / RPC
  `simulateTransaction`) and compute round-trip tax; reject if you can't exit or tax > limit. Catches
  transfer-hook/blacklist honeypots that pass authority checks.
- **Semi-auto co-pilot (Stage 6):** mode `semi_auto` sends high-score BUYs to Telegram for tap-to-approve;
  no auto-fill. The safe bridge from paper → live.
- **Deployer-reputation DB (Stage 1/3):** blacklist creators tied to prior rugs — the negative mirror of
  smart-money; a hard reject.
- **Smart-money auto-discovery (offline job):** scan early buyers of past moonshots, rank by realized PnL,
  auto-refresh the Stage-3 watchlist.
