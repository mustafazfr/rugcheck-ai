"""ingest — Stage 0. Feed candidates into the funnel (ADR-036, free/keyless):

    stream_candidates (stream.py) -> TokenCandidate(source="launch")
        GeckoTerminal new_pools + DexScreener promoted, liquidity pre-screened.

Output: core.models.TokenCandidate. No analysis here — just discovery + dedupe.
"""
