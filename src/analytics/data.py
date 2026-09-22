"""Load the project table as one flat DataFrame for analysis."""
from __future__ import annotations

import re

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from ..models import Company, District, Project, ProjectStatus, River

PIPELINE = (ProjectStatus.UNDER_CONSTRUCTION, ProjectStatus.LICENSED)


def clean_river(name) -> str | None:
    """'Khimti Khola' / 'khimti nadi' -> 'Khimti' so the same river groups together."""
    if not isinstance(name, str) or not name.strip():
        return None
    base = re.sub(r"\(.*?\)", "", name)
    base = re.sub(r"\b(khola|nadi|river|kholsi|gad)\b", "", base, flags=re.I)
    base = re.sub(r"[^A-Za-z ]", " ", base)
    return " ".join(base.split()).title() or None


def load_projects_df(session: Session) -> pd.DataFrame:
    dev, own = aliased(Company), aliased(Company)
    stmt = (
        select(
            Project.project_id, Project.project_name_en.label("name"), Project.capacity_mw, Project.status,
            Project.project_type, Project.province, District.district_name.label("district"),
            Project.river_name_raw, River.river_name.label("river_linked"), Project.commissioning_year, Project.expected_completion_year,
            Project.license_type, Project.license_expiry_date, Project.latitude, Project.longitude,
            Project.annual_energy_generation_gwh, Project.ppa_rate_npr_per_kwh, Project.data_reliability,
            dev.company_name.label("developer"), dev.stock_symbol.label("developer_symbol"),
            own.company_name.label("owner"), own.stock_symbol.label("owner_symbol"),
        )
        .outerjoin(District, Project.district_id == District.district_id)
        .outerjoin(River, Project.river_id == River.river_id)
        .outerjoin(dev, Project.developer_company_id == dev.company_id)
        .outerjoin(own, Project.owner_company_id == own.company_id)
    )
    df = pd.DataFrame(session.execute(stmt).mappings().all())
    if df.empty:
        return df
    df["status"] = df["status"].map(lambda s: s.value)
    df["project_type"] = df["project_type"].map(lambda t: t.value if t is not None else None)
    # river as reported by the source, else the linked river record (seed / CSV / API rows only have the link)
    df["river"] = df["river_name_raw"].map(clean_river).fillna(df["river_linked"].map(clean_river))
    df["province"] = df["province"].fillna("Unknown")
    df["district"] = df["district"].fillna("Unknown")
    df["symbol"] = df["owner_symbol"].fillna(df["developer_symbol"])
    return df
