# SolScout shortcuts.  Run `make` (or `make help`) to list targets.
# Examples:  make up        (start bot + dashboard in background)
#            make stats     make logs      make down
#            make report MINT=<mint>        make add WALLET=<addr>
.DEFAULT_GOAL := help
SHELL := /bin/bash

help: ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## install Python 3.12, deps, and config/.env from examples
	uv python install 3.12
	uv sync
	@test -f config/config.yaml || cp config/config.example.yaml config/config.yaml
	@test -f .env || cp config/.env.example .env
	@echo "setup done. Put HELIUS_API_KEY in .env, then: make web"

# ---- rugcheck.ai web app ----
web: ## launch the rugcheck.ai web app (http://127.0.0.1:8000)
	uv run solscout serve

web-dev: ## launch the web app with autoreload (development)
	uv run solscout serve --reload

# ---- the bot ----
run: ## run the bot loop in the FOREGROUND (paper) — Ctrl-C to stop
	uv run solscout run

dash: ## serve the dashboard in the FOREGROUND (http://localhost:8787)
	uv run solscout dashboard

up: ## start bot + dashboard in the BACKGROUND (logs in ./logs)
	@./scripts/up.sh

down: ## stop the background bot + dashboard
	@./scripts/down.sh

status: ## show whether bot/dashboard are running + recent log lines
	@./scripts/status.sh

glance: ## quick browser-free status glance (positions, PnL, watchlist, recent buys)
	@./scripts/snapshot.sh

shortcuts: ## (re)generate the double-click desktop shortcuts (Start/Stop/Durum/Panel)
	@./scripts/install-shortcuts.sh

logs: ## tail the background bot log
	@tail -n 80 -f logs/run.log

# ---- analysis / data ----
stats: ## print paper-performance summary
	uv run solscout stats

report: ## one-token report:  make report MINT=<mint>
	uv run solscout report $(MINT)

add: ## add a smart-money wallet:  make add WALLET=<addr> [WR=0.7]
	uv run solscout watchlist-add $(WALLET) --winrate $(or $(WR),0.7)

scan: ## one-shot scan of N launches:  make scan N=5
	uv run solscout scan --limit $(or $(N),5)

recheck: ## re-evaluate due queued launches once
	uv run solscout recheck

manage: ## mark open positions + apply exit rules once
	uv run solscout manage

# ---- quality ----
test: ## run the test suite
	uv run pytest -q

lint: ## run ruff
	uv run ruff check src tests

# ---- 24/7 service (macOS launchd) ----
install-service: ## install + start the always-on background service (auto-restarts/at-login)
	@./scripts/install-launchd.sh

uninstall-service: ## stop + remove the always-on service
	@./scripts/uninstall-launchd.sh

.PHONY: help setup web web-dev run dash up down status glance shortcuts logs stats report add scan recheck manage test lint install-service uninstall-service
