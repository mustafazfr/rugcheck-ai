# PLAN — rugcheck.ai (web product pivot, ADR-040)

Goal (user): pivot away from trading → build **rugcheck.ai**, a broad, comprehensive, crypto-native web app
that takes a Solana token and runs it through ALL our free analysis layers, returning a deep, controlled
("geniş çaplı kontrollü") safety report. Max effort, distinctive design (frontend-design skill), production-grade.

## What we already have (the moat — reuse, don't rebuild)
A full FREE analysis engine in `pipeline.analyze` + `filters/` + `enrich/` + `data/`:
authorities, liquidity, holder concentration (top1/top10, infra-excluded), real-activity (holders/traders/
sellers), manipulation (wash/lopsided/hyper-pump/txn-imbalance/low-float), bundle/cluster funder graph,
RugCheck.xyz (score/rugged/insiders/LP-lock/named risks), honeypot sell-sim (Jupiter), smart-money overlap,
Qwen AI commentary. All keyless except Helius (governed). This is the product's brain.

## Architecture
- **Backend: FastAPI** (`src/solscout/web/api.py`). Lifespan opens the data clients ONCE (DexScreener,
  GeckoTerminal, public RPC, Helius, Jupiter, RugCheck, Telegram, TweetScout). Endpoints:
  - `GET /api/check/{mint}` → runs `pipeline.analyze`, maps to a `SafetyReport` JSON (below).
  - `GET /api/health` → liveness + which providers are configured.
  - `GET /` + `/static/*` → serves the frontend.
  In-memory TTL cache per mint (web is user-triggered; protect Helius credits + speed). Input validation
  (base58 length 32–44). Errors degrade gracefully → partial report, never 500 the user.
- **Report builder** (`src/solscout/web/report.py`, PURE → unit-tested): `Analysis → SafetyReport`.
  Translates the trading-oriented Analysis into a SECURITY product:
  - `score` 0..100 (100 = safest) from safety_score + RugCheck + flags.
  - `level`: SAFE / CAUTION / DANGER / CRITICAL.
  - `checks[]`: every check as {id, label, status (pass/warn/fail/info/skip), detail, category} across
    categories: Authorities, Liquidity, Holders, Activity, Manipulation, Bundle/Insiders, External (RugCheck),
    Honeypot, Social/AI. This is the "geniş çaplı kontrollü" surface — ~20+ named checks.
  - `market`, `holders` (top, %), `cluster` (funder groups), `rugcheck`, `ai` (Qwen text), `meta`.
- **Frontend** (`src/solscout/web/static/`): single-page, vanilla HTML/CSS/JS (no build step → robust),
  served by FastAPI. Fetches `/api/check`.

## Design direction (frontend-design)
**"Forensic crypto-lab terminal"** — deep near-black canvas, fine technical grid + scanline grain, an
ACID-LIME signal accent, risk ramp lime→amber→red that recolors the whole verdict zone. Display font
**Chakra Petch** (cyber/forensic, not generic), data font **JetBrains Mono**. Big animated SVG **risk gauge**,
staggered reveal on load, holder distribution bars, a bundle/cluster mini-map, monospace evidence rows.
Dark, intentional, crypto-native. NO purple-on-white AI slop.

## Pages / sections (one immersive page)
1. Hero: product mark `rugcheck.ai`, tagline, big search (paste mint) + a few example/demo mints.
2. Verdict block: animated gauge (0–100), SAFE/CAUTION/DANGER/CRITICAL badge, one-line human summary,
   token identity (name/symbol/links: DexScreener, Solscan, Bubblemaps).
3. Check grid: categorized pass/warn/fail cards (the comprehensive control surface).
4. Holders + bundle/insider visualization (bars + cluster map).
5. Market & liquidity (liq, mcap, vol, age, LP-lock from RugCheck).
6. External intel: RugCheck score + named risks.
7. AI analyst note (Qwen) with honest disclaimer.
8. Footer: "free, on-chain, not financial advice" + how it works.

## Build order
1. Deps: add `fastapi`, `uvicorn[standard]`; `uv sync`.
2. `web/report.py` (pure) + tests.
3. `web/api.py` (FastAPI, lifespan, cache, validation, static mount).
4. Frontend: `static/index.html`, `styles.css`, `app.js` (forensic-lab aesthetic, gauge, checks, viz).
5. CLI/Makefile: `solscout serve` (or `make web`) → uvicorn; update docs.
6. Verify: `uv run pytest` green; start server; `curl /api/health` + `/api/check/<known mint>` returns a
   full report; load the page and confirm it renders a real analysis end-to-end.

## Honest framing (kept from before)
Defensive screener, not financial advice; ~free signals catch most rugs/manipulation but nothing is 100%
(RugCheck's own disclaimer). Surface confidence honestly per check. No trading in this product.
