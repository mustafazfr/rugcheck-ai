"""enrich — Stage 2 (social/OSINT) + Stage 3 (smart-money). Only runs on Stage-1 survivors.

Stage 2 (social.py): resolve socials (DexScreener) then verify authenticity via TweetScout —
    account age, follower quality/bot ratio, notable followers, HANDLE-REUSE history (top signal),
    CA-in-tweet proof; Telegram via PUBLIC web-preview (t.me/s/<channel>) — NEVER the user's
    personal account (ADR-016): zero-login HTTP read of public channels only.
    -> core.models.SocialReport

Stage 3 (smartmoney.py): watchlist-wallet hits + winrate, holder clustering / fresh-wallet ratio.
    -> core.models.SmartMoneyReport

Graceful degradation: a missing source lowers confidence, never crashes the funnel.
"""
