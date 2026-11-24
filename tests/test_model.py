from dataclasses import replace

import pytest

from bridgeloan.deal import load_deal
from bridgeloan.extract import extract_regex
from bridgeloan.metrics import mortgage_constant, underwrite
from bridgeloan.simulate import simulate

BK = "deals/brooklyn_multifamily.yaml"


@pytest.fixture
def bk():
    return load_deal(BK)


def test_mortgage_constant_matches_textbook():
    # $1 at 6% over 30 years: monthly payment 0.0059955 -> 0.071946/yr
    assert mortgage_constant(0.06, 30) == pytest.approx(0.071946, abs=1e-5)
    assert mortgage_constant(0.0, 30) == pytest.approx(1 / 30)


def test_underwriting_metrics_by_hand(bk):
    deal, mkt = bk
    u = underwrite(deal, mkt)
    assert u["as_is_ltv"] == pytest.approx(5_400_000 / 7_200_000)
    assert u["ltc"] == pytest.approx(6_400_000 / 8_550_000)
    assert u["stabilized_debt_yield"] == pytest.approx(520_000 / 6_400_000)
    assert u["breakeven_exit_cap_sale"] == pytest.approx(520_000 * 0.98 / 6_400_000)
    assert u["takeout_max_loan"] == min(u["takeout_by_ltv"], u["takeout_by_dscr"], u["takeout_by_dy"])


def test_simulation_is_reproducible(bk):
    deal, mkt = bk
    a, b = simulate(deal, mkt, 2000, seed=1), simulate(deal, mkt, 2000, seed=1)
    assert a.pd == b.pd and a.el == b.el


def test_outcomes_are_exhaustive(bk):
    deal, mkt = bk
    r = simulate(deal, mkt, 2000)
    assert sum(r.outcome_shares().values()) == pytest.approx(1.0)
    assert (r.loss[~r.default] == 0).all()
    assert (r.loss >= 0).all()


@pytest.mark.parametrize("field,delta", [
    ("exit_cap_rate", 0.01), ("noi_attainment_mean", -0.10), ("initial_funding", 600_000)])
def test_risk_moves_the_right_way(bk, field, delta):
    deal, mkt = bk
    base = simulate(deal, mkt, 3000)
    worse = simulate(replace(deal, **{field: getattr(deal, field) + delta}), mkt, 3000)
    assert worse.pd >= base.pd
    assert worse.el >= base.el


def test_no_leverage_no_loss(bk):
    deal, mkt = bk
    tiny = replace(deal, initial_funding=500_000, future_funding=0)
    assert simulate(tiny, mkt, 2000).el == 0


def test_regex_extraction():
    t = extract_regex(open("listings/sample_listing.txt").read())
    assert t["purchase_price"] == 7_200_000
    assert t["in_place_noi"] == 378_000
    assert t["cap_rate"] == pytest.approx(0.0525)
    assert t["units"] == 24
