"""SolScout — Solana meme-coin intelligence & paper-first trading bot.

Pipeline (funnel), each stage in its own package:
    ingest → filters → enrich → llm → scoring → execution → portfolio

Read docs/ARCHITECTURE.md before changing the flow.
Golden rule: paper by default; the LLM never makes the buy/sell decision.
"""

__version__ = "0.0.1"
