"""ADR-046 free Twitter timeline forensics — all pure: __NEXT_DATA__ parsing, CA-shill signals,
posting cadence, snowflake-id identity check, bio-website matching, excerpt picking."""

from datetime import datetime, timezone
from pathlib import Path

from solscout.data.twitter_public import (
    id_joined_mismatch_days,
    parse_timeline_html,
    pick_excerpts,
    posting_cadence,
    snowflake_age_days,
    tweet_ca_signals,
    website_matches,
)

MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
HTML = (Path(__file__).parent / "fixtures" / "syndication_profile.html").read_text()
TWEETS = parse_timeline_html(HTML)


# — parsing —

def test_parse_timeline_extracts_texts_and_skips_empty():
    assert len(TWEETS) == 5  # 6 entries, one has no text
    assert TWEETS[0]["text"].startswith("WE ARE LIVE!") and TWEETS[0]["id"] == "1"
    assert TWEETS[0]["created_at"] == "Mon Jun 08 10:00:00 +0000 2026"


def test_parse_timeline_malformed_returns_empty():
    assert parse_timeline_html("") == []
    assert parse_timeline_html("<html>no next data</html>") == []
    assert parse_timeline_html('<script id="__NEXT_DATA__" type="application/json">{broken</script>') == []
    assert parse_timeline_html(None) == []


# — CA signals —

def test_ca_signals_counts_ours_and_others_excluding_common():
    sig = tweet_ca_signals(TWEETS, MINT)
    assert sig["mint_mentions"] == 1  # our CA posted once (good sign)
    assert sig["other_ca_count"] == 2  # two foreign CAs = shilling; WSOL excluded
    assert all(ca != "So11111111111111111111111111111111111111112" for ca in sig["other_cas"])


def test_ca_signals_empty_tweets():
    assert tweet_ca_signals([], MINT) == {"mint_mentions": 0, "other_cas": [], "other_ca_count": 0}


# — cadence —

def test_posting_cadence_burst_and_span():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    cad = posting_cadence(TWEETS, now=now)
    assert cad["count"] == 4  # the "not-a-date" tweet is dropped from time math
    assert cad["burst_max_1h"] == 3  # three tweets within 40 minutes on Jun 08
    assert cad["last_tweet_age_days"] == 0  # Jun 09 → Jun 10
    assert cad["per_day"] and cad["per_day"] > 0


def test_posting_cadence_no_dates():
    cad = posting_cadence([{"text": "x", "created_at": None}])
    assert cad["per_day"] is None and cad["burst_max_1h"] is None


# — snowflake identity —

def test_snowflake_age_known_id():
    # id 1500000000000000000 → (id >> 22) + epoch ≈ 2022-03-09; ~4y before 2026-06-10
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    age = snowflake_age_days(1_500_000_000_000_000_000, now=now)
    assert age is not None and 1540 < age < 1580


def test_snowflake_rejects_sequential_and_garbage():
    assert snowflake_age_days(12) is None  # @jack — pre-snowflake sequential id
    assert snowflake_age_days("not-a-number") is None
    assert snowflake_age_days(None) is None


def test_id_joined_mismatch():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    sf_age = snowflake_age_days(1_500_000_000_000_000_000, now=now)
    # claimed age equals the real snowflake age → 0 mismatch
    assert id_joined_mismatch_days(1_500_000_000_000_000_000, sf_age, now=now) == 0
    # claims to be 5 years older than the id says → big mismatch (forged/recycled identity)
    assert id_joined_mismatch_days(1_500_000_000_000_000_000, sf_age + 1825, now=now) == 1825
    assert id_joined_mismatch_days(12, 100) is None  # pre-snowflake → unknowable


# — website match —

def test_website_matches_normalizes_hosts():
    assert website_matches("https://www.pepe.lol/airdrop", ["http://pepe.lol"]) is True
    assert website_matches("scam-site.io", ["https://pepe.lol"]) is False
    assert website_matches(None, ["https://pepe.lol"]) is None  # no claim → no flag
    assert website_matches("https://pepe.lol", []) is None


# — excerpts —

def test_pick_excerpts_prefers_mint_mentions_and_caps_length():
    ex = pick_excerpts(TWEETS, MINT, count=2, max_len=30)
    assert len(ex) == 2
    assert MINT[:10] in ex[0] or "WE ARE LIVE" in ex[0]  # mint-mentioning tweet first
    assert all(len(x) <= 30 for x in ex)
