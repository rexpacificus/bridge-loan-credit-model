"""Deal inputs.

A bridge loan funds a property that is NOT yet stable enough for a cheap
long-term ("permanent") loan: it is half empty, needs renovation, or rents are
below market. The borrower's business plan is: buy it, fix it, lease it up,
then pay off the bridge loan by refinancing into a permanent loan or selling.

The lender's core question: at maturity, will the property be worth enough
and earn enough that someone else will take us out?

Every field below is one piece of that story.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml


@dataclass
class Takeout:
    """What a permanent lender will require to refinance us out.

    The permanent lender sizes its loan as the SMALLEST of three limits.
    Whichever is smallest is the "binding constraint".
    """

    max_ltv: float = 0.70          # loan <= 70% of property value
    min_dscr: float = 1.25         # NOI must cover annual debt payments 1.25x
    min_debt_yield: float = 0.08   # NOI / loan >= 8%, rate-independent sanity check
    spread_over_10y: float = 1.75  # perm loan rate = 10y Treasury + this (in %)
    amort_years: int = 30          # perm loans amortize, bridge loans are interest-only


@dataclass
class Deal:
    # --- identity ---------------------------------------------------------
    name: str
    market: str
    property_type: str
    units: int | None = None
    notes: str = ""

    # --- the property -----------------------------------------------------
    purchase_price: float = 0.0     # what the borrower pays today
    as_is_value: float | None = None  # appraised value today (defaults to price)
    capex_budget: float = 0.0       # renovation budget in the business plan
    closing_costs: float = 0.0
    in_place_noi: float = 0.0       # Net Operating Income today, $/yr (rent - opex)
    stabilized_noi: float = 0.0     # NOI the business plan promises after the work
    months_to_stabilize: int = 12   # how long the business plan says it takes

    # --- the loan ---------------------------------------------------------
    initial_funding: float = 0.0    # loan dollars funded at closing
    future_funding: float = 0.0     # loan dollars held back, funded as capex is spent
    term_months: int = 24           # initial maturity
    extension_months: int = 12      # one extension option, if the loan qualifies
    extension_min_debt_yield: float = 0.065  # test borrower must pass to extend
    spread: float = 4.00            # interest rate = SOFR + spread (in %)
    sofr_floor: float = 2.00        # SOFR used is never below this (in %)
    rate_cap_strike: float | None = 5.50  # borrower-purchased SOFR cap (in %)
    origination_fee: float = 0.01   # 1 point up front, as fraction of commitment
    exit_fee: float = 0.005         # 0.5 point at payoff
    interest_reserve: float = 0.0   # cash escrowed at closing to pay interest shortfalls
    sponsor_liquidity: float = 0.0  # extra cash the sponsor can realistically put in

    # --- exit assumptions -------------------------------------------------
    exit_cap_rate: float = 0.055    # value at exit = NOI / cap rate
    sale_cost: float = 0.02         # broker + transfer costs if sold

    # --- if it goes wrong: foreclosure -----------------------------------
    foreclosure_months: int = 24    # time to take title and resell (NY/NJ/FL are
                                    # all judicial-foreclosure states, i.e. slow)
    liquidation_cost: float = 0.10  # legal, receiver, broker, distressed-sale discount

    # --- uncertainty: how wrong can the business plan be? ------------------
    noi_attainment_mean: float = 0.95   # on average hit 95% of promised NOI
    noi_attainment_sd: float = 0.08
    delay_mean_months: float = 4.0      # average slip vs. business plan
    exit_cap_sd: float = 0.0050         # 50bp standard deviation on exit cap
    cap_rate_beta: float = 0.50         # exit cap moves 0.5bp per 1bp move in 10y

    takeout: Takeout = field(default_factory=Takeout)

    # ----------------------------------------------------------------------
    def __post_init__(self):
        if self.as_is_value is None:
            self.as_is_value = self.purchase_price
        if isinstance(self.takeout, dict):
            self.takeout = Takeout(**self.takeout)

    @property
    def commitment(self) -> float:
        """Total loan amount the lender has promised (funded + held back)."""
        return self.initial_funding + self.future_funding

    @property
    def total_cost(self) -> float:
        """Everything the borrower spends: price + renovation + closing."""
        return self.purchase_price + self.capex_budget + self.closing_costs

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Market:
    """Rates on the day you run the model. Update these before each run."""

    as_of: str = "2026-11"
    sofr: float = 3.95          # % , overnight SOFR (NY Fed)
    treasury_10y: float = 4.95  # % , 10-year Treasury yield
    sofr_long_run: float = 3.50  # % , where SOFR mean-reverts to
    sofr_vol: float = 1.00       # % per sqrt(year), how much SOFR wanders
    sofr_reversion: float = 0.50  # how fast it pulls back to long run (per year)
    tsy_vol: float = 0.75        # % per sqrt(year), 10y yield volatility


def load_deal(path: str | Path) -> tuple[Deal, Market]:
    raw = yaml.safe_load(Path(path).read_text())
    market = Market(**raw.pop("rates", {}))
    return Deal(**raw), market
