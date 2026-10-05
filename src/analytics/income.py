"""Seasonal production and quarterly income forecast for NEPSE-listed hydropower companies.

Everything here is an ESTIMATE: capacity x assumed seasonal capacity factors x PPA rates, scaled once per company
to its last reported year of electricity sales. Assumptions live in config and are echoed in every result."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import CompanyFact

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
