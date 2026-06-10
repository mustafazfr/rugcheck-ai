"""ADR-046 RDAP domain age — pure parsing (registration event → days) + registrable-domain extraction
(infra hosts excluded, two-level TLDs respected)."""

from datetime import datetime, timezone

from solscout.data.rdap import registrable_domain, registration_age_days

# shape verified against a live rdap.org/domain/google.com response
RDAP = {
    "objectClassName": "domain",
    "ldhName": "PEPE.LOL",
    "events": [
        {"eventAction": "registration", "eventDate": "2026-06-01T04:00:00Z"},
        {"eventAction": "expiration", "eventDate": "2028-09-14T04:00:00Z"},
        {"eventAction": "last changed", "eventDate": "2026-06-02T15:39:04Z"},
    ],
}


# — registration_age_days —

def test_registration_age_days():
    now = datetime(2026, 6, 10, 12, tzinfo=timezone.utc)
    assert registration_age_days(RDAP, now=now) == 9  # registered Jun 1 → 9 days old


def test_registration_age_handles_missing_or_garbage():
    assert registration_age_days(None) is None
    assert registration_age_days({}) is None
    assert registration_age_days({"events": [{"eventAction": "expiration", "eventDate": "2028-01-01T00:00:00Z"}]}) is None
    assert registration_age_days({"events": [{"eventAction": "registration", "eventDate": "not-a-date"}]}) is None
    assert registration_age_days({"events": "nope"}) is None


def test_registration_age_naive_date_treated_utc():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    d = {"events": [{"eventAction": "registration", "eventDate": "2026-06-05T00:00:00"}]}
    assert registration_age_days(d, now=now) == 5


# — registrable_domain —

def test_registrable_domain_normalizes():
    assert registrable_domain("https://www.pepe.lol/airdrop?x=1") == "pepe.lol"
    assert registrable_domain("app.pepe.lol") == "pepe.lol"  # subdomain → registrable
    assert registrable_domain("pepe.lol") == "pepe.lol"  # scheme-less


def test_registrable_domain_two_level_tld():
    assert registrable_domain("https://shop.example.co.uk") == "example.co.uk"


def test_registrable_domain_rejects_infra_and_garbage():
    assert registrable_domain("https://x.com/project") is None  # social, not the project's site
    assert registrable_domain("https://t.me/project") is None
    assert registrable_domain("https://www.dexscreener.com/solana/x") is None
    assert registrable_domain("https://gem.linktr.ee") is None  # infra subdomain
    assert registrable_domain(None) is None
    assert registrable_domain("not a url at all") is None
