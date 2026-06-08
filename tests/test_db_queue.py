"""Re-eval queue persistence (in-memory SQLite)."""

from datetime import datetime, timedelta, timezone

import pytest

from solscout.core.db import Db


@pytest.fixture
async def db():
    async with Db(":memory:") as d:
        yield d


async def test_enqueue_and_due(db):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await db.enqueue_reeval("m1", "creator1", now + timedelta(seconds=120), "SYM")
    # not due yet
    assert await db.due_reevals(now=now) == []
    # due after the delay
    due = await db.due_reevals(now=now + timedelta(seconds=121))
    assert len(due) == 1 and due[0]["mint"] == "m1" and due[0]["symbol"] == "SYM"
    assert await db.queue_size() == 1


async def test_enqueue_is_idempotent(db):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await db.enqueue_reeval("m1", None, now, "S")
    await db.enqueue_reeval("m1", None, now, "S")  # OR IGNORE
    assert await db.queue_size() == 1


async def test_reschedule_and_drop(db):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await db.enqueue_reeval("m1", None, now, "S")
    await db.reschedule_reeval("m1", attempts=1, next_at=now + timedelta(seconds=240))
    due = await db.due_reevals(now=now + timedelta(seconds=241))
    assert due[0]["attempts"] == 1
    await db.drop_reeval("m1")
    assert await db.queue_size() == 0
