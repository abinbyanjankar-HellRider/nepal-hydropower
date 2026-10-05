"""Seasonal production and quarterly income forecast for NEPSE-listed hydropower companies.

Everything here is an ESTIMATE: capacity x assumed seasonal capacity factors x PPA rates, scaled once per company
to its last reported year of electricity sales. Assumptions live in config and are echoed in every result."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import config
from ..models import Company, CompanyFact, CompanyFinancial, Project, ProjectStatus

WET_FACT = "ppa_wet_npr_kwh"
DRY_FACT = "ppa_dry_npr_kwh"
# Fiscal quarters from Shrawan: (label, wet days, dry days). Wet = mid-Apr to mid-Dec, dry = mid-Dec to mid-Apr.
QUARTERS = (("Q1", 92, 0), ("Q2", 60, 29), ("Q3", 0, 91), ("Q4", 93, 0))
WET_DAYS = sum(w for _, w, _ in QUARTERS)  # 245
DRY_DAYS = sum(d for _, _, d in QUARTERS)  # 120


@dataclass(frozen=True)
class Rate:
    wet: float
    dry: float
    basis: str  # project / company / assumed


def seasonal_factors(capacity_factor: float | None = None, dry_share: float | None = None) -> tuple[float, float]:
    """(wet, dry) capacity factors that reproduce the annual factor with `dry_share` of energy in the dry season."""
    cf = config.ASSUMED_CAPACITY_FACTOR if capacity_factor is None else capacity_factor
    ds = config.ASSUMED_DRY_SEASON_ENERGY_SHARE if dry_share is None else dry_share
    return cf * (1 - ds) * 365 / WET_DAYS, cf * ds * 365 / DRY_DAYS


def plant_quarter_energy_gwh(mw: float, wet_cf: float, dry_cf: float) -> list[tuple[float, float]]:
    """[(wet GWh, dry GWh)] for each fiscal quarter."""
    return [(mw * wd * 24 * wet_cf / 1000, mw * dd * 24 * dry_cf / 1000) for _, wd, dd in QUARTERS]


def load_ppa_rates(session: Session) -> dict[tuple[int, str | None], tuple[float, float]]:
    """Verified PPA rates keyed by (company_id, project_id or None). A key needs both a wet and a dry value."""
    rows = session.execute(select(
        CompanyFact.company_id, CompanyFact.project_id, CompanyFact.fact_type, CompanyFact.value_num,
    ).where(CompanyFact.verified.is_(True), CompanyFact.fact_type.in_((WET_FACT, DRY_FACT)),
            CompanyFact.value_num.is_not(None))).all()
    parts: dict[tuple[int, str | None], dict[str, float]] = {}
    for company_id, project_id, fact_type, value in rows:
        parts.setdefault((company_id, project_id), {})[fact_type] = value
    return {k: (v[WET_FACT], v[DRY_FACT]) for k, v in parts.items() if WET_FACT in v and DRY_FACT in v}


def resolve_rate(rates: dict, company_id: int, project_id: str | None) -> Rate:
    """Project-level verified rate, else company-level verified rate, else the config fallback."""
    for key, basis in (((company_id, project_id), "project"), ((company_id, None), "company")):
        if key in rates:
            wet, dry = rates[key]
            return Rate(wet, dry, basis)
    return Rate(config.ASSUMED_WET_TARIFF_NPR_PER_KWH, config.ASSUMED_DRY_TARIFF_NPR_PER_KWH, "assumed")


CALIBRATION_MIN, CALIBRATION_MAX = 0.3, 1.5
NEPALI_FY_TO_AD = 57  # a Nepali fiscal year 2082/83 starts in mid-July 2025


def operating_plants(session: Session) -> list[dict]:
    """Operating plants with capacity that belong to a NEPSE-listed company (owner, else developer)."""
    owner_id = func.coalesce(Project.owner_company_id, Project.developer_company_id)
    rows = session.execute(
        select(Project.project_id, Project.project_name_en, Project.capacity_mw, Project.commissioning_year,
               Company.company_id, Company.stock_symbol, Company.listed_name, Company.company_name)
        .join(Company, Company.company_id == owner_id)
        .where(Project.status == ProjectStatus.OPERATIONAL, Project.capacity_mw > 0, Company.nepse_listed.is_(True))
        .order_by(Company.stock_symbol, Project.project_id)).all()
    return [{"company_id": r.company_id, "symbol": r.stock_symbol, "name": r.listed_name or r.company_name,
             "project_id": r.project_id, "plant": r.project_name_en, "mw": float(r.capacity_mw),
             "commissioning_year": r.commissioning_year} for r in rows]


def fiscal_year_start_ad(fiscal_year: str) -> int:
    """'2082/83' -> 2025, the calendar year in which that Nepali fiscal year begins."""
    return int(fiscal_year[:4]) - NEPALI_FY_TO_AD


def next_fiscal_year(fiscal_year: str) -> str:
    start = int(fiscal_year[:4]) + 1
    return f"{start}/{(start + 1) % 100:02d}"


def latest_full_year(session: Session) -> dict[int, tuple[str, float]]:
    """company_id -> (fiscal_year, electricity_sales_npr) from its newest quarter-4 row that reports sales."""
    rows = session.execute(
        select(CompanyFinancial.company_id, CompanyFinancial.fiscal_year, CompanyFinancial.electricity_sales_npr)
        .where(CompanyFinancial.quarter == 4, CompanyFinancial.electricity_sales_npr.is_not(None))
        .order_by(CompanyFinancial.fiscal_year)).all()
    latest: dict[int, tuple[str, float]] = {}
    for company_id, fiscal_year, sales in rows:  # ascending, so the last write is the newest
        latest[company_id] = (fiscal_year, sales)
    return latest


def quarterly_net_profit(session: Session, fiscal_year: str) -> dict[int, list[float | None]]:
    """company_id -> cumulative net profit at the end of Q1..Q4 of `fiscal_year` (None where missing)."""
    rows = session.execute(
        select(CompanyFinancial.company_id, CompanyFinancial.quarter, CompanyFinancial.net_profit_npr)
        .where(CompanyFinancial.fiscal_year == fiscal_year)).all()
    out: dict[int, list[float | None]] = {}
    for company_id, quarter, profit in rows:
        if 1 <= quarter <= 4:
            out.setdefault(company_id, [None] * 4)[quarter - 1] = profit
    return out


def calibration(reported_sales: float | None, modelled_income: float, partial_year: bool) -> dict:
    """One annual scale factor: reported sales / modelled income, clipped. Skipped when it cannot be trusted."""
    if partial_year:
        return {"factor": 1.0, "raw_factor": None, "basis": "uncalibrated", "flags": ["partial_year"]}
    if not reported_sales or reported_sales <= 0 or modelled_income <= 0:
        return {"factor": 1.0, "raw_factor": None, "basis": "uncalibrated", "flags": ["no_reported_sales"]}
    raw = reported_sales / modelled_income
    applied = min(max(raw, CALIBRATION_MIN), CALIBRATION_MAX)
    return {"factor": applied, "raw_factor": raw, "basis": "reported_sales",
            "flags": ["calibration_clipped"] if applied != raw else []}


def rank_companies(rows: list[dict]) -> list[dict]:
    """Highest annual income first; equal incomes fall back to symbol order. Adds a 1-based `rank`."""
    ordered = sorted(rows, key=lambda r: (-r["annual_income_npr"], r["symbol"] or ""))
    for position, row in enumerate(ordered, 1):
        row["rank"] = position
    return ordered


def shape_gap_pp(cumulative: list[float | None] | None, model_income: list[float]) -> float | None:
    """Mean absolute gap (percentage points) between each quarter's share of reported profit and of modelled income.
    Profit is more seasonal than income (costs are fixed), so this is a hint, not an error measure."""
    if not cumulative or any(v is None for v in cumulative) or cumulative[-1] <= 0:
        return None
    increments = [cumulative[0]] + [cumulative[i] - cumulative[i - 1] for i in range(1, 4)]
    total, model_total = sum(increments), sum(model_income)
    if any(x < 0 for x in increments) or total <= 0 or model_total <= 0:
        return None
    return round(100 * sum(abs(a / total - m / model_total) for a, m in zip(increments, model_income)) / 4, 1)


def forecast(session: Session, capacity_factor: float | None = None, dry_share: float | None = None) -> dict:
    """Seasonal usage, four-quarter income forecast and ranking for every listed company with an operating plant."""
    cf = config.ASSUMED_CAPACITY_FACTOR if capacity_factor is None else capacity_factor
    ds = config.ASSUMED_DRY_SEASON_ENERGY_SHARE if dry_share is None else dry_share
    wet_cf, dry_cf = seasonal_factors(cf, ds)
    rates = load_ppa_rates(session)
    reported = latest_full_year(session)
    reported_fy = max((fy for fy, _ in reported.values()), default=None)
    start_ad = fiscal_year_start_ad(reported_fy) if reported_fy else None
    horizon_fy = next_fiscal_year(reported_fy) if reported_fy else None
    labels = [f"{horizon_fy} {q}" if horizon_fy else q for q, _, _ in QUARTERS]
    profit_by_company = quarterly_net_profit(session, reported_fy) if reported_fy else {}

    grouped: dict[int, dict] = {}
    for plant in operating_plants(session):
        entry = grouped.setdefault(plant["company_id"], {
            "company_id": plant["company_id"], "symbol": plant["symbol"], "name": plant["name"], "plants": []})
        entry["plants"].append((plant, resolve_rate(rates, plant["company_id"], plant["project_id"]),
                                plant_quarter_energy_gwh(plant["mw"], wet_cf, dry_cf)))

    rows = []
    for entry in grouped.values():
        plants = entry["plants"]
        mw = sum(p["mw"] for p, _, _ in plants)
        wet_q = [sum(e[i][0] for _, _, e in plants) for i in range(4)]
        dry_q = [sum(e[i][1] for _, _, e in plants) for i in range(4)]
        income_q = [sum((e[i][0] * r.wet + e[i][1] * r.dry) * 1e6 for _, r, e in plants) for i in range(4)]
        modelled = sum(income_q)
        sales = reported.get(entry["company_id"])
        reported_sales = sales[1] if sales and sales[0] == reported_fy else None
        partial = start_ad is not None and any(
            p["commissioning_year"] is not None and p["commissioning_year"] >= start_ad for p, _, _ in plants)
        cal = calibration(reported_sales, modelled, partial)
        factor = cal["factor"]
        bases = {r.basis for _, r, _ in plants}
        flags = list(cal["flags"]) + (["assumed_rate"] if "assumed" in bases else [])
        wet_gwh, dry_gwh = sum(wet_q) * factor, sum(dry_q) * factor
        rows.append({
            "company_id": entry["company_id"], "symbol": entry["symbol"], "name": entry["name"],
            "capacity_mw": mw, "plants": len(plants),
            "wet_usage_pct": wet_gwh * 1000 / (mw * WET_DAYS * 24) * 100,
            "dry_usage_pct": dry_gwh * 1000 / (mw * DRY_DAYS * 24) * 100,
            "avg_usage_pct": (wet_gwh + dry_gwh) * 1000 / (mw * 8760) * 100,
            "wet_energy_gwh": wet_gwh, "dry_energy_gwh": dry_gwh,
            "quarters": [{"label": labels[i], "wet_gwh": wet_q[i] * factor, "dry_gwh": dry_q[i] * factor,
                          "income_npr": income_q[i] * factor} for i in range(4)],
            "annual_income_npr": modelled * factor,
            "rate_basis": "assumed" if bases == {"assumed"} else ("mixed" if "assumed" in bases else "verified"),
            "calibration": {"factor": factor, "raw_factor": cal["raw_factor"], "basis": cal["basis"]},
            "flags": flags,
            "profit_shape_gap_pp": shape_gap_pp(profit_by_company.get(entry["company_id"]), income_q),
        })

    companies = rank_companies(rows)
    total_mw = sum(c["capacity_mw"] for c in companies)
    wet_total = sum(c["wet_energy_gwh"] for c in companies)
    dry_total = sum(c["dry_energy_gwh"] for c in companies)
    totals = {
        "companies": len(companies), "capacity_mw": total_mw,
        "annual_income_npr": sum(c["annual_income_npr"] for c in companies),
        "wet_usage_pct": wet_total * 1000 / (total_mw * WET_DAYS * 24) * 100 if total_mw else None,
        "dry_usage_pct": dry_total * 1000 / (total_mw * DRY_DAYS * 24) * 100 if total_mw else None,
        "avg_usage_pct": (wet_total + dry_total) * 1000 / (total_mw * 8760) * 100 if total_mw else None,
    }
    return {
        "assumptions": {
            "capacity_factor": cf, "dry_energy_share": ds, "wet_usage_factor": wet_cf, "dry_usage_factor": dry_cf,
            "fallback_wet_tariff": config.ASSUMED_WET_TARIFF_NPR_PER_KWH,
            "fallback_dry_tariff": config.ASSUMED_DRY_TARIFF_NPR_PER_KWH,
            "calibration_range": [CALIBRATION_MIN, CALIBRATION_MAX],
            "reported_fiscal_year": reported_fy, "forecast_fiscal_year": horizon_fy},
        "quarters": labels, "totals": totals, "companies": companies,
    }
