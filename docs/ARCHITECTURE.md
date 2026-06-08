# Architecture

SolScout is a **staged funnel**. Each stage enriches or rejects a token. Cheap, deterministic,
on-chain checks run first so we discard ~95% of candidates before spending API quota or LLM time.

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

**Two independent triggers feed the same funnel:**

1. **New-launch stream** — subscribe to PumpPortal's free WebSocket (`new token` events) and/or a
   Helius/Geyser stream. Emits `(mint, creator, timestamp, initial_liquidity)`.
2. **Smart-money trigger** — Helius webhooks on a watchlist of profitable wallets. When a watched
   wallet buys *anything*, that token enters the funnel with a head-start flag.

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

Only for Stage-1 survivors. This is the "what kind of project is this" layer the user asked for.

- **Socials discovery:** DexScreener / token metadata → Twitter handle, Telegram, website.
- **Twitter authenticity (via TweetScout API — don't rebuild Twitter intel):**
  - account **age** (days-old account = red flag)
  - **handle-reuse history** ← *highest-signal check*: has this account been attached to other
    (dead/rugged) tokens? did it recently rename? Scammers recycle accounts.
  - **follower quality / bot ratio** and **notable followers** (recognized KOLs following = signal)
  - **CA-in-tweet proof:** did the linked account actually tweet the contract address? If not, the
    "official" link is likely borrowed/fake.
- **Telegram (Telethon):** member count, message velocity, **unique-speaker ratio** vs copy-paste spam.

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
