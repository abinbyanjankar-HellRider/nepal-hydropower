"""Finance-cost analysis: implied interest rate, repayment path, and what a lower rate does to profit, EPS and
book value per share. Company-level figures from the latest reported full year; per-plant numbers are allocations."""
from __future__ import annotations

DEFAULT_RATE_DELTA_PP = -2.0
DEFAULT_REPAY_PCT = 8.0
DEFAULT_RETENTION_PCT = 70.0
DEFAULT_YEARS = 5
MIN_LOANS_NPR = 1_000_000.0
RATE_RANGE_PCT = (2.0, 20.0)


def build_position(loans, finance_cost, net_profit, tax_provision, paid_up, eps, bvps) -> dict:
    """Current finance position of one company from its latest full-year figures (all NPR)."""
    flags: list[str] = []
    loans = loans or 0.0
    finance_cost = finance_cost or 0.0
    tax = tax_provision or 0.0
    shares = paid_up / 100 if paid_up and paid_up > 0 else None   # face value NPR 100; reproduces reported EPS
    if loans <= 0:
        flags.append("no_loans")
    elif loans < MIN_LOANS_NPR:
        flags.append("tiny_loans")
    if finance_cost <= 0:
        flags.append("zero_finance_cost")
    rate = None
    if loans >= MIN_LOANS_NPR and finance_cost > 0:
        rate = 100 * finance_cost / loans
        if not RATE_RANGE_PCT[0] <= rate <= RATE_RANGE_PCT[1]:
            flags.append("rate_out_of_range")
    pretax = (net_profit or 0.0) + tax
    if pretax > 0 and tax >= 0:
        tax_rate = min(tax / pretax, 1.0)
    else:
        tax_rate = 0.0
        flags.append("loss_making")
    cover = (pretax + finance_cost) / finance_cost if finance_cost > 0 else None
    return {"loans": loans, "finance_cost": finance_cost, "implied_rate_pct": rate, "tax_rate": tax_rate,
            "shares": shares, "eps": eps, "bvps": bvps, "interest_cover": cover, "flags": flags}


def scenario(position: dict, rate_delta_pp: float = DEFAULT_RATE_DELTA_PP, repay_pct: float = DEFAULT_REPAY_PCT,
             retention_pct: float = DEFAULT_RETENTION_PCT, years: int = DEFAULT_YEARS) -> dict | None:
    """Year-by-year effect of shifting the interest rate by `rate_delta_pp` against the base path in which the loan
    is repaid by `repay_pct` of the current balance each year. None when no rate can be implied."""
    rate = position["implied_rate_pct"]
    shares = position["shares"]
    if rate is None or not shares:
        return None
    new_rate = max(0.0, rate + rate_delta_pp)
    keep = 1 - repay_pct / 100
    cumulative_profit = 0.0
    rows = []
    for year in range(1, years + 1):
        opening = position["loans"] * keep ** (year - 1)
        base_cost = rate / 100 * opening
        scenario_cost = new_rate / 100 * opening
        saving = base_cost - scenario_cost
        profit_change = saving * (1 - position["tax_rate"])
        cumulative_profit += profit_change
        rows.append({"year": year, "opening_loans": opening, "base_finance_cost": base_cost,
                     "scenario_finance_cost": scenario_cost, "saving": saving, "net_profit_change": profit_change,
                     "eps_change": profit_change / shares,
                     "bvps_change": cumulative_profit * retention_pct / 100 / shares})
    eps, bvps = position["eps"], position["bvps"]
    return {"rate_scenario_pct": new_rate, "years": rows,
            "eps_baseline": eps, "eps_after_year1": None if eps is None else eps + rows[0]["eps_change"],
            "bvps_baseline": bvps, "bvps_after_horizon": None if bvps is None else bvps + rows[-1]["bvps_change"]}
