"""Analytics aggregation over a seeded SQLite db (reuses the real schema)."""

import sqlite3

import pytest

from solscout import analytics
from solscout.core.db import _SCHEMA


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "t.db")
    c = sqlite3.connect(p)
    c.executescript(_SCHEMA)
    # 3 decisions (m1 = a SMART-tier BUY), 1 closed winner + 1 closed loser, 1 open
    c.executescript("""
        INSERT INTO decisions (mint,verdict,composite_score,breakdown,reasons,veto_flags,position_size_sol,created_at,gate_met,tier)
        VALUES ('m1','BUY',80,'{}','[]','[]',0.2,'2026-06-01T10:00:00',1,'smart'),
               ('m2','WATCH',60,'{}','[]','[]',0,'2026-06-01T10:01:00',0,''),
               ('m3','REJECT',0,'{}','[]','[]',0,'2026-06-01T10:02:00',0,'');
        INSERT INTO positions (mint,token_amount,avg_price_usd,sol_invested,opened_at,closed,closed_at,realized_pnl_sol)
        VALUES ('m1',100,0.01,0.2,'2026-06-01T10:00:00',1,'2026-06-01T11:00:00',0.5),
               ('m4',100,0.01,0.2,'2026-06-01T10:00:00',1,'2026-06-01T11:00:00',-0.1),
               ('m5',100,0.01,0.2,'2026-06-01T10:00:00',0,NULL,0);
        INSERT INTO fills (mint,side,sol_amount,token_amount,price_usd,paper,tx_sig,created_at)
        VALUES ('m1','buy',0.2,100,0.01,1,NULL,'2026-06-01T10:00:00');
    """)
    c.commit()
    c.close()
    return p


def test_stats_aggregation(db_path):
    s = analytics.compute_stats(db_path)
    assert s.decisions_total == 3
    assert s.verdicts == {"BUY": 1, "WATCH": 1, "REJECT": 1}
    assert s.buys == 1
    assert s.closed_count == 2 and s.wins == 1 and s.losses == 1
    assert s.win_rate == 50.0
    assert s.realized_pnl_total == 0.4  # 0.5 - 0.1
    assert s.best_pnl == 0.5 and s.worst_pnl == -0.1
    assert s.open_count == 1 and s.open_exposure_sol == 0.2


def test_buy_tier_counts(db_path):
    s = analytics.compute_stats(db_path)
    assert s.buy_smart == 1 and s.buy_quality == 0


def test_tier_pnl_split(db_path):
    # m1 is a SMART-tier BUY with a closed winner (+0.5); the quality tier has no trades.
    s = analytics.compute_stats(db_path)
    assert s.tier_pnl["smart"] == {"trades": 1, "pnl": 0.5, "wins": 1}
    assert s.tier_pnl["quality"]["trades"] == 0


def test_health_notes_flag_empty_watchlist(db_path):
    notes = analytics.compute_stats(db_path).health_notes
    assert any("Watchlist empty" in n for n in notes)


def test_threshold_sensitivity(db_path):
    rows = {r["threshold"]: r for r in analytics.threshold_sensitivity(db_path)}
    # m1(BUY,80) m2(WATCH,60). 'bought' counts actual BUY verdicts at score>=T → only m1.
    assert rows[50]["score_reached"] == 2  # 80 and 60 reached >=50
    assert rows[50]["bought"] == 1  # only m1 is a BUY
    assert rows[50]["smart_at_T"] == 1  # and it's the smart tier
    assert rows[70]["bought"] == 1
    assert rows[90]["bought"] == 0


def test_missing_db_is_empty_not_error(tmp_path):
    s = analytics.compute_stats(str(tmp_path / "nope.db"))
    assert s.decisions_total == 0 and s.win_rate is None


def test_top_watch_ranks_by_score(db_path):
    rows = analytics.top_watch(db_path, 10)
    assert [r["mint"] for r in rows] == ["m1", "m2"]
    assert rows[0]["score"] == 80


def test_render_dashboard_html(db_path, monkeypatch):
    from solscout import dashboard

    # keep the test hermetic — no live DexScreener call for open-position prices
    monkeypatch.setattr(dashboard, "_live_prices", lambda mints: {m: None for m in mints})
    out = dashboard.render(db_path)
    assert "SolScout" in out and "Win rate" in out and "<table" in out
    assert "Top clean coins" in out
    assert "Geçmiş işlemler" in out  # closed-trade history table
    assert "Açık pozisyonlar" in out  # live open-position table
