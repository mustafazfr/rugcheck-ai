"""Pydantic models — the contract passed between pipeline stages.

Everything is optional-friendly: a missing signal lowers confidence, it never crashes the funnel.
On-chain amounts are integers (lamports / atomic units); never float for money.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CandidateSource(str, Enum):
    LAUNCH = "launch"
    SMART_MONEY = "smart_money"
    MANUAL = "manual"


class ExecutionMode(str, Enum):
    PAPER = "paper"
    SEMI_AUTO = "semi_auto"
    LIVE = "live"


class Verdict(str, Enum):
    BUY = "BUY"
    WATCH = "WATCH"
    REJECT = "REJECT"


class Social(BaseModel):
    type: str  # twitter | telegram | discord | website | ...
    url: str


class TokenCandidate(BaseModel):
    """Stage 0 output — a token that entered the funnel."""

    mint: str
    source: CandidateSource = CandidateSource.MANUAL
    discovered_at: datetime = Field(default_factory=_utcnow)
    creator: str | None = None
    raw_meta: dict = Field(default_factory=dict)


class TokenMarket(BaseModel):
    """Market view from DexScreener (most-liquid pair)."""

    mint: str
    name: str | None = None
    symbol: str | None = None
    price_usd: float | None = None
    liquidity_usd: float | None = None
    fdv: float | None = None
    market_cap: float | None = None
    volume_24h: float | None = None
    pair_created_at: datetime | None = None
    dex: str | None = None
    pair_address: str | None = None
    # manipulation/organic-flow signals (ADR-037). txns = DexScreener; unique buyers/sellers = GeckoTerminal.
    txns_buys_h24: int | None = None
    txns_sells_h24: int | None = None
    price_change_h24: float | None = None
    buyers_h24: int | None = None       # unique buyer wallets (24h) — None if not fetched
    sellers_h24: int | None = None      # unique seller wallets (24h) — lopsided vs buyers = manipulation
    socials: list[Social] = Field(default_factory=list)
    websites: list[str] = Field(default_factory=list)

    def social_url(self, kind: str) -> str | None:
        for s in self.socials:
            if s.type.lower() == kind.lower():
                return s.url
        return None


class MintInfo(BaseModel):
    """On-chain SPL mint facts (Stage 1 raw inputs)."""

    mint: str
    mint_authority: str | None = None  # None == renounced (good)
    freeze_authority: str | None = None  # None == cannot freeze (good)
    supply: int = 0
    decimals: int = 0
    is_initialized: bool = True
    top10_pct: float | None = None  # % held by top-10 non-infra holders; None=untrusted set
    top1_pct: float | None = None  # % held by the largest non-infra holder; None=untrusted set
    holders_sampled: int = 0


class FilterResult(BaseModel):
    """Stage 1 output. Any hard_flag => REJECT."""

    mint: str
    passed: bool
    hard_flags: list[str] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    safety_score: float = 0.0  # 0..1 (only meaningful if passed)
    # honeypot guard (ADR-012) — populated once the Jupiter client lands
    sell_simulated: bool = False
    sell_ok: bool | None = None
    round_trip_tax_pct: float | None = None


class SocialReport(BaseModel):
    """Stage 2 output — social/OSINT authenticity (all optional; graceful degradation)."""

    mint: str
    twitter_handle: str | None = None
    twitter_age_days: int | None = None
    handle_reuse_count: int | None = None  # times this account was attached to other tokens
    follower_quality: float | None = None  # 0..1 (real vs bot)
    notable_followers: int | None = None
    ca_tweet_verified: bool | None = None  # did the linked account tweet the contract address
    tg_unique_speakers: int | None = None
    chatter_texts: list[str] = Field(default_factory=list)  # fed to the LLM
    social_score: float | None = None  # 0..1 composite (computed in enrich)
    flags: list[str] = Field(default_factory=list)


class SmartMoneyReport(BaseModel):
    """Stage 3 output — smart-money + deployer reputation (ADR-008, ADR-014)."""

    mint: str
    smart_wallets_in: list[str] = Field(default_factory=list)
    best_wallet_winrate: float | None = None
    fresh_wallet_pct: float | None = None  # share of holders that are brand-new wallets
    deployer_rugged_before: bool | None = None
    deployer_prior_launches: int | None = None
    score: float | None = None  # 0..1 composite


class LlmSynthesis(BaseModel):
    """Stage 4 output — Qwen returns FEATURES ONLY, never a trade instruction."""

    summary: str = ""
    narrative_strength: float = 0.0  # 0..1
    community_authenticity: float = 0.0  # 0..1
    scam_language_flags: list[str] = Field(default_factory=list)
    notable_mentions: list[str] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    components: dict[str, float] = Field(default_factory=dict)  # raw 0..1 per signal
    weights_used: dict[str, float] = Field(default_factory=dict)  # renormalized over available
    composite: float = 0.0  # 0..100


class Decision(BaseModel):
    """Stage 5 output — the deterministic verdict (NOT the LLM's call)."""

    mint: str
    verdict: Verdict
    composite_score: float = 0.0  # 0..100
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    reasons: list[str] = Field(default_factory=list)
    veto_flags: list[str] = Field(default_factory=list)
    gate_met: bool = False  # True when a proven watchlist wallet is in (the "smart" confirmation)
    tier: str = ""  # "smart" (full size) | "quality" (half size) | "" = not a BUY
    llm_summary: str = ""  # Qwen's human commentary on the coin (analysis, not a trade order)
    position_size_sol: float = 0.0
    created_at: datetime = Field(default_factory=_utcnow)


class Fill(BaseModel):
    mint: str
    side: str  # buy | sell
    sol_amount: float
    token_amount: float
    price_usd: float | None = None
    paper: bool = True
    tx_sig: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class Position(BaseModel):
    mint: str
    token_amount: float
    avg_price_usd: float
    sol_invested: float
    opened_at: datetime = Field(default_factory=_utcnow)
    high_water_price: float | None = None
    entry_liquidity_usd: float | None = None  # ADR-037: to detect a liquidity collapse (rug) at exit
    closed: bool = False
    closed_at: datetime | None = None
    realized_pnl_sol: float = 0.0
