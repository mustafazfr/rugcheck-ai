"""Keyless Twitter intel (ADR-041): handle extraction + authenticity are pure → required tests."""

from solscout.core.config import TwitterCfg
from solscout.data.twitter_public import TwitterProfile, authenticity, handle_from_url, _age_days

CFG = TwitterCfg()


def test_handle_from_url():
    assert handle_from_url("https://twitter.com/bonk_inu") == "bonk_inu"
    assert handle_from_url("https://x.com/@dogwifcoin") == "dogwifcoin"
    assert handle_from_url("http://twitter.com/Some_Handle/status/123") == "Some_Handle"
    assert handle_from_url("https://x.com/i/communities/123") is None  # ignore non-profile paths
    assert handle_from_url("https://t.me/foo") is None
    assert handle_from_url(None) is None


def test_age_parsing_twitter_format():
    d = _age_days("Sun Jul 31 02:25:52 +0000 2022")
    assert d is not None and d > 1000  # years old


def test_no_profile_is_no_twitter():
    score, verdict, flags = authenticity(TwitterProfile(handle="x", available=False), CFG)
    assert score == 0.0 and verdict == "no_profile" and "no_twitter" in flags


def test_credible_established_account():
    p = TwitterProfile(handle="bonk_inu", available=True, followers=449554, tweets=10051,
                       age_days=1400, verified=True)
    score, verdict, flags = authenticity(p, CFG)
    assert verdict == "credible" and score >= 0.7 and flags == []


def test_brand_new_low_follower_is_inauthentic():
    p = TwitterProfile(handle="scam", available=True, followers=12, tweets=3, age_days=2, verified=False)
    score, verdict, flags = authenticity(p, CFG)
    assert verdict == "inauthentic"
    assert "brand_new_account" in flags and "low_followers" in flags


def test_weak_account():
    p = TwitterProfile(handle="meh", available=True, followers=80, tweets=400, age_days=500, verified=False)
    _, verdict, flags = authenticity(p, CFG)
    assert verdict == "weak" and "low_followers" in flags
