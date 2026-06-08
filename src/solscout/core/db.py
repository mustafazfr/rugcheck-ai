"""Persistence (aiosqlite). Store raw inputs + every decision so the whole funnel is replayable
(that is our backtester). Minimal schema for the read-only path; extended as stages land.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import aiosqlite

from .models import Decision, Fill, Position, TokenCandidate

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    mint TEXT PRIMARY KEY,
    source TEXT,
    creator TEXT,
    discovered_at TEXT,
    raw_meta TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT,
    verdict TEXT,
    composite_score REAL,
    breakdown TEXT,
    reasons TEXT,
    veto_flags TEXT,
    position_size_sol REAL,
    created_at TEXT,
    gate_met INTEGER DEFAULT 0,
    tier TEXT DEFAULT '',
    llm_summary TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT, side TEXT, sol_amount REAL, token_amount REAL,
    price_usd REAL, paper INTEGER, tx_sig TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS wallets_watchlist (
    address TEXT PRIMARY KEY,
    winrate REAL, realized_pnl_sol REAL, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT, token_amount REAL, avg_price_usd REAL, sol_invested REAL,
    opened_at TEXT, high_water_price REAL, entry_liquidity_usd REAL,
    closed INTEGER DEFAULT 0, closed_at TEXT, realized_pnl_sol REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reeval_queue (
    mint TEXT PRIMARY KEY,
    creator TEXT, first_seen TEXT, attempts INTEGER DEFAULT 0, next_at TEXT, symbol TEXT, meta TEXT
);
-- smart-money discovery (ADR-036): a wallet held this WINNER token (a trending mover we mined).
-- A wallet appearing across many DISTINCT winners = recurring smart money → promoted to the watchlist.
CREATE TABLE IF NOT EXISTS wallet_winners (
    wallet TEXT, mint TEXT, first_seen TEXT,
    PRIMARY KEY (wallet, mint)
);
CREATE INDEX IF NOT EXISTS idx_wallet_winners_wallet ON wallet_winners(wallet);
-- winners we've already mined (so discover doesn't re-fetch the same token's holders / re-spend credits)
CREATE TABLE IF NOT EXISTS mined_winners (
    mint TEXT PRIMARY KEY, mined_at TEXT
);
CREATE TABLE IF NOT EXISTS credit_usage (
    month TEXT PRIMARY KEY, credits REAL DEFAULT 0
);
"""


class Db:
    def __init__(self, path: str):
        self.path = path

    async def __aenter__(self) -> "Db":
        self._conn = await aiosqlite.connect(self.path)
        # WAL + busy_timeout: the `run` loop drives several coroutines that write concurrently.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(_SCHEMA)
        await self._migrate()
        await self._conn.commit()
        return self

    async def _migrate(self) -> None:
        """Add columns introduced after a table first shipped (CREATE IF NOT EXISTS won't backfill)."""
        for table, col, decl in [
            ("reeval_queue", "meta", "TEXT"),
            ("decisions", "gate_met", "INTEGER DEFAULT 0"),
            ("decisions", "tier", "TEXT DEFAULT ''"),
            ("decisions", "llm_summary", "TEXT"),
            ("positions", "entry_liquidity_usd", "REAL"),
        ]:
            cur = await self._conn.execute(f"PRAGMA table_info({table})")
            cols = {r[1] for r in await cur.fetchall()}
            if col not in cols:
                await self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                if (table, col) == ("decisions", "gate_met"):
                    # backfill: a logged BUY always implied the positive gate was met
                    await self._conn.execute("UPDATE decisions SET gate_met=1 WHERE verdict='BUY'")

    async def __aexit__(self, *exc) -> None:
        await self._conn.close()

    async def save_candidate(self, c: TokenCandidate) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO candidates VALUES (?,?,?,?,?)",
            (
                c.mint,
                c.source.value,
                c.creator,
                c.discovered_at.isoformat(),
                json.dumps(c.raw_meta),
            ),
        )
        await self._conn.commit()

    async def save_fill(self, f: Fill) -> None:
        await self._conn.execute(
            "INSERT INTO fills (mint, side, sol_amount, token_amount, price_usd, paper, tx_sig, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                f.mint,
                f.side,
                f.sol_amount,
                f.token_amount,
                f.price_usd,
                int(f.paper),
                f.tx_sig,
                f.created_at.isoformat(),
            ),
        )
        await self._conn.commit()

    async def save_decision(self, d: Decision) -> None:
        await self._conn.execute(
            "INSERT INTO decisions "
            "(mint, verdict, composite_score, breakdown, reasons, veto_flags, position_size_sol, created_at,"
            " gate_met, tier, llm_summary) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                d.mint,
                d.verdict.value,
                d.composite_score,
                d.breakdown.model_dump_json(),
                json.dumps(d.reasons),
                json.dumps(d.veto_flags),
                d.position_size_sol,
                d.created_at.isoformat(),
                int(d.gate_met),
                d.tier,
                d.llm_summary,
            ),
        )
        await self._conn.commit()

    async def last_buy_decision(self, mint: str) -> Decision | None:
        cur = await self._conn.execute(
            "SELECT mint, verdict, composite_score, breakdown, reasons, veto_flags, position_size_sol, "
            "created_at, COALESCE(gate_met,0), COALESCE(tier,'') FROM decisions WHERE mint=? AND verdict='BUY' "
            "ORDER BY id DESC LIMIT 1",
            (mint,),
        )
        r = await cur.fetchone()
        if not r:
            return None
        from .models import ScoreBreakdown, Verdict

        return Decision(
            mint=r[0],
            verdict=Verdict(r[1]),
            composite_score=r[2],
            breakdown=ScoreBreakdown.model_validate_json(r[3]) if r[3] else ScoreBreakdown(),
            reasons=json.loads(r[4] or "[]"),
            veto_flags=json.loads(r[5] or "[]"),
            position_size_sol=r[6],
            gate_met=bool(r[8]),
            tier=r[9],
        )

    async def add_wallet(self, address: str, winrate: float, realized_pnl_sol: float = 0.0) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO wallets_watchlist VALUES (?,?,?,?)",
            (address, winrate, realized_pnl_sol, datetime.now(timezone.utc).isoformat()),
        )
        await self._conn.commit()

    async def list_wallets(self) -> dict[str, float]:
        cur = await self._conn.execute("SELECT address, winrate FROM wallets_watchlist")
        return {addr: wr for addr, wr in await cur.fetchall()}

    async def open_position(self, p: Position) -> None:
        await self._conn.execute(
            "INSERT INTO positions (mint, token_amount, avg_price_usd, sol_invested, opened_at, "
            "high_water_price, entry_liquidity_usd, closed, closed_at, realized_pnl_sol) "
            "VALUES (?,?,?,?,?,?,?,0,NULL,0)",
            (
                p.mint,
                p.token_amount,
                p.avg_price_usd,
                p.sol_invested,
                p.opened_at.isoformat(),
                p.high_water_price,
                p.entry_liquidity_usd,
            ),
        )
        await self._conn.commit()

    async def get_open_positions(self) -> list[Position]:
        cur = await self._conn.execute(
            "SELECT mint, token_amount, avg_price_usd, sol_invested, opened_at, high_water_price, "
            "realized_pnl_sol, entry_liquidity_usd FROM positions WHERE closed=0"
        )
        return [
            Position(
                mint=r[0],
                token_amount=r[1],
                avg_price_usd=r[2],
                sol_invested=r[3],
                opened_at=datetime.fromisoformat(r[4]),
                high_water_price=r[5],
                realized_pnl_sol=r[6],
                entry_liquidity_usd=r[7],
            )
            for r in await cur.fetchall()
        ]

    async def holds(self, mint: str) -> bool:
        cur = await self._conn.execute(
            "SELECT 1 FROM positions WHERE mint=? AND closed=0 LIMIT 1", (mint,)
        )
        return await cur.fetchone() is not None

    async def close_position(self, p: Position) -> None:
        await self._conn.execute(
            "UPDATE positions SET closed=1, closed_at=?, realized_pnl_sol=?, high_water_price=? "
            "WHERE mint=? AND closed=0",
            (
                (p.closed_at or datetime.now(timezone.utc)).isoformat(),
                p.realized_pnl_sol,
                p.high_water_price,
                p.mint,
            ),
        )
        await self._conn.commit()

    async def realized_pnl_today(self) -> float:
        start = (
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
        )
        cur = await self._conn.execute(
            "SELECT COALESCE(SUM(realized_pnl_sol), 0) FROM positions WHERE closed=1 AND closed_at >= ?",
            (start,),
        )
        return float((await cur.fetchone())[0] or 0)

    # --- grace-period re-evaluation queue ---
    async def enqueue_reeval(
        self,
        mint: str,
        creator: str | None,
        next_at: datetime,
        symbol: str | None = None,
        meta: dict | None = None,
    ) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO reeval_queue (mint, creator, first_seen, attempts, next_at, symbol, meta) "
            "VALUES (?,?,?,0,?,?,?)",
            (
                mint,
                creator,
                datetime.now(timezone.utc).isoformat(),
                next_at.isoformat(),
                symbol,
                json.dumps(meta) if meta else None,
            ),
        )
        await self._conn.commit()

    async def due_reevals(self, now: datetime | None = None) -> list[dict]:
        ts = (now or datetime.now(timezone.utc)).isoformat()
        cur = await self._conn.execute(
            "SELECT mint, creator, attempts, symbol, meta FROM reeval_queue WHERE next_at <= ? ORDER BY next_at",
            (ts,),
        )
        return [
            {
                "mint": m,
                "creator": c,
                "attempts": a,
                "symbol": s,
                "meta": json.loads(mt) if mt else None,
            }
            for m, c, a, s, mt in await cur.fetchall()
        ]

    async def reschedule_reeval(self, mint: str, attempts: int, next_at: datetime) -> None:
        await self._conn.execute(
            "UPDATE reeval_queue SET attempts=?, next_at=? WHERE mint=?",
            (attempts, next_at.isoformat(), mint),
        )
        await self._conn.commit()

    async def drop_reeval(self, mint: str) -> None:
        await self._conn.execute("DELETE FROM reeval_queue WHERE mint=?", (mint,))
        await self._conn.commit()

    async def queue_size(self) -> int:
        cur = await self._conn.execute("SELECT COUNT(*) FROM reeval_queue")
        return int((await cur.fetchone())[0])

    # --- smart-money discovery (ADR-036): recurring holders across DISTINCT winners ---
    async def already_mined(self, mint: str) -> bool:
        cur = await self._conn.execute("SELECT 1 FROM mined_winners WHERE mint=? LIMIT 1", (mint,))
        return await cur.fetchone() is not None

    async def mark_mined(self, mint: str) -> None:
        await self._conn.execute(
            "INSERT OR REPLACE INTO mined_winners (mint, mined_at) VALUES (?,?)",
            (mint, datetime.now(timezone.utc).isoformat()),
        )
        await self._conn.commit()

    async def record_winner_holders(self, mint: str, wallets: list[str]) -> None:
        """Record that each wallet was a (trader-like) holder of winner `mint`. Idempotent per (wallet,mint)."""
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.executemany(
            "INSERT OR IGNORE INTO wallet_winners (wallet, mint, first_seen) VALUES (?,?,?)",
            [(w, mint, now) for w in wallets],
        )
        await self._conn.commit()

    async def recurring_wallets(
        self, min_distinct: int, max_distinct: int, lookback_days: int
    ) -> list[tuple[str, int]]:
        """Wallets seen across [min_distinct, max_distinct] DISTINCT winners in the rolling window →
        (wallet, count). The upper bound drops exchange/infra wallets that appear in everything."""
        since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        cur = await self._conn.execute(
            "SELECT wallet, COUNT(DISTINCT mint) c FROM wallet_winners WHERE first_seen >= ? "
            "GROUP BY wallet HAVING c >= ? AND c <= ? ORDER BY c DESC",
            (since, min_distinct, max_distinct),
        )
        return [(w, int(c)) for w, c in await cur.fetchall()]

    async def winner_link_count(self) -> int:
        cur = await self._conn.execute("SELECT COUNT(*) FROM wallet_winners")
        return int((await cur.fetchone())[0])

    async def recent_decision_summary(self, since_iso: str) -> dict:
        """Aggregate decisions logged since `since_iso` → counts for the FUNNEL observability line."""
        cur = await self._conn.execute(
            "SELECT verdict, COALESCE(tier,''), COALESCE(veto_flags,'[]') FROM decisions WHERE created_at >= ?",
            (since_iso,),
        )
        buy_smart = buy_quality = watch = reject = total = 0
        flags: dict[str, int] = {}
        for verdict, tier, veto_json in await cur.fetchall():
            total += 1
            if verdict == "BUY":
                buy_smart += tier == "smart"
                buy_quality += tier != "smart"
            elif verdict == "WATCH":
                watch += 1
            else:
                reject += 1
                for f in json.loads(veto_json or "[]"):
                    flags[f] = flags.get(f, 0) + 1
        top_flags = sorted(flags.items(), key=lambda x: -x[1])[:4]
        return {
            "total": total,
            "buy_smart": buy_smart,
            "buy_quality": buy_quality,
            "watch": watch,
            "reject": reject,
            "top_flags": top_flags,
        }

    # --- Helius credit meter (ADR-022): pace a monthly quota across the whole month ---
    async def get_credit_usage(self, month: str) -> float:
        cur = await self._conn.execute("SELECT credits FROM credit_usage WHERE month=?", (month,))
        row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def add_credit_usage(self, month: str, credits: float) -> None:
        await self._conn.execute(
            "INSERT INTO credit_usage (month, credits) VALUES (?,?) "
            "ON CONFLICT(month) DO UPDATE SET credits = credits + ?",
            (month, credits, credits),
        )
        await self._conn.commit()

    async def set_credit_usage(self, month: str, credits: float) -> None:
        """Align our estimate to the provider's REAL dashboard number (calibration)."""
        await self._conn.execute(
            "INSERT INTO credit_usage (month, credits) VALUES (?,?) "
            "ON CONFLICT(month) DO UPDATE SET credits = ?",
            (month, credits, credits),
        )
        await self._conn.commit()
