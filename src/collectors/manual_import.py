"""Load project records (dicts) into the database: used by the seed loader and CSV import."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd
from sqlalchemy import Boolean, Date, select
from sqlalchemy.orm import Session

from ..models import (
    Company, CompanyType, District, Project, ProjectFinancial, ProjectStatus, ProjectType, River,
    TurbineType,
)
from ..processors.cleaners import NEA_NAME, company_key

# Columns on Project that a record may set directly.
PROJECT_COLUMNS = {c.name for c in Project.__table__.columns} - {"created_at", "updated_at"}
# Friendly name keys accepted in records, resolved to FK ids.
NAME_KEYS = {"district", "river", "developer", "owner"}

_ENUMS = {"status": ProjectStatus, "project_type": ProjectType, "turbine_type": TurbineType}


def _clean(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _to_enum(enum_cls, value):
    if value is None or isinstance(value, enum_cls):
        return value
    for m in enum_cls:
        if str(value).strip().lower() in (m.value.lower(), m.name.lower()):
            return m
    raise ValueError(f"Invalid {enum_cls.__name__}: {value!r}")


def _get_or_create(session: Session, model, name_attr: str, name: str | None, **defaults):
    if not name:
        return None
    obj = session.scalar(select(model).where(getattr(model, name_attr) == name))
    if obj is None:
        obj = model(**{name_attr: name}, **defaults)
        session.add(obj)
        session.flush()
    return obj


VARIANTS_FILE = Path(__file__).resolve().parents[2] / "data" / "company_name_variants.csv"


def load_company_variants(path: Path = VARIANTS_FILE) -> dict[str, str]:
    """User-confirmed spelling variants of a promoter name in DoED: CSV `variant,canonical`, both as written in DoED.
    DoED sometimes spells one promoter two ways ('Sanjen Jalvidyut Co.' / 'Sanjen Jalvidhyut Co'); this keeps them one company."""
    if not path.exists():
        return {}
    import csv
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {company_key(r["variant"]): company_key(r["canonical"]) for r in csv.DictReader(f) if r.get("variant") and r.get("canonical")}


def get_or_create_company(session: Session, name: str | None) -> Company | None:
    """Find a company by normalised name (so 'Himal Power Ltd.' == 'Himal Power Limited') or create it."""
    if not name:
        return None
    def flat(text: str) -> str:  # 'Nilgirikhola' == 'Nilgiri Khola': spacing differs between sources
        return company_key(text).replace(" ", "")
    index = session.info.get("company_index")
    if index is None:
        index = {flat(c.company_name): c for c in session.scalars(select(Company))}
        session.info["company_index"] = index
    variants = session.info.setdefault("company_variants", load_company_variants())
    key = company_key(name)
    key = flat(variants.get(key, key))
    company = index.get(key)
    if company is None:
        ctype = CompanyType.NEA if name == NEA_NAME else CompanyType.IPP
        company = Company(company_name=name[:200], company_type=ctype)
        session.add(company)
        session.flush()
        index[key] = company
    return company


def resolve_relations(session: Session, rec: dict) -> dict:
    """Turn friendly names (district/river/developer/owner) in a record into foreign-key ids."""
    if rec.get("district"):
        rec["district_id"] = _get_or_create(session, District, "district_name", rec["district"]).district_id
    if rec.get("river"):
        rec["river_id"] = _get_or_create(session, River, "river_name", rec["river"]).river_id
    for key, fk in (("developer", "developer_company_id"), ("owner", "owner_company_id")):
        if rec.get(key):
            rec[fk] = get_or_create_company(session, rec[key]).company_id
    return rec


def upsert_project(session: Session, record: dict, default_reliability: str = "manual") -> tuple[Project, bool]:
    """Insert or update a project from a record dict. Returns (project, created)."""
    rec = {k: _clean(v) for k, v in record.items()}
    pid = rec.get("project_id")
    if not pid:
        raise ValueError("record is missing project_id")

    for key, enum_cls in _ENUMS.items():
        if key in rec:
            rec[key] = _to_enum(enum_cls, rec[key])

    resolve_relations(session, rec)

    for col in Project.__table__.columns:
        v = rec.get(col.name)
        if v is None:
            continue
        if isinstance(col.type, Date) and not isinstance(v, date):
            rec[col.name] = pd.to_datetime(v).date()
        elif isinstance(col.type, Boolean) and not isinstance(v, bool):
            rec[col.name] = str(v).strip().lower() in ("1", "true", "yes", "y")

    values = {k: v for k, v in rec.items() if k in PROJECT_COLUMNS and v is not None}
    values.setdefault("data_reliability", default_reliability)

    project = session.get(Project, pid)
    created = project is None
    if created:
        values.setdefault("project_name_en", pid)
        project = Project(**values)
        session.add(project)
    else:
        for k, v in values.items():
            setattr(project, k, v)
    return project, created


def upsert_projects(session: Session, records: Iterable[dict], default_reliability: str = "manual") -> dict:
    result = {"created": 0, "updated": 0, "errors": []}
    for i, rec in enumerate(records, 1):
        try:
            with session.begin_nested():
                _, created = upsert_project(session, rec, default_reliability)
            result["created" if created else "updated"] += 1
        except Exception as exc:  # keep going; report per-row problems
            result["errors"].append(f"row {i} ({rec.get('project_id')}): {exc}")
    return result


def import_projects_csv(session: Session, path: str | Path) -> dict:
    df = pd.read_csv(path, dtype=object)
    df.columns = [c.strip() for c in df.columns]
    numeric = [c.name for c in Project.__table__.columns
               if str(c.type) in ("FLOAT", "INTEGER") and c.name in df.columns]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return upsert_projects(session, df.to_dict("records"), default_reliability="csv-import")


def import_financials_csv(session: Session, path: str | Path) -> dict:
    """CSV columns: project_id, fiscal_year, energy_generated_gwh, revenue_npr, operating_cost_npr,
    net_profit_npr, capacity_factor_percentage, irr_percentage, source."""
    df = pd.read_csv(path, dtype={"project_id": str, "fiscal_year": str})
    result = {"created": 0, "updated": 0, "errors": []}
    fin_cols = {c.name for c in ProjectFinancial.__table__.columns} - {"financial_id"}
    for i, row in enumerate(df.to_dict("records"), 1):
        row = {k: _clean(v) for k, v in row.items() if k in fin_cols}
        try:
            if session.get(Project, row["project_id"]) is None:
                raise ValueError("unknown project_id")
            existing = session.scalar(select(ProjectFinancial).where(
                ProjectFinancial.project_id == row["project_id"],
                ProjectFinancial.fiscal_year == row["fiscal_year"]))
            if existing:
                for k, v in row.items():
                    if v is not None:
                        setattr(existing, k, v)
                result["updated"] += 1
            else:
                session.add(ProjectFinancial(**row))
                result["created"] += 1
        except Exception as exc:
            result["errors"].append(f"row {i}: {exc}")
    return result


def import_shareholders_csv(session: Session, path: str | Path) -> dict:
    """CSV columns: project_id, shareholder_name, shareholder_type, stock_symbol, stake_percentage.

    No public source publishes shareholder structure, so this is the intended way to add it
    (from company annual reports, prospectuses, SEBON filings).
    """
    from sqlalchemy import insert, update

    from ..models import Shareholder, ShareholderType, project_shareholder
    df = pd.read_csv(path, dtype={"project_id": str})
    result = {"created": 0, "updated": 0, "errors": []}
    for i, row in enumerate(df.to_dict("records"), 1):
        row = {k: _clean(v) for k, v in row.items()}
        try:
            if session.get(Project, row["project_id"]) is None:
                raise ValueError("unknown project_id")
            if not row.get("shareholder_name"):
                raise ValueError("missing shareholder_name")
            holder = session.scalar(select(Shareholder).where(Shareholder.shareholder_name == row["shareholder_name"]))
            if holder is None:
                stype = _to_enum(ShareholderType, row.get("shareholder_type"))
                holder = Shareholder(shareholder_name=row["shareholder_name"], shareholder_type=stype,
                                     stock_symbol=row.get("stock_symbol"))
                session.add(holder)
                session.flush()
            stake = float(row["stake_percentage"]) if row.get("stake_percentage") is not None else None
            link = (project_shareholder.c.project_id == row["project_id"]) & \
                   (project_shareholder.c.shareholder_id == holder.shareholder_id)
            if session.execute(select(project_shareholder).where(link)).first():
                session.execute(update(project_shareholder).where(link).values(stake_percentage=stake))
                result["updated"] += 1
            else:
                session.execute(insert(project_shareholder).values(
                    project_id=row["project_id"], shareholder_id=holder.shareholder_id, stake_percentage=stake))
                result["created"] += 1
        except Exception as exc:
            result["errors"].append(f"row {i}: {exc}")
    return result


def import_facts_csv(session: Session, path: str | Path) -> dict:
    """Verified company/project facts: CSV columns `symbol, project_id, fact_type, value_num, value_text, unit,
    fiscal_year, source`. Use this for numbers you have confirmed from a report (project cost, PPA rate, tax status).

    fact_type examples: project_cost_npr, ppa_rate_npr_kwh, tax_holiday (value_text e.g. "100% until 2085"),
    generation_gwh. Stored as verified manual facts; they take precedence in profiles over auto-extracted ones only by
    being marked verified, so keep `source` descriptive (report name + page).
    """
    from ..models import CompanyFact
    df = pd.read_csv(path, dtype={"symbol": str, "project_id": str, "fiscal_year": str})
    result = {"created": 0, "updated": 0, "errors": []}
    for i, row in enumerate(df.to_dict("records"), 1):
        row = {k: _clean(v) for k, v in row.items()}
        try:
            company = session.scalar(select(Company).where(Company.stock_symbol == (row.get("symbol") or "").upper()))
            if company is None:
                raise ValueError(f"unknown NEPSE symbol {row.get('symbol')!r}")
            if not row.get("fact_type"):
                raise ValueError("missing fact_type")
            if row.get("value_num") is None and not row.get("value_text"):
                raise ValueError("needs value_num or value_text")
            if row.get("project_id") and session.get(Project, row["project_id"]) is None:
                raise ValueError("unknown project_id")
            existing = session.scalar(select(CompanyFact).where(
                CompanyFact.company_id == company.company_id, CompanyFact.fact_type == row["fact_type"],
                CompanyFact.project_id == row.get("project_id"), CompanyFact.fiscal_year == row.get("fiscal_year"),
                CompanyFact.method == "manual"))
            fact = existing or CompanyFact(company_id=company.company_id, method="manual")
            fact.project_id, fact.fact_type = row.get("project_id"), row["fact_type"]
            fact.value_num = float(row["value_num"]) if row.get("value_num") is not None else None
            fact.value_text, fact.unit, fact.fiscal_year = row.get("value_text"), row.get("unit"), row.get("fiscal_year")
            fact.snippet, fact.verified = (row.get("source") or "")[:500], True
            if existing is None:
                session.add(fact)
            result["updated" if existing else "created"] += 1
        except Exception as exc:
            result["errors"].append(f"row {i}: {exc}")
    return result


def import_company_financials_csv(session: Session, path: str | Path) -> dict:
    """Historical company financials for trend/ratio tables, e.g. copied from annual reports.

    CSV columns: symbol, fiscal_year (BS, e.g. 2080/81), quarter (1-4; use 4 for full-year figures), and any of
    paid_up_capital_npr, reserves_npr, loans_npr, electricity_sales_npr, operating_income_npr, finance_cost_npr,
    tax_provision_npr, net_profit_npr, eps, networth_per_share, roe_pct, roa_pct, source. Amounts are in NPR (not '000).
    Values you supply overwrite stored ones for that period; blank cells leave existing values alone.
    """
    from ..models import CompanyFinancial
    df = pd.read_csv(path, dtype={"symbol": str, "fiscal_year": str})
    numeric = {c.name for c in CompanyFinancial.__table__.columns if str(c.type) == "FLOAT"}
    result = {"created": 0, "updated": 0, "errors": []}
    for i, row in enumerate(df.to_dict("records"), 1):
        row = {k: _clean(v) for k, v in row.items()}
        try:
            company = session.scalar(select(Company).where(Company.stock_symbol == (row.get("symbol") or "").upper()))
            if company is None:
                raise ValueError(f"unknown NEPSE symbol {row.get('symbol')!r}")
            fy, quarter = row.get("fiscal_year"), row.get("quarter")
            if not fy or not re.fullmatch(r"20\d\d/\d\d", fy):
                raise ValueError("fiscal_year must look like 2080/81 (Bikram Sambat)")
            if quarter is None or int(quarter) not in (1, 2, 3, 4):
                raise ValueError("quarter must be 1-4")
            existing = session.scalar(select(CompanyFinancial).where(
                CompanyFinancial.company_id == company.company_id, CompanyFinancial.fiscal_year == fy,
                CompanyFinancial.quarter == int(quarter)))
            rec = existing or CompanyFinancial(company_id=company.company_id, fiscal_year=fy, quarter=int(quarter))
            for k, v in row.items():
                if k in numeric and v is not None:
                    setattr(rec, k, float(v))
            rec.source = (row.get("source") or "manual import")[:80]
            if existing is None:
                session.add(rec)
            result["updated" if existing else "created"] += 1
        except Exception as exc:
            result["errors"].append(f"row {i}: {exc}")
    return result
