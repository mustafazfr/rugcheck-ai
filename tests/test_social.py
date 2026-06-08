"""Stage 2 social: scoring + parsing. Pure logic → unit-tested (network paths verified live separately)."""

from solscout.core.config import Config
from solscout.core.models import SocialReport
from solscout.data.telegram_web import _MSG_RE, _clean, _parse_subs
from solscout.data.tweetscout import TweetScoutClient, account_age_days
from solscout.enrich import social as social_mod

CFG = Config()


def test_handle_extraction():
    assert social_mod._handle("https://twitter.com/bonk_inu") == "bonk_inu"
    assert social_mod._handle("https://t.me/bonk_inu/") == "bonk_inu"
    assert social_mod._handle("https://x.com/foo?ref=1") == "foo"
    assert social_mod._handle(None) is None


def test_telegram_message_extraction_and_clean():
    page = (
        '<div class="tgme_widget_message_text js-message_text" dir="auto">'
        'gm <a href="x">$BONK</a> &amp; friends<br/>buy now</div>'
    )
    raw = _MSG_RE.findall(page)
    assert len(raw) == 1
    assert _clean(raw[0]) == "gm $BONK & friends buy now"


def test_parse_subscribers():
    page = (
        'x<span class="counter_value">26.6K</span> <span class="counter_type">subscribers</span>y'
    )
    assert _parse_subs(page) == 26600
    assert _parse_subs("nothing here") is None


def test_social_score_telegram_only_is_capped():
    # no Twitter authenticity → Telegram alone must not exceed 0.5 (can't open the BUY gate)
    assert social_mod._score(SocialReport(mint="m"), tg_active=1.0, cfg=CFG) == 0.5


def test_social_score_none_without_any_signal():
    assert social_mod._score(SocialReport(mint="m"), tg_active=None, cfg=CFG) is None


def test_social_score_with_twitter_age_not_capped():
    r = SocialReport(mint="m", twitter_age_days=365)
    assert social_mod._score(r, tg_active=1.0, cfg=CFG) == 1.0


def test_tweetscout_unavailable_without_key():
    assert TweetScoutClient("").available is False


def test_account_age_days_parsing():
    assert account_age_days({"register_date": "2021-01-01"}) > 1000
    assert account_age_days(None) is None
    assert account_age_days({}) is None
