"""Screen every deal in deals/ and write credit memos + charts to output/.

    python run.py                       # all deals
    python run.py deals/miami_multifamily.yaml
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

from bridgeloan.charts import deal_chart, portfolio_chart
from bridgeloan.deal import load_deal
from bridgeloan.memo import write_memo
from bridgeloan.metrics import underwrite
from bridgeloan.simulate import max_approvable_commitment, sensitivities, simulate, verdict

OUT = Path("output")


def main(paths: list[str]) -> None:
    OUT.mkdir(exist_ok=True)
    results, rows = [], []
    for p in paths:
        deal, mkt = load_deal(p)
        slug = Path(p).stem
        u = underwrite(deal, mkt)
        res = simulate(deal, mkt)
        sens = sensitivities(deal, mkt)
        max_loan = max_approvable_commitment(deal, mkt)
        deal_chart(res, OUT / f"{slug}.png")
        (OUT / f"{slug}_memo.md").write_text(
            write_memo(deal, mkt, u, res, sens, max_loan, f"{slug}.png"))
        results.append(res)
        call = verdict(res)[0]
        rows.append((deal.market, deal.commitment, u["ltc"], res.pd, res.el_annual, call, max_loan))
        print(f"{deal.name:<40} PD {res.pd:6.1%}  EL/yr {res.el_annual:6.2%}  -> {call}")

    if len(results) > 1:
        portfolio_chart(results, OUT / "portfolio.png")
    lines = ["| Market | Loan | LTC | PD | Annual EL | Call | Max loan for approve |",
             "|---|---|---|---|---|---|---|"]
    lines += [f"| {m} | ${c / 1e6:.2f}M | {ltc:.0%} | {pd_:.1%} | {el:.2%} | {call} | ${ml / 1e6:.2f}M |"
              for m, c, ltc, pd_, el, call, ml in rows]
    (OUT / "summary.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(glob.glob("deals/*.yaml")))
