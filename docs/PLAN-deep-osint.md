# PLAN — Deep multi-source OSINT for rugcheck.ai (ADR-041)

User: don't rely only on rugcheck.xyz. Research (web) free ways to detect rugs, then "turn the coin upside
down" — scan the DEPLOYER's wallet + Twitter, the BUYERS' wallets + history. Verified-free building blocks
(live-tested this session):
- **GoPlus Security API** (api.gopluslabs.io, keyless) — independent 2nd security source: mintable/freezable/
  closable authorities, `malicious_address` flag on creators/metadata authority, transfer_hook (Token-2022
  honeypot), transfer_fee, lp_holders (LP lock), holder_count, trusted_token.
- **fxtwitter** (api.fxtwitter.com/<handle>, keyless) — real Twitter profile: account age (joined), followers,
  tweet count, verified, description, avatar → authenticity signals (brand-new / low-follower / no-tweets).
- **Deployer history** (Helius, already have `deployer_signal`/`count_prior_creations` + `cluster.dominant_funder`)
  — serial-deployer (made & abandoned many tokens) + fresh/mixer funding source.
- **Holder/buyer history** (Helius enhanced txns + `wallet_swap_summary`) — fresh-wallet cluster among top holders.

## Architecture
- **GoPlus → engine** (`data/goplus.py` + pipeline), like RugCheck: a 2nd independent rug source feeding
  hard/soft flags + a `goplus_score`. Two independent sources = a consensus, not one point of failure.
- **Deep OSINT → web layer** (`web/osint.py`, called by api after `pipeline.analyze`; cached): gathers
  - `twitter`: handle from DexScreener/RugCheck socials → fxtwitter profile → authenticity verdict.
  - `deployer`: creator (RugCheck `creator` / GoPlus `creators` / launch_meta) → Helius txns → prior token
    creations + dominant funder + age → serial-deployer / fresh-funded flags.
  - `holders_intel`: top N non-infra holders → Helius txns → fresh-wallet / trader summary (buyer history).
  - `sources`: RugCheck + GoPlus side-by-side (agreement / disagreement).
  Best-effort + credit-governed; each piece degrades to "unavailable" without breaking the report.
- **report.py**: add `twitter`, `deployer`, `holders_intel`, `sources` blocks + new categorized checks
  (External: GoPlus; Social: Twitter authenticity; Deployer: serial/fresh; Buyers: fresh-wallet cluster).
- **Frontend**: new panels — "Creator / deployer" (wallet, prior tokens, funding, links), "Twitter / X"
  (avatar, age, followers, verified, authenticity bar), "Top holders & buyer history" (fresh/trader tags),
  "Source consensus" (RugCheck vs GoPlus). Same forensic-lab aesthetic.

## Build order
1. `data/goplus.py` (client + pure `flags`) + config `GoPlusCfg`; wire into pipeline (like rugcheck). Tests.
2. `data/twitter_public.py` (fxtwitter, keyless) + pure authenticity scoring. Tests.
3. `web/osint.py` — gather twitter + deployer + holders_intel + sources (Helius governed, cached). Pure
   sub-helpers unit-tested where possible.
4. `web/report.py` — new blocks + checks. Tests.
5. Frontend — render new panels + checks.
6. config.example, ADR-041; `uv run pytest`; restart web; live-verify on BONK + a fresh meme +
   screenshot. Enable `check_deployer` for the web path.

## Honest framing
Two independent rug APIs + on-chain deployer/holder forensics + real Twitter profile = a genuinely deep,
multi-source scan — but still a screener, not a guarantee. Keyless Twitter gives profile facts (age/
followers/verified), not sentiment. Web "search" is reconstructed as concrete free OSINT sources, not a
scrape. All third-party strings escaped.
