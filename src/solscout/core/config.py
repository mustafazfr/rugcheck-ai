"""Config + secrets loading. Config from YAML (no magic numbers in code); secrets from .env.

from solscout.core.config import load
cfg, secrets = load()            # reads config/config.yaml (or .example) + .env
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import ExecutionMode

REPO_ROOT = Path(__file__).resolve().parents[3]


class HeliusCfg(BaseModel):
    cache_ttl_s: float = (
        600  # memoize mint-info/holders per mint so recheck doesn't re-spend credits
    )
    # ADR-022 credit governor: pace a monthly quota across the whole month so it lasts 24/7.
    # Split the ~1M free budget between the two consumers (with a safety margin under 1M).
    monthly_budget_funnel: float = 650_000  # the run/scan/report funnel (holder calls)
    monthly_budget_discover: float = 250_000  # auto-discovery (holders + per-holder txns)
    cost_per_call: float = 10.0  # approx credits per DAS/enhanced call (tune to your plan)
    cost_per_gpa: float = 5.0  # approx credits per getProgramAccounts holder call (measured ~4)


class LlmCfg(BaseModel):
    # 127.0.0.1, NOT "localhost": on macOS `localhost` can resolve to IPv6 ::1 (or hit the Ollama.app
    # menubar shim) and the httpx-based client gets a 502 while curl silently falls back to IPv4. Pinning
    # the IPv4 loopback makes the local-LLM path reliable (the analyst was silently dropping to rules).
    host: str = "http://127.0.0.1:11434"
    synthesis_model: str = "qwen2.5:latest"
    classify_model: str = "llama3.2:3b"
    # ADR-042: the web "AI analyst" reasons over the FULL forensic report. A bigger local model gives much
    # better verdicts; we try `analyst_model` first and fall back to `synthesis_model` if it isn't pulled.
    analyst_model: str = "qwen2.5:14b"
    request_timeout_s: int = 60
    fail_open_to_rules: bool = True


class GraceCfg(BaseModel):
    enabled: bool = True
    first_delay_s: int = 300  # wait before first re-eval — covers DexScreener indexing + maturity
    max_attempts: int = 5  # give up after this many re-evals
    backoff_mult: float = (
        1.6  # 300s,480s,768s,1229s,1966s ≈ up to ~80 min total (covers 15-min maturity)
    )


class IngestCfg(BaseModel):
    # Candidate source is the FREE/keyless stack (ADR-036): GeckoTerminal new_pools + DexScreener
    # boosts/profiles. No pump.fun, no SOL/USDC search noise. Poll cadence lives under `geckoterminal`.
    min_initial_liquidity_usd: float = 2000
    # only analyze coins with real traction & a readable DEX market — quality over quantity (free pre-screen)
    min_liquidity_to_analyze: float = 15000
    grace: GraceCfg = Field(default_factory=GraceCfg)
    # CREDIT DIET (ADR-020): the Helius free tier is ~1M credits/MONTH and holder/DAS calls are metered.
    # PENDING tokens cost only a cheap public-RPC call; holders are cached + governed. Cap analyses/min.
    max_analyses_per_min: int = 30  # burst cap on Helius-funnel work (holders ~1 credit via gPA)


class GeckoTerminalCfg(BaseModel):
    """GeckoTerminal API — FREE, no key (~30 req/min). Our fresh-token + winners source (ADR-036)."""

    network: str = "solana"
    poll_s: int = 30  # how often to pull new_pools for the funnel
    new_pools_pages: int = 1  # 1 page ≈ 20 newest pools per poll (stay well under the rate limit)


class DiscoveryCfg(BaseModel):
    """Smart-money discovery = 'recurring holders across unrelated winners' (ADR-036). Free: GeckoTerminal
    trending winners + Helius holder sets. A wallet in >=min_distinct_winners DISTINCT recent winners is
    treated as smart money. Backward-looking (fills in minutes) AND compounds forward."""

    enabled: bool = True
    cycle_s: int = 90  # pull trending winners this often
    winners_per_cycle: int = 6  # cap NEW winners we fetch holders for per cycle (credit budget)
    top_holders: int = 40  # holders per winner to consider as candidate smart money
    min_distinct_winners: int = 2  # appear in >= this many DISTINCT winners → promote to watchlist
    max_distinct_winners: int = (
        25  # appear in MORE than this → it's an exchange/infra wallet, NOT alpha → drop
    )
    lookback_days: int = 14  # rolling window for "distinct winners"
    vet_finalists: bool = False  # optional 1 Helius enhanced-call/finalist to drop obvious bots/MMs
    # what counts as a "winner" worth mining (real, alive, still small enough that early holders were sharp)
    winner_min_liquidity_usd: float = 20000
    winner_min_volume_24h_usd: float = 50000
    winner_max_age_days: float = 30
    # ADR-037: don't mine MANIPULATED winners (wash-traded / one-way pumps) — they'd seed bad wallets.
    winner_min_seller_share: float = 0.10  # unique sellers/(buyers+sellers) must be ≥ this
    winner_max_vol_liq_ratio: float = 30.0  # volume_24h/liquidity above this = wash → not a real winner


class FiltersCfg(BaseModel):
    require_mint_renounced: bool = True
    require_freeze_null: bool = True
    require_lp_locked_or_burned: bool = True
    # Concentration is NO LONGER a blanket hard veto (ADR-036): new memes are concentrated by nature, so
    # vetoing at 35% rejected every opportunity. `max_top10_holder_pct` is now only the knee of the graded
    # safety score. We HARD-veto only genuinely dangerous, TRUSTWORTHY concentration:
    max_top10_holder_pct: float = (
        35  # score knee (not a veto): top-10% above this drags safety down
    )
    extreme_top10_pct: float = (
        90  # HARD veto: top-10 holds ~everything (trustworthy set, infra excluded)
    )
    single_wallet_max_pct: float = 50  # HARD veto: one non-infra wallet can dump the whole market
    max_dev_hold_pct: float = 10
    max_bundle_pct: float = 25
    bundle_window_blocks: int = 20
    min_liquidity_usd: float = 5000
    min_pair_age_minutes: int = (
        15  # maturity gate: don't BUY until the pair is this old (rug signals surface)
    )
    min_volume_24h_usd: float = 3000  # below this = effectively dead / untraded
    min_liquidity_for_buy_usd: float = 25000  # ADR-037: a BUY needs a real, exitable pool (stricter than analyze floor)
    exclude_pump_origin: bool = False  # ADR-038: True = reject ALL pump.fun-origin coins (…pump mints / PumpSwap) — much quieter funnel
    require_sell_simulation: bool = (
        False  # honeypot guard via Jupiter round-trip quote (free); off until verified
    )
    max_buy_sell_tax_pct: float = 15


class SocialCfg(BaseModel):
    max_twitter_handle_reuse: int = 0
    min_twitter_account_age_days: int = 30
    require_ca_tweet_proof: bool = False
    min_followers: int = 200
    min_tg_unique_speakers: int = 15


class SmartMoneyCfg(BaseModel):
    # QUALITY bar: only wallets proven across >= discovery.min_distinct_winners winners count
    # (winrate_from_winners: 2→0.66). A single such wallet in a token = the "smart" BUY tier.
    watchlist_min_winrate: float = 0.66
    reject_if_deployer_rugged_before: bool = True
    check_deployer: bool = (
        False  # credit diet: deployer history = 1 expensive Helius enhanced call/launch
    )
    min_wallets_for_gate: int = 1  # >= this many proven wallets in → "smart" tier (full-size BUY)


class ScoringCfg(BaseModel):
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "safety": 0.30,
            "smart_money": 0.30,
            "social": 0.25,
            "narrative": 0.15,
        }
    )
    # TWO BUY tiers (ADR-036). Smart tier (a proven watchlist wallet is in) buys at `buy_threshold` and full
    # size. Quality tier (no smart money, but safe + liquid + momentum + LLM-clean) needs a HIGHER bar and
    # buys at a fraction of the size. Smart-money is a BOOST + size multiplier, never a hard gate.
    buy_threshold: float = 62  # smart-tier BUY bar
    quality_buy_min_score: float = 68  # quality-tier (no smart money) BUY bar — stricter
    quality_size_mult: float = (
        0.5  # quality-tier position size = this × normal (smaller bets while we measure)
    )
    watch_threshold: float = 45
    # OPPORTUNITY zone (money focus): a coin is only surfaced (WATCH/BUY) if it can still multiply —
    # right size + real momentum + not ancient. Established/old/dead coins are hidden UNLESS a smart-money
    # buy signal fires. Tuned for meme-coin upside, not blue chips.
    opp_min_market_cap_usd: float = 30_000  # below = dust
    opp_max_market_cap_usd: float = 10_000_000  # above = too big to multiply (mega/large-cap)
    opp_min_volume_ratio: float = 0.30  # 24h volume must be >= this * liquidity (real trading)
    opp_max_age_days: float = 7  # older = established; easy money gone (show only on signal)
    # the LLM is our best rug detector — if Qwen flags scam language OR judges the community fake, HARD-CAP
    # the score so obvious rugs can't score ~50 just for having renounced authorities + some liquidity.
    llm_scam_score_cap: float = 30  # capped score when Qwen flags an OBVIOUS rug
    llm_min_authenticity: float = 0.20  # authenticity below this = genuinely fake community → cap


class ExecutionCfg(BaseModel):
    mode: ExecutionMode = ExecutionMode.PAPER
    semi_auto_min_score: float = 75
    per_trade_cap_sol: float = 0.25
    daily_loss_limit_sol: float = 1.0
    max_open_positions: int = 5
    max_total_exposure_sol: float = 2.0
    risk_fraction: float = 0.1
    max_slippage_bps: int = 300
    # ADR-037: never bet more than this % of a pool's liquidity — a trade that big can't fill at the quoted
    # price (we'd move the market). Bounds buy impact AND our exposure to thin/manipulated pools.
    max_pool_share_pct: float = 1.0
    priority_fee_microlamports: int = 100000
    use_jito_bundle: bool = True


class PositionCfg(BaseModel):
    take_profit_pct: list[float] = Field(default_factory=lambda: [50, 100, 300])
    stop_loss_pct: float = 35
    trailing_stop_pct: float = 25
    time_stop_minutes: int = 240
    exit_if_smart_money_exits: bool = True
    # ADR-037 rug exit: if the pool's liquidity collapses, you CAN'T sell — book it as a (near-total) loss
    # instead of a fictional fill. Fires when current liquidity < rug_liquidity_usd OR dropped more than
    # rug_liquidity_drop_pct from entry.
    rug_liquidity_usd: float = 2000
    rug_liquidity_drop_pct: float = 70


class ManipulationCfg(BaseModel):
    """ADR-037 — anti-rug/anti-manipulation ENTRY filters. Catch wash-traded / honeypot-shaped / low-float
    pumps (the SPCX/BARRON/SV151 pattern) BEFORE buying. Signals are free: DexScreener txns + GeckoTerminal
    unique buyers/sellers. Strongest flags are hard vetoes; the rest feed a score penalty."""

    enabled: bool = True
    # lopsided flow: almost everyone buying, ~nobody selling → honeypot / one-way pump (BARRON: 33 sellers
    # vs 1696 buyers ≈ 2%). Only judged when enough unique traders exist to be meaningful.
    min_unique_traders: int = 50  # below this, buyers/sellers data is too thin to judge
    min_seller_share: float = 0.08  # sellers/(buyers+sellers) must be ≥ this
    # wash volume: absurd turnover vs the pool size = fake churn to fake momentum
    max_vol_liq_ratio: float = 25.0  # volume_24h / liquidity above this = wash (hard veto when also thin)
    wash_liquidity_usd: float = 60000  # "thin" pool ceiling for the wash hard-veto
    # buy/sell TXN imbalance (a softer signal — penalty, not veto)
    max_txn_imbalance: float = 6.0  # max(buys,sells)/min(buys,sells)
    # hyper-pump: brand-new + vertical price move on a thin pool = manipulation
    young_minutes: float = 90
    max_young_pump_pct: float = 400  # price_change_24h above this on a young thin pool = veto
    young_pump_liquidity_usd: float = 80000
    # low float: too little supply circulates outside the pool → trivially pumpable / rug-prone
    min_float_pct: float = 8.0  # non-pool circulating supply must be ≥ this % (when holder set is trusted)
    soft_penalty: float = 0.6  # multiply safety_score by this when a SOFT manipulation flag fires
    # REAL-ACTIVITY floor (ADR-038): a token must be genuinely, two-sidedly traded — not a 1-holder husk or
    # a 0-seller honeypot (WIF 37 holders/1 txn; BARRON 3 buyers/0 sellers; IRAN 1/0 — all bought before).
    min_holders: int = 50  # reject when the (trusted) holder count is below this
    min_traders: int = 40  # reject when unique buyers+sellers (24h) is below this
    no_seller_min_buyers: int = 10  # if there are ≥ this many buyers but ZERO sellers → honeypot/one-way


class ClusterCfg(BaseModel):
    """ADR-038 — bundle / sybil-cluster detection (the Bubblemaps signal), FREE via Helius funding sources.
    For a BUY candidate, fetch the top holders' funders; if many top holders were funded by the SAME wallet
    they're one entity's sybils (a bundled rug), not real distribution that holder-concentration can't see.
    Runs ONLY on BUY candidates (few) to stay credit-cheap; funders are cached per wallet across tokens."""

    enabled: bool = True
    top_n: int = 12  # how many top holders to trace funding for
    tx_limit: int = 100  # enhanced-tx history depth per holder
    min_cluster_size: int = 4  # this many top holders sharing ONE funder = a bundle
    max_funder_share: float = 0.35  # OR a single funder backing ≥ this fraction of the traced holders


class RugCheckCfg(BaseModel):
    """ADR-039 — RugCheck.xyz aggregated rug analysis (FREE, no key). An independent risk read that
    complements our on-chain checks (LP-lock, insider graph, overall score). Flaky third party → fail open."""

    enabled: bool = True
    veto_score: int = 60  # score_normalised ≥ this → HARD veto (BONK≈7; a dead husk≈71)
    soft_score: int = 35  # score in [soft_score, veto_score) → safety-score penalty
    max_insider_holders: int = 3  # this many RugCheck-flagged insider top-holders → bundle veto
    # named danger-risks that always veto (substring, case-insensitive). LP-unlock is folded into the score.
    critical_risks: list[str] = Field(
        default_factory=lambda: ["honeypot", "can't sell", "cannot sell", "freeze authority enabled"]
    )
    trust_insiders: bool = True  # use RugCheck's insider graph instead of our Helius funder-trace (saves credits)


class GoPlusCfg(BaseModel):
    """ADR-041 — GoPlus Security (FREE, no key). An INDEPENDENT 2nd rug source next to RugCheck: authorities,
    transfer-hook / non-transferable (Token-2022 honeypots), transfer-fee, malicious-creator flag. Fail-open."""

    enabled: bool = True
    max_transfer_fee_pct: float = 10.0  # transfer fee above this = a tax trap → hard veto


class TwitterCfg(BaseModel):
    """ADR-041 — keyless Twitter/X profile intel (fxtwitter). Account age / followers / tweets = real
    authenticity signals (scammers reuse brand-new, low-follower, no-tweet accounts)."""

    enabled: bool = True
    min_age_days: int = 30  # younger than this = brand-new account flag
    mature_age_days: int = 365  # age contribution saturates here
    min_followers: int = 500
    min_tweets: int = 20


class DeployerCfg(BaseModel):
    """ADR-041 — deep deployer/creator forensics (Helius). Serial-deployer (made & abandoned many tokens) and
    fresh/mixer funding are top rug signals. Runs on the web path (credit-governed, cached)."""

    enabled: bool = True
    tx_limit: int = 100  # enhanced-tx history depth for the creator wallet
    serial_creator_min: int = 4  # this many prior token creations by the creator → serial-deployer flag
    holders_intel_top_n: int = 8  # how many top holders to profile for fresh-wallet / trader history
    holder_tx_limit: int = 60
    fresh_buyer_min: int = 5  # this many of the profiled top holders being fresh wallets → cluster flag


class RunCfg(BaseModel):
    """Continuous-loop knobs (the unattended `run` command)."""

    summary_every_s: int = 60  # emit a rolling FUNNEL summary line this often (observability)


class StorageCfg(BaseModel):
    db_path: str = "./solscout.db"


class AlertsCfg(BaseModel):
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


class Config(BaseModel):
    cost_tier: str = "free"
    # Solana RPC for the CHEAP calls (mint authorities/supply). Empty => free public mainnet RPC.
    # Point this at ANY provider (QuickNode/Ankr/Alchemy/your node) to avoid leaning on Helius credits.
    # Helius is used ONLY for the DAS holder call (its credit-metered specialty).
    solana_rpc_url: str = ""
    helius: HeliusCfg = Field(default_factory=HeliusCfg)
    llm: LlmCfg = Field(default_factory=LlmCfg)
    geckoterminal: GeckoTerminalCfg = Field(default_factory=GeckoTerminalCfg)
    ingest: IngestCfg = Field(default_factory=IngestCfg)
    discovery: DiscoveryCfg = Field(default_factory=DiscoveryCfg)
    filters: FiltersCfg = Field(default_factory=FiltersCfg)
    manipulation: ManipulationCfg = Field(default_factory=ManipulationCfg)
    cluster: ClusterCfg = Field(default_factory=ClusterCfg)
    rugcheck: RugCheckCfg = Field(default_factory=RugCheckCfg)
    goplus: GoPlusCfg = Field(default_factory=GoPlusCfg)
    twitter: TwitterCfg = Field(default_factory=TwitterCfg)
    deployer: DeployerCfg = Field(default_factory=DeployerCfg)
    social: SocialCfg = Field(default_factory=SocialCfg)
    smart_money: SmartMoneyCfg = Field(default_factory=SmartMoneyCfg)
    scoring: ScoringCfg = Field(default_factory=ScoringCfg)
    execution: ExecutionCfg = Field(default_factory=ExecutionCfg)
    position: PositionCfg = Field(default_factory=PositionCfg)
    run: RunCfg = Field(default_factory=RunCfg)
    storage: StorageCfg = Field(default_factory=StorageCfg)
    alerts: AlertsCfg = Field(default_factory=AlertsCfg)


class Secrets(BaseSettings):
    """Loaded from .env / environment. Live trading is OFF unless allow_live=1 (and execution.mode=live)."""

    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore")

    helius_api_key: str = ""
    rugcheck_api_key: str = ""
    tweetscout_api_key: str = ""
    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    cielo_api_key: str = ""
    solscout_allow_live: str = ""
    hot_wallet_private_key: str = ""

    @property
    def live_allowed(self) -> bool:
        return self.solscout_allow_live.strip() == "1"


def _config_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    real = REPO_ROOT / "config" / "config.yaml"
    return real if real.exists() else REPO_ROOT / "config" / "config.example.yaml"


def load(path: str | None = None) -> tuple[Config, Secrets]:
    cfg_path = _config_path(path)
    data = yaml.safe_load(cfg_path.read_text()) or {}
    return Config(**data), Secrets()
