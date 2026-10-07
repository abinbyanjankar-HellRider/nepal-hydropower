"""Financial analytics: estimated revenue, reported financial history, IRR/payback."""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import Project, ProjectFinancial

HOURS_PER_YEAR = 8760


def blended_tariff(wet: float, dry: float, dry_share: float) -> float:
    return wet * (1 - dry_share) + dry * dry_share


def estimate_energy_gwh(capacity_mw: float, capacity_factor: float) -> float:
    return capacity_mw * HOURS_PER_YEAR * capacity_factor / 1000


def estimate_revenue_df(df: pd.DataFrame, capacity_factor: float | None = None, wet_tariff: float | None = None,
                        dry_tariff: float | None = None, dry_share: float | None = None,
                        status: str = "Operational", rates: dict | None = None) -> pd.DataFrame:
    """ESTIMATED annual energy and revenue = MW x 8760h x capacity factor x blended tariff.

    Uses the project's own annual_energy_generation_gwh / ppa_rate when known, otherwise the assumptions
    in config. The `basis` column says which energy figure was used. When `rates` (verified PPA rates from
    income.load_ppa_rates) is given, a plant without its own rate is priced at its verified project or company rate,
    and `tariff_basis` says which tariff applied: 'project record', 'verified project', 'verified company' or
    'assumed'. This is a scenario tool, not reported revenue.
    """
    from .income import resolve_rate  # local import: income depends on the models, not on this module
    cf = config.ASSUMED_CAPACITY_FACTOR if capacity_factor is None else capacity_factor
    share = config.ASSUMED_DRY_SEASON_ENERGY_SHARE if dry_share is None else dry_share
    tariff = blended_tariff(
        config.ASSUMED_WET_TARIFF_NPR_PER_KWH if wet_tariff is None else wet_tariff,
        config.ASSUMED_DRY_TARIFF_NPR_PER_KWH if dry_tariff is None else dry_tariff, share)
    d = df[(df["status"] == status) & df["capacity_mw"].notna()].copy()
    known_energy = d["annual_energy_generation_gwh"].notna()
    d["est_energy_gwh"] = d["annual_energy_generation_gwh"].where(
        known_energy, d["capacity_mw"].map(lambda mw: estimate_energy_gwh(mw, cf)))
    tariffs, bases = [], []
    company_ids = d["company_id"] if "company_id" in d.columns else pd.Series([None] * len(d), index=d.index)
    for own_rate, company_id, project_id in zip(d["ppa_rate_npr_per_kwh"], company_ids, d["project_id"]):
        if pd.notna(own_rate):
            tariffs.append(own_rate), bases.append("project record")
        elif rates and pd.notna(company_id) and (found := resolve_rate(rates, int(company_id), project_id)).basis != "assumed":
            tariffs.append(blended_tariff(found.wet, found.dry, share)), bases.append(f"verified {found.basis}")
        else:
            tariffs.append(tariff), bases.append("assumed")
    d["tariff_npr_kwh"], d["tariff_basis"] = tariffs, bases
    d["est_revenue_npr_m"] = d["est_energy_gwh"] * d["tariff_npr_kwh"]  # GWh x NPR/kWh = NPR million
    d["basis"] = known_energy.map({True: "project energy figure", False: f"assumed CF {cf:.0%}"})
    return d[["project_id", "name", "capacity_mw", "est_energy_gwh", "tariff_npr_kwh", "est_revenue_npr_m",
              "basis", "developer", "symbol", "tariff_basis"]].round(2)


def financial_history(session: Session, project_id: str) -> pd.DataFrame:
    """Reported annual financials for one project, with capacity factor derived when missing."""
    project = session.get(Project, project_id)
    rows = session.scalars(select(ProjectFinancial).where(ProjectFinancial.project_id == project_id)
                           .order_by(ProjectFinancial.fiscal_year)).all()
    out = pd.DataFrame([{
        "fiscal_year": r.fiscal_year, "energy_gwh": r.energy_generated_gwh, "revenue_npr": r.revenue_npr,
        "operating_cost_npr": r.operating_cost_npr, "net_profit_npr": r.net_profit_npr,
        "capacity_factor_pct": r.capacity_factor_percentage, "irr_pct": r.irr_percentage, "source": r.source,
    } for r in rows])
    if out.empty or project is None or not project.capacity_mw:
        return out
    derived = out["energy_gwh"] / (project.capacity_mw * HOURS_PER_YEAR / 1000) * 100
    out["capacity_factor_pct"] = out["capacity_factor_pct"].fillna(derived).round(1)
    out["realised_tariff_npr_kwh"] = (out["revenue_npr"] / (out["energy_gwh"] * 1e6)).round(2)
    return out


def portfolio_financials(session: Session) -> pd.DataFrame:
    """Reported generation and revenue summed by fiscal year across all projects with data."""
    rows = session.execute(select(ProjectFinancial.fiscal_year, ProjectFinancial.project_id,
                                  ProjectFinancial.energy_generated_gwh, ProjectFinancial.revenue_npr,
                                  ProjectFinancial.net_profit_npr)).all()
    df = pd.DataFrame(rows, columns=["fiscal_year", "project_id", "energy_gwh", "revenue_npr", "net_profit_npr"])
    if df.empty:
        return df
    return (df.groupby("fiscal_year").agg(projects=("project_id", "nunique"), energy_gwh=("energy_gwh", "sum"),
                                          revenue_npr=("revenue_npr", "sum"), net_profit_npr=("net_profit_npr", "sum"))
              .reset_index().round(1))


def irr(cashflows: list[float], lo: float = -0.99, hi: float = 1.0, tol: float = 1e-9) -> float | None:
    """Internal rate of return by bisection. cashflows[0] is the (negative) initial outlay."""
    def npv(rate: float) -> float:
        return sum(cf / (1 + rate) ** i for i, cf in enumerate(cashflows))
    if not (any(c < 0 for c in cashflows) and any(c > 0 for c in cashflows)):
        return None
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def project_return_profile(cost_npr: float, annual_net_cash_npr: float, life_years: int = 35,
                           construction_years: int = 0) -> dict:
    """Simple unlevered profile: payback and IRR for a flat annual net cash flow. Illustrative only."""
    flows = [-cost_npr] + [0.0] * construction_years + [annual_net_cash_npr] * life_years
    rate = irr(flows)
    return {"simple_payback_years": round(cost_npr / annual_net_cash_npr, 1) if annual_net_cash_npr > 0 else None,
            "irr_pct": round(rate * 100, 2) if rate is not None else None}
