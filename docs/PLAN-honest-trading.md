# PLAN — Honest fills + anti-rug/anti-manipulation + better wallets (ADR-037)

Goal (user, verbatim): **"SADECE PARA KAZANMAK İSTİYORUM."** Build a system that (a) reports HONEST paper P&L
(no fictional fills on illiquid/rugged coins), (b) refuses to buy manipulated/rug-prone coins like the ones
flagged (SPCX, BARRON wash-trade, SV151 low-float), and (c) keeps discovering QUALITY wallets. Autonomous —
no questions; everything done + running on return.

## Evidence that forced this (from the live DB + user screenshots)
- Booked **+292% on SPCX** (6233B…) and **+288% on AMERICA** (EnTRt…) — but SPCX liquidity is now <$1.
  Those sells were physically impossible. Root cause: `PaperExecutor` fills at quoted price × flat 3% slip,
  **ignoring pool depth / price impact / rug**. ⇒ all "wins" are inflated; the dashboard lies.
- BARRON: $1.2M "volume" is ONE wallet wash-trading; 1,696 buyers / **33 sellers**. Our momentum gate
  (vol/liq ≥ 0.30) reads fake volume as real. No organic-flow check.
- These are all PumpSwap (pump.fun) casino coins, re-surfaced via GeckoTerminal trending.

## Phase 1 — HONEST fills (constant-product price impact) [foundation]
Constant-product (x·y=k) realizable value when exiting a position worth `v` (USD, at mid) into quote reserve
`Q ≈ liquidity_usd/2`:  **realized = v · Q/(v+Q)**, effective_price = mid · Q/(v+Q). As liquidity→0 (rug),
realized→0 automatically — so this fixes BOTH impact and rug-exit with one model.
- `execution/paper.py`: `buy`/`sell` take `liquidity_usd`; apply impact (sell) / inverse impact (buy) + fee.
- `portfolio/manager.py`: `mark` + `evaluate_exit` use the REALIZABLE price (impact-adjusted) so a fake spike
  on thin liquidity can't trigger a fake take-profit; add `rugged` exit when liquidity collapses.
- `service.manage_open_positions`: fetch the FULL market (liquidity, not just price); pass to mark/sell.
- `execution/runner` + `base.RiskGate`: cap position so our trade ≤ `max_pool_share_pct` of liquidity
  (don't bet into a pool we'd move); skip/shrink otherwise.
- config: `execution.max_pool_share_pct`, `position.rug_liquidity_usd`, `position.rug_liquidity_drop_pct`.

## Phase 2 — Anti-rug / anti-manipulation ENTRY filters (don't buy the junk)
Free data: extend `TokenMarket` (DexScreener txns h24 buys/sells + price_change_h24) and `GeckoPool`
(h24 buyers/sellers/buys/sells). Add `data/geckoterminal.token_pool(mint)` (free, cached) so the funnel gets
UNIQUE buyers/sellers for any candidate.
New pure module `filters/manipulation.py` (unit-tested) → flags:
- `lopsided_flow`: unique sellers ≪ buyers (e.g. sellers/(buyers+sellers) < `min_seller_share` with enough
  buyers) → everyone buying / can't sell (honeypot/pump). (BARRON 33/1729 ≈ 2% → flag.)
- `wash_volume`: volume_24h/liquidity > `max_vol_liq_ratio` (e.g. 25×) → fake churn.
- `txn_imbalance`: buys/sells txn ratio beyond `max_txn_imbalance`.
- `hyper_pump`: very young + vertical (price_change_h24 > `max_young_pump_pct`) + thin liquidity.
- `thin_float`: circulating (non-pool) float share < `min_float_pct` (uses holder data we already fetch).
Wire into `pipeline.analyze`: fetch gecko pool, compute flags. The strongest (lopsided_flow, wash on thin liq)
are HARD vetoes; the rest are scoring penalties. Raise `filters.min_liquidity_usd`/`ingest.min_liquidity_to_analyze`.

## Phase 3 — Better, cleaner wallet discovery
- `enrich/discovery.is_winner`: a winner must ALSO be clean (reuse manipulation checks) so we don't mine
  wash-traders into the watchlist.
- Promote ranked by realized SOL flow: turn on finalist vetting (`wallet_swap_summary`/`looks_like_trader`)
  and store `realized_pnl_sol`; require `looks_like_trader` before promotion. Keep it credit-governed.
- Keep the recurring-across-distinct-winners core (it works).

## Phase 4 — Observability, tests, docs, honest re-baseline
- Dashboard + analytics: surface manipulation/rug reject reasons; add a "rug/manipülasyon elendi" counter;
  closed-trade table now shows HONEST PnL.
- Tests: impact math, rug exit, every manipulation flag, is_winner-clean, pool-share cap. Keep 100% green.
- `docs/DECISIONS.md` ADR-037; refresh the docs.
- Back up + RESET the DB (old P&L is fictional) → clean honest baseline.
- Deploy: stop bot → new code → reset DB → `make up` (run+dashboard+discover+caffeinate) → verify it runs and
  produces honest verdicts (manipulated coins REJECTed, fills impact-aware).

## Verification
`uv run pytest` green · grep no fake-fill paths · live `report` on a known wash coin → REJECT with the new
flags · short `run` shows manipulated coins rejected + realistic position marks · `discover` still fills the
watchlist · dashboard shows honest PnL. Then leave it running (caffeinated) for the user's return.

## Honest expectation (stated up front)
After Phase 1 the fake +290% wins vanish — measured edge will likely drop toward ~flat/negative. That is the
TRUTH this market gives a free, non-latency bot; surfacing it is the point. Real money is protected by never
flipping to live until paper clears the bar.
