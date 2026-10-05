"""Finance-cost analysis: implied interest rate, repayment path, and what a lower rate does to profit, EPS and
book value per share. Company-level figures from the latest reported full year; per-plant numbers are allocations."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Company, CompanyFinancial
from .income import operating_plants

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


def latest_positions(session: Session) -> dict[int, dict]:
    """company_id -> position (plus fiscal_year) from the newest quarter-4 row that reports loans, listed companies only."""
    rows = session.execute(
        select(CompanyFinancial).join(Company, Company.company_id == CompanyFinancial.company_id)
        .where(Company.nepse_listed.is_(True), CompanyFinancial.quarter == 4, CompanyFinancial.loans_npr.is_not(None))
        .order_by(CompanyFinancial.fiscal_year)).scalars().all()
    latest: dict[int, dict] = {}
    for row in rows:  # ascending fiscal year: the last write is the newest
        latest[row.company_id] = {**build_position(
            row.loans_npr, row.finance_cost_npr, row.net_profit_npr, row.tax_provision_npr, row.paid_up_capital_npr,
            row.eps, row.networth_per_share), "fiscal_year": row.fiscal_year}
    return latest


def allocate_to_plants(loans: float, finance_cost: float, plants: list[dict]) -> list[dict]:
    """Spread company loans and finance cost across its operating plants by MW share. An allocation, not reported."""
    total_mw = sum(p["mw"] for p in plants)
    if not plants or total_mw <= 0:
        return []
    return [{"project_id": p["project_id"], "plant": p["plant"], "mw": p["mw"], "share_pct": 100 * p["mw"] / total_mw,
             "loans": loans * p["mw"] / total_mw, "finance_cost": finance_cost * p["mw"] / total_mw} for p in plants]


def impact(session: Session, rate_delta_pp: float = DEFAULT_RATE_DELTA_PP, repay_pct: float = DEFAULT_REPAY_PCT,
           retention_pct: float = DEFAULT_RETENTION_PCT, years: int = DEFAULT_YEARS,
           income_by_company: dict[int, float] | None = None) -> dict:
    """Finance position and low-rate scenario for every listed company with reported loans, heaviest burden first."""
    income_by_company = income_by_company or {}
    positions = latest_positions(session)
    names = {c.company_id: (c.stock_symbol, c.listed_name or c.company_name)
             for c in session.execute(select(Company).where(Company.company_id.in_(positions))).scalars()}
    plants_by_company: dict[int, list[dict]] = {}
    for plant in operating_plants(session):
        plants_by_company.setdefault(plant["company_id"], []).append(plant)

    rows = []
    for company_id, pos in positions.items():
        symbol, name = names[company_id]
        income = income_by_company.get(company_id)
        burden = 100 * pos["finance_cost"] / income if income and income > 0 else None
        rows.append({
            "company_id": company_id, "symbol": symbol, "name": name, "fiscal_year": pos["fiscal_year"],
            "loans": pos["loans"], "finance_cost": pos["finance_cost"], "implied_rate_pct": pos["implied_rate_pct"],
            "tax_rate": pos["tax_rate"], "interest_cover": pos["interest_cover"], "burden_pct": burden,
            "flags": pos["flags"],
            "scenario": scenario(pos, rate_delta_pp, repay_pct, retention_pct, years),
            "plants": allocate_to_plants(pos["loans"], pos["finance_cost"], plants_by_company.get(company_id, [])),
        })
    rows.sort(key=lambda r: (r["burden_pct"] is None, -(r["burden_pct"] or 0), r["symbol"] or ""))
    for position, row in enumerate(rows, 1):
        row["burden_rank"] = position if row["burden_pct"] is not None else None
    return {"assumptions": {"rate_delta_pp": rate_delta_pp, "repay_pct": repay_pct,
                            "retention_pct": retention_pct, "years": years},
            "companies": rows}
