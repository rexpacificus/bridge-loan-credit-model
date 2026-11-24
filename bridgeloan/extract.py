"""Turn a messy listing (broker OM text, LoopNet/Crexi copy-paste) into deal terms.

Two modes:
  * AI mode: if ANTHROPIC_API_KEY is set and the `anthropic` package is
    installed, Claude reads the listing and returns structured JSON.
  * Regex mode (default fallback): pattern-matches the common fields. Works on
    clean listings, misses things on messy ones. That's the point of the AI layer.

Either way the output is a partial deal. Loan terms (how much WE lend, at
what spread) are the lender's decision, not the listing's, so `draft_deal`
fills those with standard sizing rules that you then edit.

Usage:
    python -m bridgeloan.extract listings/sample_listing.txt > deals/new_deal.yaml
"""

from __future__ import annotations

import json
import os
import re
import sys

import yaml

FIELDS = {
    "name": "short deal name",
    "market": "city, state",
    "property_type": "e.g. Multifamily, Mixed-use, Office",
    "units": "integer number of units",
    "purchase_price": "asking or purchase price in dollars",
    "in_place_noi": "current annual net operating income in dollars",
    "cap_rate": "going-in cap rate as a decimal (0.0525)",
    "occupancy": "current occupancy as a decimal",
    "capex_budget": "renovation budget in dollars, if stated",
    "pro_forma_noi": "projected/stabilized NOI in dollars, if stated",
}

_MONEY = r"\$?\s*([\d,]+(?:\.\d+)?)\s*(m|mm|million|k)?"


def _money(s: str, unit: str | None) -> float:
    v = float(s.replace(",", ""))
    if unit:
        unit = unit.lower()
        v *= 1_000 if unit == "k" else 1_000_000
    return v


def extract_regex(text: str) -> dict:
    out: dict = {}
    pats = {
        "purchase_price": rf"(?:asking\s+price|list\s+price|price)\s*[:\-]?\s*{_MONEY}",
        "in_place_noi": rf"(?:in[- ]place\s+|current\s+|t-?12\s+)?noi\s*[:\-]?\s*{_MONEY}",
        "pro_forma_noi": rf"(?:pro[- ]?forma|stabilized|projected)\s+noi\s*[:\-]?\s*{_MONEY}",
        "capex_budget": rf"(?:capex|renovation\s+budget|capital\s+improvements?)\s*[:\-]?\s*{_MONEY}",
    }
    for k, p in pats.items():
        m = re.search(p, text, re.I)
        if m:
            out[k] = _money(m.group(1), m.group(2))
    if m := re.search(r"cap\s*rate\s*[:\-]?\s*([\d.]+)\s*%", text, re.I):
        out["cap_rate"] = float(m.group(1)) / 100
    if m := re.search(r"(\d+)\s*(?:residential\s+)?units", text, re.I):
        out["units"] = int(m.group(1))
    if m := re.search(r"(?:occupancy|occupied|leased)\s*[:\-]?\s*([\d.]+)\s*%", text, re.I):
        out["occupancy"] = float(m.group(1)) / 100
    if "in_place_noi" not in out and "purchase_price" in out and "cap_rate" in out:
        out["in_place_noi"] = out["purchase_price"] * out["cap_rate"]
    return out


def extract_ai(text: str) -> dict:
    import anthropic  # optional dependency

    client = anthropic.Anthropic()
    schema = "\n".join(f"- {k}: {v}" for k, v in FIELDS.items())
    msg = client.messages.create(
        model=os.environ.get("BRIDGELOAN_MODEL", "claude-sonnet-5-5"),
        max_tokens=800,
        messages=[{"role": "user", "content": (
            "Extract these fields from the real estate listing below. Return ONLY a JSON "
            "object. Use null for anything not stated; do not guess.\n"
            f"{schema}\n\nLISTING:\n{text}")}],
    )
    raw = msg.content[0].text
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    return {k: v for k, v in json.loads(raw).items() if v is not None}


def extract(text: str) -> tuple[dict, str]:
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return extract_ai(text), "ai"
        except Exception as e:  # fall back rather than crash a screening run
            print(f"[extract] AI extraction failed ({e}); using regex", file=sys.stderr)
    return extract_regex(text), "regex"


def draft_deal(terms: dict) -> dict:
    """Fill a deal file using standard bridge sizing rules of thumb:
    75% of price at close, ~80% of capex as future funding, SOFR + 4.00%,
    24 + 12 months. Every one of these is meant to be edited."""
    price = terms.get("purchase_price", 0.0)
    noi = terms.get("in_place_noi", 0.0)
    capex = terms.get("capex_budget", round(price * 0.12, -4))
    stab = terms.get("pro_forma_noi", round(noi * 1.30, -3))
    return {
        "name": terms.get("name", "New deal"),
        "market": terms.get("market", ""),
        "property_type": terms.get("property_type", ""),
        "units": terms.get("units"),
        "purchase_price": price,
        "capex_budget": capex,
        "closing_costs": round(price * 0.02, -3),
        "in_place_noi": noi,
        "stabilized_noi": stab,
        "months_to_stabilize": 15,
        "initial_funding": round(price * 0.75, -4),
        "future_funding": round(capex * 0.80, -4),
        "spread": 4.00,
        "interest_reserve": round(price * 0.02, -4),
        "sponsor_liquidity": round(price * 0.03, -4),
        "exit_cap_rate": round(max(terms.get("cap_rate", 0.055) + 0.0025, 0.05), 4),
    }


if __name__ == "__main__":
    text = open(sys.argv[1]).read()
    terms, mode = extract(text)
    print(f"# extracted with {mode} mode; REVIEW EVERY NUMBER before running")
    print(f"# raw extraction: {json.dumps(terms)}")
    print(yaml.safe_dump(draft_deal(terms), sort_keys=False))
