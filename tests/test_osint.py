"""web/osint.py gather() unit checks (ADR-046 regressions) — fully mocked clients, no network."""

from types import SimpleNamespace

from solscout.core.config import Config
from solscout.web import osint

CFG = Config()


def _analysis():
    return SimpleNamespace(
        mint="M1",
        market=None,
        mint_info=SimpleNamespace(supply=1000, decimals=0, mint_authority=None, freeze_authority=None),
        top_holders=[],
    )


class _FakeRC:
    def __init__(self, networks):
        self._rep = SimpleNamespace(
            available=True, creator=None, creator_tokens=None, creator_balance=None,
            score=10, rugged=False, risks=[], lp_locked_pct=None, total_lp_providers=None,
            markets_count=1, total_market_liquidity=1000.0, insider_networks=networks,
            known_accounts=None,
        )

    async def report(self, mint):
        return self._rep


async def test_insider_network_over_100pct_is_untrusted_not_flagged():
    """WIF regression: RugCheck's tokenAmount is flow-like and exceeded the supply (119.9%) — that
    can't be holdings, so the pct reads as unknown and the DANGER flag must NOT fire."""
    rc = _FakeRC([{"id": "big", "size": 2933, "token_amount": 1200}])  # 120% of supply=1000
    out = await osint.gather(_analysis(), CFG, rc=rc)
    assert out["insider_networks"][0]["pct"] is None  # impossible value → unknown, not 120%
    assert "insider_network_dominant" not in out["flags"]


async def test_insider_network_plausible_dominant_still_flags():
    rc = _FakeRC([
        {"id": "big", "size": 2933, "token_amount": 1200},  # 120% → ignored
        {"id": "real", "size": 9, "token_amount": 300},     # 30% → plausible AND dominant
    ])
    out = await osint.gather(_analysis(), CFG, rc=rc)
    assert out["insider_networks"][1]["pct"] == 30.0
    assert "insider_network_dominant" in out["flags"]


async def test_jupiter_devmints_never_critical():
    """WIF regression: Jupiter devMints (all-time; BONK dev=10, WIF dev=15) must not raise the
    serial-rugger CRITICAL — that flag is calibrated on the Helius recent-window count only."""
    jt = SimpleNamespace(
        available=True, verified=True, tags=[], organic_score=70.0, organic_label="medium",
        holder_count=100, mint_auth_disabled=True, freeze_auth_disabled=True,
        top_holders_pct=None, dev_balance_pct=None, dev_mints=15, dev_wallet="DEV",
        first_pool_created_at=None, num_traders_24h=None, num_net_buyers_24h=None,
        buy_volume_24h=None, buy_organic_volume_24h=None,
    )

    class _FakeJup:
        async def token_info(self, mint):
            return jt

    class _FakeHelius:
        available = True

    out = await osint.gather(_analysis(), CFG, jup=_FakeJup(), helius=_FakeHelius())
    assert out["deployer"]["prior_creations"] == 15 and out["deployer"]["source"] == "jupiter"
    assert out["deployer"]["serial"] is False
    assert "deployer_serial_rugger" not in out["flags"]
