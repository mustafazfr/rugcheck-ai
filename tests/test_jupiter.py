"""Jupiter honeypot/sell-sim: the round-trip-tax math is pure → unit-tested (ADR-012)."""

from solscout.data.jupiter import round_trip_tax_pct


def test_no_loss_is_zero_tax():
    assert round_trip_tax_pct(10_000_000, 10_000_000) == 0.0


def test_ten_percent_loss():
    assert abs(round_trip_tax_pct(10_000_000, 9_000_000) - 10.0) < 1e-6


def test_total_loss_is_100():
    assert round_trip_tax_pct(10_000_000, 0) == 100.0


def test_zero_input_is_safe():
    assert round_trip_tax_pct(0, 0) == 0.0


def test_gain_clamps_to_zero():
    # a positive round trip (sol_out > sol_in) shouldn't read as negative tax
    assert round_trip_tax_pct(10_000_000, 11_000_000) == 0.0
