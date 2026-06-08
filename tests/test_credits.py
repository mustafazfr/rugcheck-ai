"""Credit governor (ADR-022) — paces a monthly quota so it can't run out before month end."""

from datetime import datetime, timezone

from solscout.core.credits import CreditGovernor, month_bounds, month_key


def _gov(budget, used, day, hour=0):
    now = datetime(2026, 6, day, hour, tzinfo=timezone.utc)  # June has 30 days
    return CreditGovernor(budget, used=used, now=now, head_start=0.0), now


def test_month_bounds_june():
    start, end = month_bounds(datetime(2026, 6, 15, tzinfo=timezone.utc))
    assert start == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 1, tzinfo=timezone.utc)


def test_paced_allowance_midmonth():
    # day 16 ~ halfway through June (15/30 days elapsed) → ~half the budget allowed
    g, now = _gov(1_000_000, 0, day=16)
    assert abs(g.paced_allowance(now) - 500_000) < 20_000


def test_blocks_when_ahead_of_pace():
    # halfway through the month but already spent 600k of 1M → over pace → cannot spend more now
    g, now = _gov(1_000_000, 600_000, day=16)
    assert not g.can_spend(10, now)


def test_allows_when_behind_pace():
    # halfway but only spent 100k → well behind pace → may spend
    g, now = _gov(1_000_000, 100_000, day=16)
    assert g.can_spend(10, now)


def test_never_exceeds_total_budget_even_at_month_end():
    g, now = _gov(1_000_000, 1_000_000, day=30, hour=23)
    assert not g.can_spend(1, now)  # quota fully used → blocked regardless of pace


def test_note_accumulates():
    g, _ = _gov(1000, 0, day=1)
    g.note(10)
    g.note(5)
    assert g.used == 15 and g.remaining() == 985


def test_head_start_buffer_allows_early_spend():
    # very start of month with a 2% head-start buffer → small spend allowed despite ~0 elapsed
    now = datetime(2026, 6, 1, 0, 1, tzinfo=timezone.utc)
    g = CreditGovernor(1_000_000, used=0, now=now, head_start=0.02)
    assert g.can_spend(10, now)  # 2% buffer = 20k headroom


def test_month_key_format():
    assert month_key(datetime(2026, 6, 3, tzinfo=timezone.utc)) == "2026-06"
