"""core — shared foundation used by every stage.

Planned contents:
    config.py       load + validate config.yaml and .env (pydantic-settings).
    models.py       pydantic dataclasses passed between stages: TokenCandidate,
                    FilterResult, SocialReport, SmartMoneyReport, LlmSynthesis,
                    Decision, Fill, Position.
    db.py           aiosqlite persistence; every input + decision stored (replayable).
    logging.py      structured logging + optional Telegram alert sink.
    kill_switch.py  global halt flag checked before EVERY execution; auto-trips on
                    daily-loss breach or repeated client failures (fail-safe = no-trade).

No business logic here — only primitives the stages depend on.
"""
