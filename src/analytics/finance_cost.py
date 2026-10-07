"""Finance-cost analysis: implied interest rate, repayment path, and what a lower rate does to profit, EPS and
book value per share. Company-level figures from the latest reported full year; per-plant numbers are allocations."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Company, CompanyFact, CompanyFinancial
from .income import operating_plants

DEFAULT_RATE_DELTA_PP = -2.0
DEFAULT_REPAY_PCT = 8.0
DEFAULT_RETENTION_PCT = 70.0
DEFAULT_YEARS = 5
MIN_LOANS_NPR = 1_000_000.0
RATE_RANGE_PCT = (2.0, 20.0)


TAX_HOLIDAY_MAX_RATE = 0.02   # effective tax rate below this: paying (almost) no tax, i.e. a full holiday
TAXED_MIN_RATE = 0.17         # at or above this: taxed in full (a 50% exemption on the 25% rate gives about 12.5%)


def tax_status(net_profit, tax_provision) -> str:
    """Tax position of a company read from its reported net profit and tax provision (a derived label, not a legal status)."""
    if net_profit is None and tax_provision is None:
        return "No data"
    profit, tax = net_profit or 0.0, tax_provision or 0.0
    pretax = profit + tax
    if profit <= 0 or pretax <= 0:
        return "Loss-making"
    if tax < 0:
        return "Tax credit"
    rate = tax / pretax
    if rate < TAX_HOLIDAY_MAX_RATE:
        return "Tax holiday"
    return "Partly exempt" if rate < TAXED_MIN_RATE else "Taxed"


def build_position(loans, finance_cost, net_profit, tax_provision, paid_up, eps, bvps, electricity_sales=None) -> dict:
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
    if (net_profit or 0.0) <= 0 or pretax <= 0:
        tax_rate = 0.0          # a loss after tax: extra profit is not taxed until break-even
        flags.append("loss_making")
    elif tax < 0:
        tax_rate = 0.0          # profitable with a deferred-tax credit: no tax on extra profit, not a loss
    else:
        tax_rate = min(tax / pretax, 1.0)
    cover = (pretax + finance_cost) / finance_cost if finance_cost > 0 else None
    return {"loans": loans, "finance_cost": finance_cost, "implied_rate_pct": rate, "tax_rate": tax_rate,
            "shares": shares, "eps": eps, "bvps": bvps, "interest_cover": cover, "flags": flags,
            "net_profit": net_profit, "tax_provision": tax_provision, "electricity_sales": electricity_sales,
            "tax_status": tax_status(net_profit, tax_provision)}


def forecast_year(position: dict, forecast_income: float | None, repay_pct: float = DEFAULT_REPAY_PCT,
                  retention_pct: float = DEFAULT_RETENTION_PCT) -> dict | None:
    """Net profit, EPS and book value per share for the forecast year, anchored on the last reported year.

    net profit = reported net profit + [(forecast income - reported electricity sales) - (forecast finance cost -
    reported finance cost)] x (1 - effective tax rate). Operating costs are held fixed, so a change in income flows
    straight to profit; finance cost is the implied rate on the average loan balance after `repay_pct` repayment;
    book value grows by the retained part of the profit. None when there is no reported-sales anchor."""
    sales, shares = position.get("electricity_sales"), position["shares"]
    if forecast_income is None or not sales or sales <= 0 or not shares or position.get("net_profit") is None:
        return None
    rate, reported_cost = position["implied_rate_pct"], position["finance_cost"]
    cost = rate / 100 * position["loans"] * (1 - repay_pct / 200) if rate is not None else reported_cost
    pretax_change = (forecast_income - sales) - (cost - reported_cost)
    profit = position["net_profit"] + pretax_change * (1 - position["tax_rate"])
    bvps = position["bvps"]
    return {"net_profit": profit, "eps": profit / shares,
            "bvps": None if bvps is None else bvps + profit * retention_pct / 100 / shares,
            "finance_cost": cost, "income_change": forecast_income - sales, "finance_cost_change": cost - reported_cost}


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
            row.eps, row.networth_per_share, row.electricity_sales_npr), "fiscal_year": row.fiscal_year}
    return latest


def _holiday_notes(session: Session) -> dict[int, str]:
    """company_id -> the tax-holiday wording a report used (e.g. '100% tax holiday and 5 year after that 50% tax')."""
    rows = session.execute(select(CompanyFact.company_id, CompanyFact.value_text).where(
        CompanyFact.fact_type == "tax_holiday_mention", CompanyFact.value_text.is_not(None))).all()
    notes: dict[int, str] = {}
    for company_id, text in rows:
        notes.setdefault(company_id, text)
    return notes


def _listed(session: Session) -> dict[int, tuple[str | None, str]]:
    return {c.company_id: (c.stock_symbol, c.listed_name or c.company_name)
            for c in session.execute(select(Company).where(Company.nepse_listed.is_(True))).scalars()}


def enrich_forecast(session: Session, forecast: dict, repay_pct: float = DEFAULT_REPAY_PCT,
                    retention_pct: float = DEFAULT_RETENTION_PCT) -> dict:
    """Add tax status and forecast net profit / EPS / book value per share to every company of an income forecast,
    and list the remaining NEPSE-listed companies (no operating plant) under `others` so none is left out."""
    positions, holiday, listed = latest_positions(session), _holiday_notes(session), _listed(session)

    def fields(company_id: int, income: float | None) -> dict:
        pos = positions.get(company_id)
        out = {"tax_status": "No data", "tax_rate_pct": None, "tax_holiday_note": holiday.get(company_id),
               "net_profit_reported": None, "eps_reported": None, "bvps_reported": None,
               "net_profit_forecast": None, "eps_forecast": None, "bvps_forecast": None, "forecast_flags": []}
        if pos is None:
            out["forecast_flags"].append("no_financials")
            return out
        out.update(tax_status=pos["tax_status"], tax_rate_pct=100 * pos["tax_rate"], net_profit_reported=pos["net_profit"],
                   eps_reported=pos["eps"], bvps_reported=pos["bvps"])
        result = forecast_year(pos, income, repay_pct, retention_pct)
        if result is not None:
            out.update(net_profit_forecast=result["net_profit"], eps_forecast=result["eps"], bvps_forecast=result["bvps"])
        elif income is not None:
            out["forecast_flags"].append("no_reported_sales" if not pos.get("electricity_sales") else "no_shares")
        return out

    seen = set()
    for row in forecast["companies"]:
        seen.add(row["company_id"])
        income, note = row["annual_income_npr"], None
        if "calibration_clipped" in row["flags"]:
            # Model and reported sales disagree beyond the calibration limits (a capacity or tariff problem in the data):
            # the income change would be noise, so hold income at last year's reported sales and move only finance cost.
            income = (positions.get(row["company_id"]) or {}).get("electricity_sales") or income
            note = "flat_income"
        elif "partial_year" in row["flags"]:
            note = "income_not_anchored"  # a full year of a plant that started mid-year: real growth, but pure model
        row.update(fields(row["company_id"], income))
        if note:
            row["forecast_flags"].append(note)
    forecast["others"] = [
        {"company_id": cid, "symbol": symbol, "name": name, "flags": ["no_operating_plant"], **fields(cid, None)}
        for cid, (symbol, name) in sorted(listed.items(), key=lambda kv: kv[1][0] or "") if cid not in seen]
    forecast["assumptions"].update(repay_pct=repay_pct, retention_pct=retention_pct)
    return forecast


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
    names = _listed(session)
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
            "tax_rate": pos["tax_rate"], "tax_rate_pct": 100 * pos["tax_rate"], "tax_status": pos["tax_status"],
            "interest_cover": pos["interest_cover"], "burden_pct": burden,
            "flags": pos["flags"],
            "scenario": scenario(pos, rate_delta_pp, repay_pct, retention_pct, years),
            "plants": allocate_to_plants(pos["loans"], pos["finance_cost"], plants_by_company.get(company_id, [])),
        })
    for company_id, (symbol, name) in names.items():       # listed companies with no full-year figures (newly listed)
        if company_id not in positions:
            rows.append({"company_id": company_id, "symbol": symbol, "name": name, "fiscal_year": None, "loans": None,
                         "finance_cost": None, "implied_rate_pct": None, "tax_rate": None, "tax_rate_pct": None,
                         "tax_status": "No data", "interest_cover": None, "burden_pct": None, "flags": ["no_financials"],
                         "scenario": None, "plants": allocate_to_plants(0.0, 0.0, plants_by_company.get(company_id, []))})
    rows.sort(key=lambda r: (r["burden_pct"] is None, -(r["burden_pct"] or 0), r["symbol"] or ""))
    for position, row in enumerate(rows, 1):
        row["burden_rank"] = position if row["burden_pct"] is not None else None
    return {"assumptions": {"rate_delta_pp": rate_delta_pp, "repay_pct": repay_pct,
                            "retention_pct": retention_pct, "years": years},
            "companies": rows}
