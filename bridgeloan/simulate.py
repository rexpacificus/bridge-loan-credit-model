"""Monte Carlo stress engine.

The story of ONE simulated path (we run 10,000 of them):

  1. Month 0: lender funds the loan. SOFR and the 10-year Treasury then wander
     month by month. SOFR drives what the borrower pays now. The 10-year
     drives what a permanent lender will charge later, and drags cap rates
     along with it (higher rates -> buyers pay less per $ of NOI).

  2. The business plan slips. It takes D extra months to stabilize, and the
     building ends up earning a fraction `a` of the NOI that was promised.

  3. Every month, if NOI doesn't cover interest, the shortfall is paid from
     the interest reserve, then from sponsor cash. Run out of both before
     maturity -> TERM DEFAULT.

  4. At maturity we test the exits in order:
        refi  : can a permanent lender size a loan >= our balance?
        sale  : is the building (stabilized) worth more than our balance after costs?
        extend: if neither yet, does it pass the extension test? Try again later.
     All fail -> MATURITY DEFAULT.

  5. On default the lender forecloses: takes the building, waits through the
     foreclosure timeline, sells it at a discount. Loss = balance - what
     comes back, in today's dollars.

Variable key (arrays are length N = number of paths):
    sofr[t], tsy[t]  rates in % at month t
    D                months of delay vs business plan
    a                NOI attainment (0.9 = earned 90% of promised NOI)
    noi[t]           annual NOI run-rate at month t
    bal[t]           loan balance at month t (grows as future funding is drawn)
    cap[t]           market cap rate at month t
    PD               probability of default
    LGD              loss given default, as a fraction of the balance
    EL               expected loss = average loss across all paths / commitment
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from .deal import Deal, Market
from .metrics import mortgage_constant

RATE_CORRELATION = 0.6   # monthly shocks to SOFR and the 10y are positively correlated
MIN_CAP_RATE = 0.035


@dataclass
class SimResult:
    deal: Deal
    n_paths: int
    outcome: np.ndarray       # 'refi', 'sale', 'ext_refi', 'ext_sale', 'term_default', 'mat_default'
    default: np.ndarray       # bool
    loss: np.ndarray          # $ loss per path (0 if repaid)
    life_months: np.ndarray   # months the loan was outstanding
    final_ltv: np.ndarray     # balance / value at the last exit test
    final_dscr: np.ndarray    # NOI / takeout debt service at the last exit test
    delay: np.ndarray
    attainment: np.ndarray
    exit_cap: np.ndarray

    # ---- summary statistics ------------------------------------------------
    @property
    def pd(self) -> float:
        return float(self.default.mean())

    @property
    def pd_term(self) -> float:
        return float((self.outcome == "term_default").mean())

    @property
    def pd_maturity(self) -> float:
        return float((self.outcome == "mat_default").mean())

    @property
    def lgd(self) -> float:
        """Average loss severity on the paths that defaulted."""
        if not self.default.any():
            return 0.0
        return float((self.loss[self.default] / self.deal.commitment).mean())

    @property
    def el(self) -> float:
        """Expected loss over the life of the loan, % of commitment."""
        return float(self.loss.mean() / self.deal.commitment)

    @property
    def avg_life_years(self) -> float:
        return float(self.life_months.mean() / 12)

    @property
    def el_annual(self) -> float:
        return self.el / self.avg_life_years

    def loss_quantile(self, q: float) -> float:
        """Credit VaR: loss (% of commitment) not exceeded with probability q."""
        return float(np.quantile(self.loss, q) / self.deal.commitment)

    @property
    def lender_margin(self) -> float:
        """Annual spread + fees spread over the expected life."""
        fees = self.deal.origination_fee + self.deal.exit_fee
        return self.deal.spread / 100 + fees / self.avg_life_years

    @property
    def risk_adjusted_margin(self) -> float:
        return self.lender_margin - self.el_annual

    def outcome_shares(self) -> dict[str, float]:
        labels = ["refi", "sale", "ext_refi", "ext_sale", "term_default", "mat_default"]
        return {k: float((self.outcome == k).mean()) for k in labels}

    def summary(self) -> dict:
        return {
            "pd": self.pd,
            "pd_term": self.pd_term,
            "pd_maturity": self.pd_maturity,
            "lgd": self.lgd,
            "el": self.el,
            "el_annual": self.el_annual,
            "var95": self.loss_quantile(0.95),
            "var99": self.loss_quantile(0.99),
            "avg_life_years": self.avg_life_years,
            "lender_margin": self.lender_margin,
            "risk_adjusted_margin": self.risk_adjusted_margin,
            **{f"p_{k}": v for k, v in self.outcome_shares().items()},
        }


def simulate(deal: Deal, mkt: Market, n_paths: int = 10_000, seed: int = 7) -> SimResult:
    """Run the Monte Carlo. Same seed -> same random draws (so sensitivities
    compare like with like: only the input you changed differs)."""
    rng = np.random.default_rng(seed)
    T, E = deal.term_months, deal.extension_months
    H = T + E                      # horizon in months
    N = n_paths
    dt = 1 / 12

    # ---- 1. random draws (all drawn up front, fixed shapes) -------------
    z_sofr = rng.standard_normal((N, H))
    z_eps = rng.standard_normal((N, H))
    z_cap = rng.standard_normal(N)
    z_noi = rng.standard_normal(N)
    u_delay = rng.standard_gamma(2.0, N)      # Gamma(shape=2), scaled below

    # ---- 2. rate paths ----------------------------------------------------
    # SOFR: Vasicek / mean-reverting. Each month it drifts back toward the
    # long-run level and gets a random shock.
    sofr = np.empty((N, H + 1))
    sofr[:, 0] = mkt.sofr
    for t in range(H):
        drift = mkt.sofr_reversion * (mkt.sofr_long_run - sofr[:, t]) * dt
        sofr[:, t + 1] = np.maximum(0.0, sofr[:, t] + drift + mkt.sofr_vol * np.sqrt(dt) * z_sofr[:, t])

    # 10y Treasury: random walk whose shocks are correlated with SOFR's.
    z_tsy = RATE_CORRELATION * z_sofr + np.sqrt(1 - RATE_CORRELATION ** 2) * z_eps
    tsy = np.empty((N, H + 1))
    tsy[:, 0] = mkt.treasury_10y
    tsy[:, 1:] = mkt.treasury_10y + mkt.tsy_vol * np.sqrt(dt) * np.cumsum(z_tsy, axis=1)

    # Cap rate: base exit cap, pushed up/down by 10y moves (beta), plus a
    # property-specific shock (market sentiment for this asset type/location).
    cap_idio = deal.exit_cap_sd * z_cap
    cap = deal.exit_cap_rate + deal.cap_rate_beta * (tsy - mkt.treasury_10y) / 100 + cap_idio[:, None]
    cap = np.maximum(cap, MIN_CAP_RATE)

    # ---- 3. business plan execution --------------------------------------
    D = u_delay * (deal.delay_mean_months / 2.0)   # Gamma(2, mean/2) has mean = delay_mean
    a = np.clip(deal.noi_attainment_mean + deal.noi_attainment_sd * z_noi, 0.4, 1.2)
    stab_time = deal.months_to_stabilize + D       # actual months to stabilize

    months = np.arange(H + 1)[None, :]
    progress = np.minimum(months / stab_time[:, None], 1.0)
    noi = deal.in_place_noi + (a * deal.stabilized_noi - deal.in_place_noi)[:, None] * progress
    bal = deal.initial_funding + deal.future_funding * progress   # capex draws follow progress

    # ---- 4. monthly carry: does NOI pay interest? ------------------------
    s_eff = np.maximum(sofr, deal.sofr_floor)
    if deal.rate_cap_strike is not None:
        s_eff = np.minimum(s_eff, max(deal.rate_cap_strike, deal.sofr_floor))
    borrower_r = (s_eff + deal.spread) / 100
    shortfall = np.maximum(0.0, bal * borrower_r / 12 - noi / 12)[:, 1:]   # months 1..H
    cum_short = np.cumsum(shortfall, axis=1)
    cushion = deal.interest_reserve + deal.sponsor_liquidity
    broke = cum_short > cushion
    # first month the sponsor runs out of money (H+1 = never)
    first_broke = np.where(broke.any(axis=1), broke.argmax(axis=1) + 1, H + 1)

    # ---- 5. exit tests ---------------------------------------------------
    t_spec = deal.takeout

    def exit_test(m: int):
        n, c, b = noi[:, m], cap[:, m], bal[:, m]
        refi_rate = (tsy[:, m] + t_spec.spread_over_10y) / 100
        k = mortgage_constant(refi_rate, t_spec.amort_years)  # vectorized
        value = n / c
        max_loan = np.minimum.reduce([t_spec.max_ltv * value,
                                      n / (t_spec.min_dscr * k),
                                      n / t_spec.min_debt_yield])
        refi_ok = max_loan >= b
        sale_ok = value * (1 - deal.sale_cost) >= b
        stabilized = m >= stab_time
        dscr = n / (b * k)
        return refi_ok, sale_ok, stabilized, value, dscr

    refi_T, sale_T, stab_T, val_T, dscr_T = exit_test(T)
    refi_H, sale_H, stab_H, val_H, dscr_H = exit_test(H)
    ext_ok = (E > 0) & (noi[:, T] / bal[:, T] >= deal.extension_min_debt_yield)

    outcome = np.full(N, "", dtype=object)
    term_def = first_broke <= T
    outcome[term_def] = "term_default"

    live = ~term_def
    m = live & refi_T
    outcome[m] = "refi"
    live &= ~m
    m = live & sale_T & stab_T
    outcome[m] = "sale"
    live &= ~m

    # extension path: sponsor must also survive the extension period
    ext = live & ext_ok
    ext_broke = ext & (first_broke <= H)
    outcome[ext_broke] = "term_default"
    ext_alive = ext & ~ext_broke
    outcome[ext_alive & refi_H] = "ext_refi"
    outcome[ext_alive & ~refi_H & sale_H] = "ext_sale"
    outcome[ext_alive & ~refi_H & ~sale_H] = "mat_default"
    live &= ~ext

    # no extension available: sell if possible (even unstabilized), else default
    outcome[live & sale_T] = "sale"
    outcome[live & ~sale_T] = "mat_default"

    # ---- 6. when did it end, and what did the lender lose? ---------------
    end_month = np.select(
        [outcome == "term_default", np.isin(outcome, ["refi", "sale"])],
        [first_broke, T], default=H)
    end_month = np.minimum(end_month, H)
    idx = np.arange(N)
    noi_end, cap_end, bal_end = noi[idx, end_month], cap[idx, end_month], bal[idx, end_month]
    value_end = noi_end / cap_end

    default = np.isin(outcome, ["term_default", "mat_default"])
    W = deal.foreclosure_months
    disc_rate = (np.maximum(sofr[idx, end_month], deal.sofr_floor) + deal.spread) / 100
    # Recovery: sell at a discount after W months, plus the rent a court-appointed
    # receiver collects meanwhile, discounted back at the loan rate.
    gross = value_end * (1 - deal.liquidation_cost) + noi_end * (W / 12)
    pv_recovery = gross / (1 + disc_rate) ** (W / 12)
    loss = np.where(default, np.maximum(0.0, bal_end - pv_recovery), 0.0)

    final_m = np.where(np.isin(outcome, ["refi", "sale"]), T, H)
    final_val = np.where(final_m == T, val_T, val_H)
    final_ltv = bal[idx, final_m] / final_val
    final_dscr = np.where(final_m == T, dscr_T, dscr_H)

    return SimResult(
        deal=deal, n_paths=N, outcome=outcome.astype(str), default=default, loss=loss,
        life_months=end_month.astype(float), final_ltv=final_ltv, final_dscr=final_dscr,
        delay=D, attainment=a, exit_cap=cap[idx, final_m],
    )


def sensitivities(deal: Deal, mkt: Market, n_paths: int = 10_000, seed: int = 7) -> list[dict]:
    """Shock one input at a time and see how expected loss moves.
    Uses the same random draws as the base run, so differences are causal."""
    base = simulate(deal, mkt, n_paths, seed)
    shocks = [
        ("Exit cap +50bp", replace(deal, exit_cap_rate=deal.exit_cap_rate + 0.005), mkt),
        ("Exit cap +100bp", replace(deal, exit_cap_rate=deal.exit_cap_rate + 0.010), mkt),
        ("NOI attainment -10pts", replace(deal, noi_attainment_mean=deal.noi_attainment_mean - 0.10), mkt),
        ("Lease-up delay +6 months", replace(deal, delay_mean_months=deal.delay_mean_months + 6), mkt),
        # A 10y shock raises the perm rate AND (via beta) the exit cap we should underwrite.
        ("10y +100bp (perm rate up, caps follow)",
         replace(deal, exit_cap_rate=deal.exit_cap_rate + deal.cap_rate_beta * 0.01),
         replace(mkt, treasury_10y=mkt.treasury_10y + 1.0)),
        ("SOFR +150bp", deal, replace(mkt, sofr=mkt.sofr + 1.5, sofr_long_run=mkt.sofr_long_run + 1.5)),
        ("No sponsor support", replace(deal, sponsor_liquidity=0.0), mkt),
    ]
    rows = [{"scenario": "Base case", "pd": base.pd, "el": base.el}]
    for name, d, m in shocks:
        r = simulate(d, m, n_paths, seed)
        rows.append({"scenario": name, "pd": r.pd, "el": r.el})
    return rows


def verdict(res: SimResult) -> tuple[str, str]:
    """Illustrative screening rule. Real credit committees use judgment;
    this just makes the model's opinion explicit and testable."""
    pd_, ela, ram = res.pd, res.el_annual, res.risk_adjusted_margin
    spread = res.deal.spread / 100
    if pd_ <= 0.05 and ela <= 0.10 * spread:
        return "APPROVE", "Low default probability and expected loss eats under 10% of the spread."
    if pd_ <= 0.15 and ela <= 0.30 * spread:
        return "APPROVE WITH CONDITIONS", ("Pricing covers expected loss, but default risk is "
                                           "material. Tighten structure (lower leverage, bigger reserve, "
                                           "stronger extension test).")
    return "DECLINE AS STRUCTURED", ("Expected loss consumes too much of the spread or default "
                                     "probability is too high. Needs a lower loan amount.")


def max_approvable_commitment(deal: Deal, mkt: Market, n_paths: int = 10_000,
                              seed: int = 7, iters: int = 14) -> float:
    """Largest loan (scaling initial + future funding together) that still
    gets an APPROVE. This is the number a credit officer gives back to the
    originator: 'we can do this deal at $X, not at $Y'."""
    def approves(scale: float) -> bool:
        d = replace(deal, initial_funding=deal.initial_funding * scale,
                    future_funding=deal.future_funding * scale)
        return verdict(simulate(d, mkt, n_paths, seed))[0] == "APPROVE"

    lo, hi = 0.3, 1.3
    if not approves(lo):
        return 0.0
    if approves(hi):
        return deal.commitment * hi
    for _ in range(iters):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if approves(mid) else (lo, mid)
    return deal.commitment * lo
