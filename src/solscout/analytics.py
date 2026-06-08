"""Read-only analytics over the SQLite log — powers `stats`, `dashboard`, and `backtest`.

Uses stdlib sqlite3 (sync) so the threaded dashboard server and the CLI share ONE set of queries.
Everything here is reporting only; it never writes. Honest by construction: if no trades happened,
the numbers say so. The tier split (smart vs quality) answers "which edge actually works on paper?".
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field


@dataclass
class Stats:
    db_path: str
    first_decision: str | None = None
    last_decision: str | None = None
    candidates_total: int = 0
    decisions_total: int = 0
    verdicts: dict[str, int] = field(default_factory=dict)
    buy_smart: int = 0  # BUY decisions where a proven watchlist wallet was in (smart tier)
    buy_quality: int = 0  # BUY decisions on quality alone (no smart money)
    tier_pnl: dict[str, dict] = field(
        default_factory=dict
    )  # {tier: {trades, wins, pnl}} over closed positions
    buys: int = 0
    sells: int = 0
    open_count: int = 0
    open_exposure_sol: float = 0.0
    closed_count: int = 0
    wins: int = 0
    losses: int = 0
    realized_pnl_total: float = 0.0
    realized_pnl_today: float = 0.0
    best_pnl: float = 0.0
    worst_pnl: float = 0.0
    avg_score_buy: float | None = None
    watchlist_size: int = 0
    queue_size: int = 0
    credits_used_month: float = 0.0

    @property
    def win_rate(self) -> float | None:
        return round(100 * self.wins / self.closed_count, 1) if self.closed_count else None

    @property
    def avg_pnl(self) -> float | None:
        return round(self.realized_pnl_total / self.closed_count, 4) if self.closed_count else None

    @property
    def health_notes(self) -> list[str]:
        """Honest diagnostics — why might we be collecting no trade data?"""
        n = []
        if self.decisions_total == 0:
            n.append("No decisions logged yet — run `scan`/`run` first.")
        if self.watchlist_size == 0:
            n.append(
                "Watchlist empty → no SMART-tier (copy-a-winner) buys yet, only QUALITY-tier. "
                "Run `solscout discover` (free) to fill it from recurring winner-holders."
            )
        if self.verdicts.get("BUY", 0) == 0 and self.decisions_total > 20:
            n.append(
                "Zero BUY verdicts despite many decisions — thresholds may be too high for current "
                "candidates, or candidates are low quality. Check the FUNNEL reject reasons."
            )
        if self.closed_count == 0 and self.open_count == 0 and self.decisions_total > 0:
            n.append(
                "No paper positions opened yet → no PnL to validate. Quality-tier buys need "
                "score ≥ quality_buy_min_score; smart-tier needs a discovered wallet in the token."
            )
        return n


def _conn(db_path: str) -> sqlite3.Connection:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c


def _scalar(c: sqlite3.Connection, sql: str, default=0):
    try:
        row = c.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default
    except sqlite3.OperationalError:
        return default  # table not created yet


def compute_stats(db_path: str) -> Stats:
    s = Stats(db_path=db_path)
    try:
        c = _conn(db_path)
    except sqlite3.OperationalError:
        return s
    with c:
        s.candidates_total = _scalar(c, "SELECT COUNT(*) FROM candidates")
        s.decisions_total = _scalar(c, "SELECT COUNT(*) FROM decisions")
        s.first_decision = _scalar(c, "SELECT MIN(created_at) FROM decisions", None)
        s.last_decision = _scalar(c, "SELECT MAX(created_at) FROM decisions", None)
        try:
            for row in c.execute("SELECT verdict, COUNT(*) n FROM decisions GROUP BY verdict"):
                s.verdicts[row["verdict"]] = row["n"]
        except sqlite3.OperationalError:
            pass
        s.avg_score_buy = _scalar(
            c, "SELECT AVG(composite_score) FROM decisions WHERE verdict='BUY'", None
        )
        s.buy_smart = _scalar(
            c, "SELECT COUNT(*) FROM decisions WHERE verdict='BUY' AND tier='smart'"
        )
        s.buy_quality = _scalar(
            c, "SELECT COUNT(*) FROM decisions WHERE verdict='BUY' AND tier='quality'"
        )
        # tier split of CLOSED-trade PnL (join each position to its mint's latest BUY tier) — the honest
        # "does either edge actually work?" table. Smart = copy-a-winner; quality = our own scoring alone.
        for tier in ("smart", "quality"):
            try:
                row = c.execute(
                    "SELECT COUNT(*), COALESCE(SUM(realized_pnl_sol),0), "
                    "       COALESCE(SUM(realized_pnl_sol>0),0) "
                    "FROM positions p WHERE closed=1 AND ("
                    "  SELECT tier FROM decisions d WHERE d.mint=p.mint AND d.verdict='BUY' "
                    "  ORDER BY d.id DESC LIMIT 1)=?",
                    (tier,),
                ).fetchone()
                s.tier_pnl[tier] = {
                    "trades": int(row[0]),
                    "pnl": round(float(row[1]), 4),
                    "wins": int(row[2]),
                }
            except sqlite3.OperationalError:
                s.tier_pnl[tier] = {"trades": 0, "pnl": 0.0, "wins": 0}
        s.buys = _scalar(c, "SELECT COUNT(*) FROM fills WHERE side='buy'")
        s.sells = _scalar(c, "SELECT COUNT(*) FROM fills WHERE side='sell'")
        s.open_count = _scalar(c, "SELECT COUNT(*) FROM positions WHERE closed=0")
        s.open_exposure_sol = round(
            _scalar(c, "SELECT SUM(sol_invested) FROM positions WHERE closed=0", 0.0), 4
        )
        s.closed_count = _scalar(c, "SELECT COUNT(*) FROM positions WHERE closed=1")
        s.wins = _scalar(
            c, "SELECT COUNT(*) FROM positions WHERE closed=1 AND realized_pnl_sol > 0"
        )
        s.losses = _scalar(
            c, "SELECT COUNT(*) FROM positions WHERE closed=1 AND realized_pnl_sol <= 0"
        )
        s.realized_pnl_total = round(
            _scalar(c, "SELECT SUM(realized_pnl_sol) FROM positions WHERE closed=1", 0.0), 4
        )
        s.best_pnl = round(
            _scalar(c, "SELECT MAX(realized_pnl_sol) FROM positions WHERE closed=1", 0.0), 4
        )
        s.worst_pnl = round(
            _scalar(c, "SELECT MIN(realized_pnl_sol) FROM positions WHERE closed=1", 0.0), 4
        )
        s.watchlist_size = _scalar(c, "SELECT COUNT(*) FROM wallets_watchlist")
        s.queue_size = _scalar(c, "SELECT COUNT(*) FROM reeval_queue")
        from datetime import datetime, timezone

        mk = datetime.now(timezone.utc).strftime("%Y-%m")
        s.credits_used_month = float(
            _scalar(c, f"SELECT credits FROM credit_usage WHERE month='{mk}'", 0.0)
        )
        start = "date('now')"
        s.realized_pnl_today = round(
            _scalar(
                c,
                f"SELECT SUM(realized_pnl_sol) FROM positions WHERE closed=1 AND closed_at >= {start}",
                0.0,
            ),
            4,
        )
    return s


def recent_decisions(db_path: str, limit: int = 25) -> list[dict]:
    try:
        c = _conn(db_path)
    except sqlite3.OperationalError:
        return []
    with c:
        try:
            rows = c.execute(
                "SELECT mint, verdict, composite_score, created_at FROM decisions "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


def top_watch(db_path: str, limit: int = 15) -> list[dict]:
    """Highest-scoring WATCH coins (deduped by mint, best score each) — the curated 'clean coins' list."""
    try:
        c = _conn(db_path)
        try:
            # Latest decision per mint that CLEARED the hard gates (no veto: not a rug, not no_opportunity)
            # and has a positive score — ranked by score. This is the analyst's shortlist of viable coins;
            # it stays populated and ranks best-first, instead of being gated by an arbitrary WATCH cutoff.
            rows = c.execute(
                "SELECT mint, composite_score, created_at, COALESCE(llm_summary,'') FROM decisions d "
                "WHERE id = (SELECT MAX(id) FROM decisions WHERE mint=d.mint) "
                "  AND (veto_flags IN ('[]','') OR veto_flags IS NULL) AND composite_score > 0 "
                "ORDER BY composite_score DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        c.close()
    except sqlite3.OperationalError:
        return []
    return [{"mint": r[0], "score": r[1], "ts": r[2], "llm": r[3]} for r in rows]


def open_positions(db_path: str) -> list[dict]:
    try:
        c = _conn(db_path)
    except sqlite3.OperationalError:
        return []
    with c:
        try:
            rows = c.execute(
                "SELECT mint, sol_invested, avg_price_usd, opened_at FROM positions "
                "WHERE closed=0 ORDER BY id DESC"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


def reject_reasons(db_path: str, limit: int = 12) -> list[dict]:
    """Why coins were REJECTed — the veto-flag histogram (rug + manipulation reasons). Shows the funnel
    is actively protecting us (ADR-037)."""
    try:
        c = _conn(db_path)
        try:
            rows = c.execute("SELECT veto_flags FROM decisions WHERE verdict='REJECT'").fetchall()
        except sqlite3.OperationalError:
            rows = []
        c.close()
    except sqlite3.OperationalError:
        return []
    counter: dict[str, int] = {}
    for (vj,) in rows:
        for f in json.loads(vj or "[]"):
            counter[f] = counter.get(f, 0) + 1
    return [{"flag": k, "n": v} for k, v in sorted(counter.items(), key=lambda x: -x[1])[:limit]]


def closed_trades(db_path: str, limit: int = 50) -> list[dict]:
    """Trade HISTORY: each closed paper position with entry price, exit price (its sell fill), invested SOL,
    realized PnL, and open/close times — what the dashboard 'past trades' table shows."""
    try:
        c = _conn(db_path)
    except sqlite3.OperationalError:
        return []
    with c:
        try:
            rows = c.execute(
                "SELECT p.mint, p.avg_price_usd AS entry, p.sol_invested, p.realized_pnl_sol, "
                "       p.opened_at, p.closed_at, "
                "       (SELECT f.price_usd FROM fills f WHERE f.mint=p.mint AND f.side='sell' "
                "        ORDER BY f.id DESC LIMIT 1) AS exit_price "
                "FROM positions p WHERE p.closed=1 ORDER BY p.closed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [dict(r) for r in rows]


def threshold_sensitivity(db_path: str) -> list[dict]:
    """Backtest-lite: at each candidate score threshold, how many logged decisions reach it, and how many
    were actual BUYs? (ADR-036: BUY no longer needs a positive gate — it needs score≥tier-bar + opportunity
    + maturity. `smart_at_T` shows how many of those reaching T had smart-money confirmation.)"""
    try:
        c = _conn(db_path)
        try:
            rows = [
                (r[0], r[1], r[2])
                for r in c.execute(
                    "SELECT composite_score, COALESCE(verdict,''), COALESCE(tier,'') FROM decisions"
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            rows = [
                (r[0], "", "")
                for r in c.execute("SELECT composite_score FROM decisions").fetchall()
            ]
        c.close()
    except sqlite3.OperationalError:
        return []
    out = []
    for th in (50, 60, 70, 80, 90):
        reached = sum(1 for sc, _, _ in rows if sc is not None and sc >= th)
        bought = sum(1 for sc, v, _ in rows if sc is not None and sc >= th and v == "BUY")
        smart = sum(
            1 for sc, v, t in rows if sc is not None and sc >= th and v == "BUY" and t == "smart"
        )
        out.append(
            {"threshold": th, "bought": bought, "score_reached": reached, "smart_at_T": smart}
        )
    return out
