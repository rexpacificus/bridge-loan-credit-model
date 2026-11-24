"""Deterministic underwriting metrics, the numbers on page 1 of any credit memo.

Variable key (used everywhere in this package):
    NOI   Net Operating Income, $/yr. Rent collected minus operating expenses,
          BEFORE debt payments. The cash the building throws off.
    V     Property value. For income property V = NOI / cap rate.
    L     Loan balance.
    r     Interest rate (annual, decimal).
    LTV   Loan-to-Value = L / V. How much of the building the lender "owns".
    LTC   Loan-to-Cost = L / (price + capex + closing). Borrower's skin in the game
          is 1 - LTC.
    DSCR  Debt Service Coverage Ratio = NOI / annual debt payments.
          1.0x means the building exactly pays its mortgage.
    DY    Debt Yield = NOI / L. The return the lender would earn if it took
          the building tomorrow. Ignores interest rates, which is why lenders like it.
    k     Mortgage constant = annual payment per $1 of an amortizing loan.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .deal import Deal, Market


def mortgage_constant(rate: float, amort_years: int) -> float:
    """Annual payment per $1 borrowed on a fully amortizing loan.

    rate is annual decimal (0.065 = 6.5%). Monthly compounding, as US
    mortgages are paid monthly.
    """
    m = np.asarray(rate, dtype=float) / 12
    n = amort_years * 12
    with np.errstate(divide="ignore", invalid="ignore"):
        k = np.where(m == 0, 1 / amort_years, 12 * m / (1 - (1 + m) ** -n))
    return float(k) if k.ndim == 0 else k


def bridge_rate(deal: Deal, sofr_pct: float) -> float:
    """Contract rate the LENDER earns = max(SOFR, floor) + spread, as decimal."""
    return (max(sofr_pct, deal.sofr_floor) + deal.spread) / 100


def borrower_rate(deal: Deal, sofr_pct: float) -> float:
    """Rate the BORROWER effectively pays after its rate cap pays out.

    A rate cap is an option the borrower buys: if SOFR rises above the strike,
    the cap counterparty pays the difference. The lender still gets the full
    rate, but the borrower's cash cost is capped, which keeps the deal alive.
    """
    s = max(sofr_pct, deal.sofr_floor)
    if deal.rate_cap_strike is not None:
        s = min(s, max(deal.rate_cap_strike, deal.sofr_floor))
    return (s + deal.spread) / 100


@dataclass
class TakeoutSizing:
    refi_rate: float
    max_by_ltv: float
    max_by_dscr: float
    max_by_debt_yield: float

    @property
    def max_loan(self) -> float:
        return min(self.max_by_ltv, self.max_by_dscr, self.max_by_debt_yield)

    @property
    def binding(self) -> str:
        d = {"LTV": self.max_by_ltv, "DSCR": self.max_by_dscr,
             "Debt yield": self.max_by_debt_yield}
        return min(d, key=d.get)


def size_takeout(deal: Deal, noi: float, cap_rate: float, refi_rate: float) -> TakeoutSizing:
    """How big a permanent loan can this property support?"""
    t = deal.takeout
    value = noi / cap_rate
    k = mortgage_constant(refi_rate, t.amort_years)
    return TakeoutSizing(
        refi_rate=refi_rate,
        max_by_ltv=t.max_ltv * value,
        max_by_dscr=noi / (t.min_dscr * k),
        max_by_debt_yield=noi / t.min_debt_yield,
    )


def underwrite(deal: Deal, mkt: Market) -> dict:
    """Base-case metrics, assuming the business plan goes exactly to plan."""
    L0, L = deal.initial_funding, deal.commitment
    r_now = bridge_rate(deal, mkt.sofr)
    refi_rate = (mkt.treasury_10y + deal.takeout.spread_over_10y) / 100

    stab_value = deal.stabilized_noi / deal.exit_cap_rate
    sizing = size_takeout(deal, deal.stabilized_noi, deal.exit_cap_rate, refi_rate)

    # Carry: does today's NOI pay today's interest?
    annual_interest_now = L0 * borrower_rate(deal, mkt.sofr)
    shortfall_now = max(0.0, annual_interest_now - deal.in_place_noi)

    # Breakevens: how wrong can the plan be before the lender is in trouble?
    # (a) exit cap at which a SALE no longer repays the loan
    be_cap_sale = deal.stabilized_noi * (1 - deal.sale_cost) / L
    # (b) NOI needed for a full REFI at base-case cap and rate
    k = mortgage_constant(refi_rate, deal.takeout.amort_years)
    noi_needed_refi = max(
        L * deal.takeout.min_dscr * k,
        L * deal.takeout.min_debt_yield,
        L * deal.exit_cap_rate / deal.takeout.max_ltv,
    )

    return {
        "bridge_rate_today": r_now,
        "as_is_ltv": L0 / deal.as_is_value,
        "ltc": L / deal.total_cost,
        "borrower_equity": deal.total_cost - L,
        "in_place_debt_yield": deal.in_place_noi / L0,
        "in_place_dscr_io": deal.in_place_noi / (L0 * r_now),
        "annual_shortfall_now": shortfall_now,
        "reserve_months_cover": (12 * (deal.interest_reserve / shortfall_now)
                                 if shortfall_now > 0 else float("inf")),
        "stabilized_value": stab_value,
        "as_stabilized_ltv": L / stab_value,
        "stabilized_debt_yield": deal.stabilized_noi / L,
        "stabilized_dscr_io": deal.stabilized_noi / (L * r_now),
        "refi_rate": refi_rate,
        "takeout_max_loan": sizing.max_loan,
        "takeout_binding": sizing.binding,
        "takeout_by_ltv": sizing.max_by_ltv,
        "takeout_by_dscr": sizing.max_by_dscr,
        "takeout_by_dy": sizing.max_by_debt_yield,
        "refi_gap": L - sizing.max_loan,  # >0 means a refi alone doesn't repay us
        "breakeven_exit_cap_sale": be_cap_sale,
        "noi_needed_for_refi": noi_needed_refi,
        "noi_cushion_refi": 1 - noi_needed_refi / deal.stabilized_noi,
    }
