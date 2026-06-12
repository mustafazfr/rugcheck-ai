# Decision-logic audit (ADR-049, 2026-06-12)

The owner asked: *"botun karar verme aşamalarını gözden geçirelim sağlamlaştıralım… goplus gibi
sitelerden aldığımız verileri kullanıyor muyuz?"* This is that review: every flag, where its data
comes from, how it moves the score, and its false-positive risk. Pure logic lives in
`web/report.py` (+ flag emitters in `pipeline.py`, `filters/`, `data/goplus.py`, `web/osint.py`).

## How the score works (deterministic, no LLM — ADR-048)

```
any CRITICAL flag  → score 2–16  (16 − 4·n)        level CRITICAL
else any DANGER    → score 18–44 (44 − 7·n)        level DANGER
else               → 0.6·(engine safety_score·100)
                     + 0.4·(100 − RugCheck risk)    ← consensus blend (skipped if RugCheck down)
                     − 8 per SOFT flag
                     → ≥72 SAFE · ≥48 CAUTION · else DANGER
```

**Yes, the external sources are load-bearing, not decoration:** GoPlus emits 6 CRITICAL-class flags
+ 2 SOFT; RugCheck contributes 40% of the clean-path score plus `rugged/high_risk/insiders` flags;
Jupiter emits 2 SOFT flags, feeds the deployer token count, and is the tie-breaker panel. Kill any
one source and the engine degrades gracefully (skip rows, weight re-normalizes) — by design.

## CRITICAL flags (any one → 2–16/100)

| Flag | Source | Fires when | FP risk → mitigation |
|---|---|---|---|
| `not_a_valid_spl_mint` | our RPC | account isn't a mint | ~zero |
| `mint_authority_active` / `freeze_authority_active` | our RPC (chain truth) | authority not renounced | ~zero (USDC honestly trips this — correct) |
| `honeypot_cannot_sell` | Jupiter sell-sim | round-trip quote fails | off by default until verified live |
| `low_liquidity` / `no_liquidity_data` | DexScreener | below floor / unindexed | unindexed-but-real tokens → "couldn't verify" is the honest verdict |
| `rugcheck_rugged` | RugCheck | their rugged marker | trusted (their strongest signal) |
| `single_wallet_dominant` / `holder_concentration_extreme` | Helius holders | >50% one wallet / >90% top-10 | **gated**: infra/LP/pool excluded, only on COMPLETE holder sets, else neutral |
| `goplus_malicious_creator/_non_transferable/_transfer_hook/_high_transfer_fee` | GoPlus | their scan | accepted (no cheaper independent check exists) |
| `goplus_mintable` / `goplus_freezable` | GoPlus | their authority read | **HARDENED (ADR-049):** if our RPC says renounced, the chain wins — downgraded to SOFT `authority_consensus_mismatch`; GoPlus alone can no longer CRITICAL a provably clean token |
| `deployer_serial_rugger` | Helius tx window | many recent launches | **gated (WIF fix):** Jupiter all-time `devMints` NEVER feeds this — only the recent Helius window |
| `insider_funding_match` | Helius funders | deployer's funder == fresh buyers' funder | strongest on-chain rug proof we have; requires BOTH funder traces to agree |

## DANGER flags (any one → 18–44/100)

| Flag | Source | FP risk → mitigation |
|---|---|---|
| `wash_volume`, `lopsided_flow`, `hyper_pump` | DexScreener txns + Gecko traders | thresholds tuned on real incidents (SPCX/BARRON); `wash` hard-vetoes only on thin pools |
| `no_sellers`, `too_few_traders`, `too_few_holders` | DexScreener/Helius | absolute floors (`min_sellers_abs=3`) so 2-sellers-in-500-buyers still trips |
| `bundled_holders` | Helius funder trace | needs shared funder across top holders (complete-set gated) |
| `fresh_buyer_cluster` | Helius buyer profiling | needs ≥`fresh_buyer_min`(5) fresh wallets among profiled — can't fire on <5 profiled by construction |
| `rugcheck_high_risk`, `rugcheck_insiders` | RugCheck | their scoring; insiders cross-checked by our own funding trace |
| `insider_network_dominant` | RugCheck graph | **gated (WIF fix):** % sanity-checked to [0,100], flagged only on plausible values |
| `ticker_impersonation` | static canonical-mint table | conservative 10-symbol list, exact-mint comparison — near-zero FP |
| `twitter_serial_shill`, `twitter_id_mismatch` | syndication timeline / snowflake ID | ID math is exact; shill needs ≥3 OTHER CAs in recent tweets |
| `twitter_inauthentic`, `twitter_handle_reuse` | fxtwitter / DexScreener | score-based; reuse count from market metadata |
| `deployer_rugged_before` | engine watch history | only fires with positive evidence |
| `deployer_fresh_funded` | **reserved — currently emitted by no path** (kept in the tier table so re-enabling it is a one-line change; documented here so nobody thinks it's live) |

## SOFT flags (−8 each on the clean path)

`high_turnover · txn_imbalance · low_float · rugcheck_elevated · llm_rug (funnel only) ·
goplus_closable · goplus_mutable_metadata (info-only, no penalty — BONK lesson) · twitter_weak ·
no_twitter · dev_holds_large · lp_unlocked (age-gated ≥3d AND <15%; ADR-043: low measured lock is
common on legit multi-AMM tokens) · jupiter_low_organic · authority_consensus_mismatch ·
twitter_no_ca_mention (≤30d tokens only) · twitter_site_mismatch · twitter_burst_posting ·
website_brand_new (<14d RDAP) · young_premined (<10min AND top1>30%, infra-excluded top1) ·
fdv_mcap_inflated (>8×) · transfer_fee_unusual (2–10% band) · creator_token_factory (age-gated
≤30d — established devs' public history is not a tell)`

Worst realistic stack on a legit 2-day-old token: `no_twitter + lp_unlocked + website_brand_new +
jupiter_low_organic` = −32 → lands in CAUTION. Verdict: correct — a 2-day-old token with those
gaps **should** read CAUTION.

## Verified during this audit (no change needed)

- `mint_info.top1_pct/top10_pct` are computed **after** infra/LP/pool exclusion and **only** on
  complete holder sets → `young_premined` and the concentration CRITICALs use the right numbers.
- `fresh_buyer_cluster` cannot fire on thin profiling (absolute min doubles as a sample-size guard).
- Jupiter `devMints` (all-time) feeds only the graduated, age-gated `creator_token_factory` —
  regression-tested since the WIF incident.
- Soft flags can't double-fire on mutually exclusive branches (`no_twitter` vs `twitter_weak`).
- Score path is total: every flag in the three tier sets, every tier handled, clamps bounded.

## Changed in this audit (ADR-049)

1. **Chain-truth consensus filter** (`_consensus_filter`): GoPlus authority claims are overruled by
   our own RPC read when the chain says renounced → SOFT mismatch instead of false CRITICAL.
   Regression tests: suppressed / kept-when-chain-agrees / kept-when-chain-unreadable.
2. **Adaptive holders panel** (frontend): >90d-old or >50k-holder tokens collapse the lineup to the
   stat row + "show full holder lineup"; any fresh-buyer signal forces the full forensic view.

## What we'd do differently (honest retro, owner asked)

- **Ship taste decisions as mocks first.** The parchment v1 went straight to code and was rejected
  on sight; a 10-minute mock would have saved a cycle. (Now standard practice.)
- **The A/B machinery outlived its question.** The owner picked design B by conviction before
  traffic could ever power the test. Cost was low and the counters still serve, but the lesson
  stands: brand questions get decided by the owner, not by infrastructure.
- **Don't build derivative features.** The AI analyst paragraph never moved the verdict (by
  design) — which is exactly why it didn't survive contact with "what does this add?" (ADR-048).
- **Twitter timeline is the most fragile dependency** (per-IP 429s, one shared server IP in prod).
  Fail-open is correct; just never promise the section exists.
- **Per-IP limits are abuse *friction*, not abuse *proof*** — VPN rotation defeats them. The real
  wall is the layered Helius budget (monthly governor + daily cap); limits just keep honest users
  honest. Priced in.
