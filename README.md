# rugcheck.ai *(working name)*

A **free Solana token-forensics web app**. Paste a mint address → it runs the token through ~35
on-chain + external checks and returns a forensic safety report in seconds:

- **0–100 safety score** + a **SAFE / CAUTION / DANGER / CRITICAL** verdict
- authorities (mint/freeze), liquidity & LP lock, holder concentration, real-activity floor
- **manipulation forensics**: wash-trading, one-way flow, hyper-pumps, low float, ticker impersonation
- **bundle & insider detection**: shared-funder sybil clusters, RugCheck insider networks,
  **deployer-and-buyers-funded-by-the-same-wallet** (the strongest rug pattern)
- **"Who's behind it" OSINT**: deployer wallet history (prior launches, funding, age), top buyers'
  wallet profiles (fresh-sybil detection), **Twitter/X timeline forensics** (serial CA-shilling,
  forged account-creation dates via snowflake IDs, posting-cadence bot tells), website domain age
- **3-source consensus**: RugCheck.xyz + GoPlus + Jupiter (organicScore, audit, verified list)
- **honeypot sell-simulation** (Jupiter round-trip quote)
- an **AI analyst** paragraph that cites the actual findings (Groq/Ollama; prompt-injection-hardened;
  the verdict itself is always the deterministic rule engine — never the LLM)

Two interchangeable frontends ship A/B: **A "forensic crypto lab"** (dark neon terminal) and
**B "the case file"** (paper dossier with rubber stamps). Visitors are split 50/50; `GET /api/ab`
reports which design drives more scans.

> ⚠️ **Honesty over hype.** ~98% of fresh meme tokens go to zero. This is a *defensive screener* —
> it catches the patterns above, but no screen catches every scam. DYOR. Not financial advice.

## Run it

```bash
# 1. Install uv (https://docs.astral.sh/uv/), then:
uv python install 3.12
uv sync

# 2. Configure (free stack — only HELIUS_API_KEY needed, free tier)
cp config/config.example.yaml config/config.yaml
cp config/.env.example .env      # add HELIUS_API_KEY; never commit .env

# 3. Optional: local AI analyst
ollama serve & ollama pull qwen2.5:14b     # or set GROQ_API_KEY (free) in .env instead

# 4. Serve
make web                          # = uv run solscout serve → http://127.0.0.1:8000
```

`?v=a` / `?v=b` forces a design; the footer link switches too.

## Costs: $0

The entire stack is **free/keyless**: DexScreener, GeckoTerminal, RugCheck, GoPlus, Jupiter,
fxtwitter, Twitter syndication, RDAP, public Solana RPC. The only metered dependency is the **Helius
free tier** (1M credits/mo), protected three ways: persistent SQLite caches (repeat checks cost 0,
across restarts), a daily web sub-budget, and per-IP rate limits + a daily fresh-mint ledger.
The LLM is local Ollama in dev or Groq's free tier in production. See `docs/DATA_SOURCES.md`.

## Heritage: the trading engine

The analysis engine grew out of a **paper-first trading bot** (SolScout). Those paths (`scoring/`,
`execution/`, `portfolio/`, the `run`/`discover` loops, the smart-money watchlist) still live in the
repo and still work, but the web product doesn't use them. Live trading is scaffolded and **locked**
behind two flags + an Edge-Validation Gate — paper is the default, always.

```bash
uv run solscout report <MINT>     # engine report in the terminal (read-only)
make up / make stats / make down  # dormant paper-trading loops, if you ever want them
```

## Layout

| Path | What |
|------|------|
| `src/solscout/web/` | **The product**: FastAPI + OSINT + pure report builder + both frontends |
| `src/solscout/data/` | One client per external API (retry/throttle/cache; nothing else hits the network) |
| `docs/DECISIONS.md` | ADR-001…046 — every design decision and *why* |
| `docs/DATA_SOURCES.md` | Every dependency: cost, auth, fallback |
| `tests/` | 255 passing — the pure layers (filters/scoring/report/osint) are required to be tested |

## Tests

```bash
uv run pytest -q && uv run ruff check src/ tests/
```
