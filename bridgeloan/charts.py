from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .simulate import SimResult  # noqa: E402

REPAID = "#3b6ea5"
DEFAULT = "#c8553d"
plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})


def deal_chart(res: SimResult, path: Path) -> None:
    """Left: where each path ended up. Right: what drives default."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))

    shares = res.outcome_shares()
    labels = {"refi": "Refi at maturity", "sale": "Sale at maturity",
              "ext_refi": "Extend, then refi", "ext_sale": "Extend, then sale",
              "term_default": "Default: ran out of cash", "mat_default": "Default: no exit at maturity"}
    keys = [k for k in labels if shares[k] > 0]
    colors = [DEFAULT if "default" in k else REPAID for k in keys]
    ax1.barh([labels[k] for k in keys][::-1], [shares[k] * 100 for k in keys][::-1], color=colors[::-1])
    for i, k in enumerate(keys[::-1]):
        ax1.text(shares[k] * 100 + 0.8, i, f"{shares[k]:.1%}", va="center", fontsize=9)
    ax1.set_xlabel("% of 10,000 simulated paths")
    ax1.set_title("How the loan gets repaid (or doesn't)", loc="left", fontweight="bold")
    ax1.set_xlim(0, 105)

    d = res.default
    ax2.scatter(res.attainment[~d] * 100, res.exit_cap[~d] * 100, s=3, alpha=0.25, color=REPAID, label="Repaid")
    ax2.scatter(res.attainment[d] * 100, res.exit_cap[d] * 100, s=5, alpha=0.6, color=DEFAULT, label="Default")
    ax2.set_xlabel("NOI achieved, % of business plan")
    ax2.set_ylabel("Exit cap rate (%)")
    ax2.set_title("Defaults cluster where NOI misses AND caps widen", loc="left", fontweight="bold")
    ax2.legend(markerscale=4, frameon=False, loc="upper left")

    fig.suptitle(res.deal.name, x=0.01, ha="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def portfolio_chart(results: list[SimResult], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 3.6))
    names = [r.deal.market for r in results]
    x = np.arange(len(results))
    w = 0.38
    el = [r.el_annual * 100 for r in results]
    spread = [r.deal.spread for r in results]
    ax.bar(x - w / 2, spread, w, color=REPAID, label="Spread over SOFR (what we earn)")
    ax.bar(x + w / 2, el, w, color=DEFAULT, label="Annual expected loss (what we lose)")
    for i, v in enumerate(el):
        ax.text(i + w / 2, v + 0.05, f"{v:.2f}%", ha="center", fontsize=9)
    ax.set_xticks(x, names)
    ax.set_ylabel("% per year")
    ax.set_title("Is the spread paying for the risk?", loc="left", fontweight="bold")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
