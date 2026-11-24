"""Write a one-page credit memo in the format a credit committee expects."""

from __future__ import annotations

from .deal import Deal, Market
from .simulate import SimResult, verdict


def _m(x: float) -> str:
    return f"${x / 1e6:,.2f}M"


def _p(x: float, d: int = 1) -> str:
    return f"{x * 100:.{d}f}%"


def write_memo(deal: Deal, mkt: Market, u: dict, res: SimResult,
               sens: list[dict], max_loan: float, chart_file: str) -> str:
    s = res.summary()
    call, why = verdict(res)
    gap = u["refi_gap"]
    exit_story = (
        f"At today's permanent-loan rate of {_p(u['refi_rate'], 2)} (10y Treasury "
        f"{mkt.treasury_10y:.2f}% + {deal.takeout.spread_over_10y:.2f}%), a takeout lender sizes to "
        f"{_m(u['takeout_max_loan'])}, constrained by **{u['takeout_binding']}**. "
    )
    if gap > 0:
        exit_story += (
            f"That is **{_m(gap)} short** of our {_m(deal.commitment)} balance, so even if the business "
            f"plan is hit exactly, a refinance alone does not repay us. The realistic exit is a **sale** "
            f"(or a cash-in refi by the sponsor). The building would need to earn "
            f"{_m(u['noi_needed_for_refi'])} of NOI to refi us out cleanly, "
            f"{_p(-u['noi_cushion_refi'], 0)} above the business plan."
        )
    else:
        exit_story += (f"That covers our balance with {_m(-gap)} to spare, so the refi exit works "
                       f"on business-plan numbers with a {_p(u['noi_cushion_refi'], 0)} NOI cushion.")

    sens_rows = "\n".join(
        f"| {r['scenario']} | {_p(r['pd'])} | {_p(r['el'], 2)} |" for r in sens)

    worst = max(sens[1:], key=lambda r: r["el"])
    loan_line = (f"Model supports up to **{_m(max_loan)}** "
                 f"({_p(max_loan / deal.total_cost, 0)} LTC) for an unconditional approve."
                 if max_loan > 0 else "No loan size reaches an unconditional approve.")

    return f"""# Credit Memo: {deal.name}

**{deal.market} | {deal.property_type}{f" | {deal.units} units" if deal.units else ""}**
Rates as of {mkt.as_of}: SOFR {mkt.sofr:.2f}%, 10y Treasury {mkt.treasury_10y:.2f}%.
*Illustrative deal for a modeling project. Not a real transaction.*

## Recommendation: {call}

{why} {loan_line} Requested: {_m(deal.commitment)} ({_p(u['ltc'], 0)} LTC).

| | |
|---|---|
| Probability of default (PD) | **{_p(s['pd'])}** ({_p(s['pd_term'])} during term, {_p(s['pd_maturity'])} at maturity) |
| Loss given default (LGD) | {_p(s['lgd'])} of balance |
| Expected loss, life of loan | {_p(s['el'], 2)} |
| Expected loss, annualized | {_p(s['el_annual'], 2)} vs. {deal.spread:.2f}% spread |
| Risk-adjusted margin | {_p(s['risk_adjusted_margin'], 2)} / yr (spread + fees - expected loss) |
| 99% credit VaR | {_p(s['var99'])} of commitment |

## The deal

{deal.notes.strip()}

| Sources & uses | |
|---|---|
| Purchase price | {_m(deal.purchase_price)} |
| Capex budget | {_m(deal.capex_budget)} |
| Closing costs | {_m(deal.closing_costs)} |
| **Total cost** | **{_m(deal.total_cost)}** |
| Loan: funded at close | {_m(deal.initial_funding)} |
| Loan: future funding (capex) | {_m(deal.future_funding)} |
| Sponsor equity | {_m(u['borrower_equity'])} ({_p(1 - u['ltc'], 0)} of cost) |

Terms: SOFR + {deal.spread:.2f}% (floor {deal.sofr_floor:.2f}%), {deal.term_months} months + {deal.extension_months}-month extension, {deal.origination_fee * 100:.1f} pt in / {deal.exit_fee * 100:.1f} pt out. Interest reserve {_m(deal.interest_reserve)}.

## Sizing metrics

| Metric | Today (as-is) | Stabilized (business plan) |
|---|---|---|
| NOI | {_m(deal.in_place_noi)} | {_m(deal.stabilized_noi)} |
| Loan-to-value (LTV) | {_p(u['as_is_ltv'])} | {_p(u['as_stabilized_ltv'])} |
| Debt yield (NOI / loan) | {_p(u['in_place_debt_yield'], 2)} | {_p(u['stabilized_debt_yield'], 2)} |
| DSCR on bridge interest | {u['in_place_dscr_io']:.2f}x | {u['stabilized_dscr_io']:.2f}x |

Today's NOI {"does not cover" if u['annual_shortfall_now'] > 0 else "covers"} interest{f" (shortfall about {_m(u['annual_shortfall_now'])}/yr; reserve covers ~{u['reserve_months_cover']:.0f} months at today's draw)" if u['annual_shortfall_now'] > 0 else ""}.

## Exit analysis: who takes us out?

{exit_story}

A sale repays us as long as the exit cap rate stays below **{_p(u['breakeven_exit_cap_sale'], 2)}**, versus {_p(deal.exit_cap_rate, 2)} underwritten: a **{(u['breakeven_exit_cap_sale'] - deal.exit_cap_rate) * 1e4:.0f}bp cushion**.

## Stress test (10,000 Monte Carlo paths)

Each path simulates SOFR and the 10-year Treasury month by month, moves cap rates with Treasuries, delays the business plan, and shortchanges NOI, then tests refi / sale / extension at maturity.

![outcomes]({chart_file})

Outcomes: repaid by refi {_p(s['p_refi'] + s['p_ext_refi'])}, repaid by sale {_p(s['p_sale'] + s['p_ext_sale'])}, default {_p(s['pd'])}.

| Scenario | PD | Expected loss |
|---|---|---|
{sens_rows}

Biggest single sensitivity: **{worst['scenario']}** takes expected loss from {_p(sens[0]['el'], 2)} to {_p(worst['el'], 2)}.

## Conditions / what would change my mind

- Exit is sale-dependent while the perm rate sits near {_p(u['refi_rate'], 1)}. Confirm sale comps support a {_p(deal.exit_cap_rate, 2)} cap.
- Extension test: in-place debt yield of at least {_p(deal.extension_min_debt_yield, 1)}. Consider tightening if PD at maturity is the main risk.
- Verify sponsor liquidity of {_m(deal.sponsor_liquidity)} with bank statements; the model leans on it to cover carry.
- Rate cap at {deal.rate_cap_strike:.2f}% SOFR strike required for the full term including extension.
"""
