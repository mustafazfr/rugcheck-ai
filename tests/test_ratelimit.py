"""ADR-046 web abuse control — TokenBucket refill math (injected clock), IpLimiter LRU bound,
DailyMintLedger day rollover + per-mint dedupe. All pure, no network/sleep."""

from solscout.web.ratelimit import DailyMintLedger, IpLimiter, TokenBucket


# — TokenBucket —

def test_bucket_burst_then_block():
    b = TokenBucket(capacity=3, refill_per_s=1.0, now=0.0)
    assert b.allow(now=0.0) and b.allow(now=0.0) and b.allow(now=0.0)  # full burst
    assert not b.allow(now=0.0)  # empty
    assert b.retry_after_s() >= 1


def test_bucket_refills_over_time_and_caps_at_capacity():
    b = TokenBucket(capacity=2, refill_per_s=0.5, now=0.0)
    assert b.allow(now=0.0) and b.allow(now=0.0)
    assert not b.allow(now=1.0)  # only 0.5 refilled
    assert b.allow(now=2.0)  # 1.0 refilled by t=2
    assert b.allow(now=100.0) and b.allow(now=100.0)  # long idle refills to CAPACITY, not beyond
    assert not b.allow(now=100.0)


def test_bucket_clock_never_goes_backward():
    b = TokenBucket(capacity=1, refill_per_s=1.0, now=10.0)
    assert b.allow(now=10.0)
    assert not b.allow(now=5.0)  # past timestamp must not mint tokens


# — IpLimiter —

def test_iplimiter_isolates_ips_and_reports_retry():
    lim = IpLimiter(capacity=1, refill_per_s=0.5)
    ok, retry = lim.allow("1.1.1.1", now=0.0)
    assert ok and retry == 0
    ok, retry = lim.allow("1.1.1.1", now=0.0)
    assert not ok and retry >= 1  # same IP blocked
    ok, _ = lim.allow("2.2.2.2", now=0.0)
    assert ok  # different IP unaffected


def test_iplimiter_lru_eviction_bounds_memory():
    lim = IpLimiter(capacity=1, refill_per_s=1.0, max_ips=2)
    lim.allow("a", now=0.0)
    lim.allow("b", now=0.0)
    lim.allow("c", now=0.0)  # evicts "a"
    assert len(lim._buckets) == 2 and "a" not in lim._buckets
    # evicted IP comes back with a FRESH bucket (gets its burst again — acceptable trade-off)
    ok, _ = lim.allow("a", now=0.0)
    assert ok


# — DailyMintLedger —

def test_ledger_counts_unique_mints_and_dedupes():
    led = DailyMintLedger(max_per_day=2)
    assert led.allow("ip", "M1", "2026-06-10")
    assert led.allow("ip", "M1", "2026-06-10")  # same mint again = free
    assert led.allow("ip", "M2", "2026-06-10")
    assert not led.allow("ip", "M3", "2026-06-10")  # over the daily cap
    assert led.allow("other-ip", "M3", "2026-06-10")  # other IPs unaffected


def test_ledger_resets_on_day_rollover():
    led = DailyMintLedger(max_per_day=1)
    assert led.allow("ip", "M1", "2026-06-10")
    assert not led.allow("ip", "M2", "2026-06-10")
    assert led.allow("ip", "M2", "2026-06-11")  # new UTC day → fresh allowance
