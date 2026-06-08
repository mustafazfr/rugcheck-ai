# SolScout

A **Solana meme-coin intelligence & paper-first trading bot**.

It watches newly launched tokens, filters scams on-chain, verifies the project's social identity
(is the linked Twitter real? bot followers? account age? handle recycled from dead coins? is there a
real community?), checks whether proven-profitable wallets are buying, then runs a **deterministic
"worth buying?" decision** and can place trades — **paper-traded by default**.

> ⚠️ **Reality check.** This operates in one of the most adversarial, negative-sum markets there is.
> Roughly 98% of these tokens go to zero. The bot's edge is *defensive* (avoid rugs) and *imitative*
> (follow proven smart money) — **not** out-sniping MEV bots. **Default mode is paper. Do not risk
> money you can't lose, and only go live after the paper-validation gate is cleared.** Not financial advice.

## How it works (funnel)

`Ingest → Hard rug filters → Social/OSINT enrichment → Smart-money check → LLM synthesis (Qwen) →
Deterministic score & decision → Execution (paper/live) → Position management`

The LLM (local Ollama `qwen2.5`) only **summarizes and scores text** — it never makes the buy/sell
decision. That is a transparent, auditable rule engine.

See [`docs/`](./docs) for the full design, decisions, and backup plan.

## Quick start (after the development phase)

```bash
# 1. Install uv (https://docs.astral.sh/uv/), then:
uv python install 3.12
uv sync                          # creates the venv, installs deps

# 2. Configure
cp config/config.example.yaml config/config.yaml
cp config/.env.example .env      # fill in API keys (Helius etc.); never commit this

# 3. Make sure Ollama is running with the model
ollama serve &
ollama pull qwen2.5

# 4. Analyze a single token (read-only, always safe)
uv run solscout report <TOKEN_MINT_ADDRESS>
```

## Shortcuts (no need to memorize commands)

```bash
make setup            # one-time: Python 3.12 + deps + config/.env from examples
make add WALLET=<addr> # seed a smart-money wallet (do this or the bot rarely BUYs!)
make up               # start bot loop + dashboard in the background (PAPER)
make status           # are they running? + recent log
make stats            # paper performance summary (PnL, win-rate, …)
make down             # stop everything
make logs             # follow the bot log
```
Dashboard: <http://localhost:8787> (auto-refreshing). Run `make help` for all targets.

**Always-on (macOS):** `make install-service` keeps the bot alive 24/7 (restarts on crash, starts at
login) via launchd — paper mode, safe. `make uninstall-service` removes it.

> ⚠️ With an **empty watchlist** the positive gate can't open, so the bot almost never BUYs and collects
> little trade data. Seed wallets with `make add WALLET=<addr>` first — `make stats` / the dashboard warn
> you when the watchlist is empty.

## Status

Paper bot is **feature-complete**: full funnel + autonomous paper buy/manage + grace-period re-eval +
continuous `run` loop + analytics/dashboard. Live execution (`LiveExecutor`) is **scaffolded but locked**
behind two flags and stays off until the paper Edge-Validation Gate is cleared.

## Layout

| Path | What |
|------|------|
| `docs/ARCHITECTURE.md` | Full pipeline + scoring formula |
| `docs/DECISIONS.md` | Why each choice was made (ADR log) |
| `docs/BACKUP_PLAN.md` | Fallbacks, cost tiers, edge-validation gate, safety rails |
| `docs/DATA_SOURCES.md` | Every external API: cost, limits, auth, fallback |
| `src/solscout/` | Source (one folder per pipeline stage) |
