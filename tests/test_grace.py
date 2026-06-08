"""Grace-period re-eval scheduling is pure → unit-tested."""

from datetime import datetime, timezone

from solscout.core.config import GraceCfg
from solscout.ingest import grace

# explicit values so these tests don't depend on the shipped defaults
CFG = GraceCfg(first_delay_s=120, max_attempts=3, backoff_mult=2.0)


def test_backoff_delays():
    assert grace.next_delay_s(1, CFG) == 120
    assert grace.next_delay_s(2, CFG) == 240
    assert grace.next_delay_s(3, CFG) == 480


def test_next_due_at_is_in_future():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    due = grace.next_due_at(1, CFG, now=now)
    assert (due - now).total_seconds() == 120


def test_should_retry_respects_max():
    assert grace.should_retry(0, CFG)
    assert grace.should_retry(2, CFG)
    assert not grace.should_retry(3, CFG)


def test_should_retry_disabled():
    assert not grace.should_retry(0, GraceCfg(enabled=False))


def test_reeval_action_terminal_when_indexed():
    assert grace.reeval_action(still_pending=False, attempts_done=1, cfg=CFG) == "terminal"


def test_reeval_action_reschedule_then_giveup():
    assert grace.reeval_action(still_pending=True, attempts_done=1, cfg=CFG) == "reschedule"
    assert grace.reeval_action(still_pending=True, attempts_done=2, cfg=CFG) == "reschedule"
    assert grace.reeval_action(still_pending=True, attempts_done=3, cfg=CFG) == "giveup"
