"""SQLAlchemy ORM models for the Nepal Hydropower database."""
from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Table, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class ProjectStatus(enum.Enum):
    OPERATIONAL = "Operational"
    UNDER_CONSTRUCTION = "Under Construction"
    LICENSED = "Licensed"  # generation licence issued; construction start not confirmed
    PLANNED = "Planned"  # survey licence / proposal stage
    SUSPENDED = "Suspended"
    DECOMMISSIONED = "Decommissioned"


class ProjectType(enum.Enum):
    RUN_OF_RIVER = "Run-of-River"
    PEAKING_ROR = "Peaking Run-of-River"
    RESERVOIR = "Reservoir"
    STORAGE = "Storage"


class TurbineType(enum.Enum):
    PELTON = "Pelton"
    FRANCIS = "Francis"
    KAPLAN = "Kaplan"
    TURGO = "Turgo"
    CROSSFLOW = "Crossflow"


class CompanyType(enum.Enum):
    NEA = "NEA"
    IPP = "IPP"
    PRIVATE = "Private"
    GOVERNMENT = "Government"
    JOINT_VENTURE = "Joint Venture"


class UpdateType(enum.Enum):
    NEWS = "News"
    MILESTONE = "Milestone"
    REGULATORY = "Regulatory"
    FINANCIAL = "Financial"


class ShareholderType(enum.Enum):
    INDIVIDUAL = "Individual"
    COMPANY = "Company"
    FUND = "Fund"
    GOVERNMENT = "Government"
    PUBLIC = "Public"


project_shareholder = Table(
    "project_shareholder", Base.metadata,
    Column("project_id", String(20), ForeignKey("projects.project_id"), primary_key=True),
    Column("shareholder_id", Integer, ForeignKey("shareholders.shareholder_id"), primary_key=True),
    Column("stake_percentage", Float),
)


class District(Base):
    __tablename__ = "districts"
    district_id: Mapped[int] = mapped_column(primary_key=True)
    district_name: Mapped[str] = mapped_column(String(80), unique=True)
    province: Mapped[Optional[str]] = mapped_column(String(80))
    projects: Mapped[List["Project"]] = relationship(back_populates="district")


class River(Base):
    __tablename__ = "rivers"
    river_id: Mapped[int] = mapped_column(primary_key=True)
    river_name: Mapped[str] = mapped_column(String(80), unique=True)
    basin: Mapped[Optional[str]] = mapped_column(String(80))
    projects: Mapped[List["Project"]] = relationship(back_populates="river")


class Company(Base):
    __tablename__ = "companies"
    company_id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(200), unique=True)
    company_type: Mapped[Optional[CompanyType]] = mapped_column(Enum(CompanyType))
    registration_number: Mapped[Optional[str]] = mapped_column(String(60))
    established_date: Mapped[Optional[date]] = mapped_column(Date)
    headquarters: Mapped[Optional[str]] = mapped_column(String(120))
    website: Mapped[Optional[str]] = mapped_column(String(200))
    nepse_listed: Mapped[bool] = mapped_column(Boolean, default=False)
    stock_symbol: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    total_capacity_developed_mw: Mapped[Optional[float]] = mapped_column(Float)
    listed_shares: Mapped[Optional[int]] = mapped_column(Integer)
    paidup_value: Mapped[Optional[float]] = mapped_column(Float)
    listed_name: Mapped[Optional[str]] = mapped_column(String(200))  # NEPSE name; company_name stays DoED's promoter name
    sharesansar_id: Mapped[Optional[int]] = mapped_column(Integer)
    email: Mapped[Optional[str]] = mapped_column(String(200))
    address: Mapped[Optional[str]] = mapped_column(String(300))
    website_source: Mapped[Optional[str]] = mapped_column(String(40))  # sharesansar / email-domain / manual

    developed_projects: Mapped[List["Project"]] = relationship(
        back_populates="developer", foreign_keys="Project.developer_company_id")
    owned_projects: Mapped[List["Project"]] = relationship(
        back_populates="owner", foreign_keys="Project.owner_company_id")
    updates: Mapped[List["ProjectUpdate"]] = relationship(back_populates="company")


class Shareholder(Base):
    __tablename__ = "shareholders"
    shareholder_id: Mapped[int] = mapped_column(primary_key=True)
    shareholder_name: Mapped[str] = mapped_column(String(200), unique=True)
    shareholder_type: Mapped[Optional[ShareholderType]] = mapped_column(Enum(ShareholderType))
    stock_symbol: Mapped[Optional[str]] = mapped_column(String(20))
    projects: Mapped[List["Project"]] = relationship(
        secondary=project_shareholder, back_populates="shareholders")


class Project(Base):
    __tablename__ = "projects"

    # Identification
    project_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    project_name_en: Mapped[str] = mapped_column(String(200), index=True)
    project_name_np: Mapped[Optional[str]] = mapped_column(String(200))
    acronym: Mapped[Optional[str]] = mapped_column(String(30))

    # Location
    district_id: Mapped[Optional[int]] = mapped_column(ForeignKey("districts.district_id"))
    river_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rivers.river_id"))
    province: Mapped[Optional[str]] = mapped_column(String(80))
    municipality: Mapped[Optional[str]] = mapped_column(String(120))
    latitude: Mapped[Optional[float]] = mapped_column(Float)
    longitude: Mapped[Optional[float]] = mapped_column(Float)
    altitude_m: Mapped[Optional[float]] = mapped_column(Float)

    # Technical
    capacity_mw: Mapped[Optional[float]] = mapped_column(Float, index=True)
    project_type: Mapped[Optional[ProjectType]] = mapped_column(Enum(ProjectType))
    turbine_type: Mapped[Optional[TurbineType]] = mapped_column(Enum(TurbineType))
    turbine_count: Mapped[Optional[int]] = mapped_column(Integer)
    design_head_m: Mapped[Optional[float]] = mapped_column(Float)
    design_discharge_m3s: Mapped[Optional[float]] = mapped_column(Float)
    tunnel_length_m: Mapped[Optional[float]] = mapped_column(Float)
    penstock_length_m: Mapped[Optional[float]] = mapped_column(Float)
    annual_energy_generation_gwh: Mapped[Optional[float]] = mapped_column(Float)

    # Financial
    estimated_project_cost_usd: Mapped[Optional[float]] = mapped_column(Float)
    estimated_project_cost_npr: Mapped[Optional[float]] = mapped_column(Float)
    ppa_rate_npr_per_kwh: Mapped[Optional[float]] = mapped_column(Float)
    debt_percentage: Mapped[Optional[float]] = mapped_column(Float)
    equity_percentage: Mapped[Optional[float]] = mapped_column(Float)

    # Development
    status: Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus), default=ProjectStatus.PLANNED, index=True)
    construction_start_date: Mapped[Optional[date]] = mapped_column(Date)
    commissioning_date: Mapped[Optional[date]] = mapped_column(Date)
    commissioning_year: Mapped[Optional[int]] = mapped_column(Integer)  # when only the year is known
    expected_completion_date: Mapped[Optional[date]] = mapped_column(Date)
    expected_completion_year: Mapped[Optional[int]] = mapped_column(Integer)
    license_type: Mapped[Optional[str]] = mapped_column(String(20))  # Survey / Generation
    license_number: Mapped[Optional[str]] = mapped_column(String(60))
    license_issue_date: Mapped[Optional[date]] = mapped_column(Date)
    license_expiry_date: Mapped[Optional[date]] = mapped_column(Date)
    river_name_raw: Mapped[Optional[str]] = mapped_column(String(120))  # river as reported by the source
    location_raw: Mapped[Optional[str]] = mapped_column(String(300))  # VDC/district text as reported

    # Ownership
    developer_company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.company_id"))
    owner_company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.company_id"))

    # Grid
    transmission_voltage_kv: Mapped[Optional[float]] = mapped_column(Float)
    transmission_length_km: Mapped[Optional[float]] = mapped_column(Float)
    substation_name: Mapped[Optional[str]] = mapped_column(String(120))
    grid_connected: Mapped[Optional[bool]] = mapped_column(Boolean)

    # Regulatory
    environmental_clearance: Mapped[Optional[bool]] = mapped_column(Boolean)
    social_safeguard_status: Mapped[Optional[str]] = mapped_column(String(120))

    # Impact
    jobs_created: Mapped[Optional[int]] = mapped_column(Integer)
    co2_avoided_tonnes_per_year: Mapped[Optional[float]] = mapped_column(Float)
    population_benefited: Mapped[Optional[int]] = mapped_column(Integer)

    # Metadata
    data_source: Mapped[Optional[str]] = mapped_column(String(200))
    data_reliability: Mapped[Optional[str]] = mapped_column(String(20))  # verified / seed / scraped
    notes: Mapped[Optional[str]] = mapped_column(Text)
    inference_note: Mapped[Optional[str]] = mapped_column(String(500))  # how inferred (not source-reported) values were obtained
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    district: Mapped[Optional[District]] = relationship(back_populates="projects")
    river: Mapped[Optional[River]] = relationship(back_populates="projects")
    developer: Mapped[Optional[Company]] = relationship(
        back_populates="developed_projects", foreign_keys=[developer_company_id])
    owner: Mapped[Optional[Company]] = relationship(
        back_populates="owned_projects", foreign_keys=[owner_company_id])
    shareholders: Mapped[List[Shareholder]] = relationship(
        secondary=project_shareholder, back_populates="projects")
    financials: Mapped[List["ProjectFinancial"]] = relationship(
        back_populates="project", cascade="all, delete-orphan")
    updates: Mapped[List["ProjectUpdate"]] = relationship(
        back_populates="project", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Project {self.project_id} {self.project_name_en} {self.capacity_mw} MW>"


class ProjectFinancial(Base):
    __tablename__ = "project_financials"
    __table_args__ = (UniqueConstraint("project_id", "fiscal_year"),)

    financial_id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), index=True)
    fiscal_year: Mapped[str] = mapped_column(String(10))  # e.g. "2080/81"
    energy_generated_gwh: Mapped[Optional[float]] = mapped_column(Float)
    revenue_npr: Mapped[Optional[float]] = mapped_column(Float)
    operating_cost_npr: Mapped[Optional[float]] = mapped_column(Float)
    net_profit_npr: Mapped[Optional[float]] = mapped_column(Float)
    capacity_factor_percentage: Mapped[Optional[float]] = mapped_column(Float)
    irr_percentage: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[Optional[str]] = mapped_column(String(200))

    project: Mapped[Project] = relationship(back_populates="financials")


class ProjectUpdate(Base):
    """A news/milestone/regulatory item. Linked to a project, a company, or both — a market item about a
    NEPSE-listed company (e.g. a dividend announcement) may name no specific project, and a project item
    (e.g. a tunnel breakthrough) is attributed to its developer/owner company automatically, not stored twice."""
    __tablename__ = "project_updates"

    update_id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("projects.project_id"), index=True)
    company_id: Mapped[Optional[int]] = mapped_column(ForeignKey("companies.company_id"), index=True)
    update_date: Mapped[date] = mapped_column(Date, default=date.today)
    update_type: Mapped[UpdateType] = mapped_column(Enum(UpdateType), default=UpdateType.NEWS)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(String(500))
    source_name: Mapped[Optional[str]] = mapped_column(String(100))

    project: Mapped[Optional[Project]] = relationship(back_populates="updates")
    company: Mapped[Optional[Company]] = relationship(back_populates="updates")


class Certification(Base):
    __tablename__ = "certifications"
    certification_id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id"), index=True)
    certification_name: Mapped[str] = mapped_column(String(120))  # HSAP, IFC PS, ...
    score: Mapped[Optional[str]] = mapped_column(String(40))
    issued_date: Mapped[Optional[date]] = mapped_column(Date)


class CompanyFinancial(Base):
    """Reported company financials for one period (a quarter's year-to-date figures), amounts in NPR."""
    __tablename__ = "company_financials"
    __table_args__ = (UniqueConstraint("company_id", "fiscal_year", "quarter"),)

    financial_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), index=True)
    fiscal_year: Mapped[str] = mapped_column(String(10))  # BS fiscal year, e.g. "2082/83"
    quarter: Mapped[int] = mapped_column(Integer)  # 1-4; quarter 4 = full-year figures
    paid_up_capital_npr: Mapped[Optional[float]] = mapped_column(Float)
    reserves_npr: Mapped[Optional[float]] = mapped_column(Float)
    loans_npr: Mapped[Optional[float]] = mapped_column(Float)  # "Loans & long-term liabilities"
    ppe_npr: Mapped[Optional[float]] = mapped_column(Float)
    cwip_npr: Mapped[Optional[float]] = mapped_column(Float)
    investments_npr: Mapped[Optional[float]] = mapped_column(Float)
    current_liabilities_npr: Mapped[Optional[float]] = mapped_column(Float)
    operating_income_npr: Mapped[Optional[float]] = mapped_column(Float)
    electricity_sales_npr: Mapped[Optional[float]] = mapped_column(Float)
    finance_cost_npr: Mapped[Optional[float]] = mapped_column(Float)  # "Financial expenses", year to date
    depreciation_npr: Mapped[Optional[float]] = mapped_column(Float)
    tax_provision_npr: Mapped[Optional[float]] = mapped_column(Float)
    net_profit_npr: Mapped[Optional[float]] = mapped_column(Float)
    eps: Mapped[Optional[float]] = mapped_column(Float)
    networth_per_share: Mapped[Optional[float]] = mapped_column(Float)
    roe_pct: Mapped[Optional[float]] = mapped_column(Float)
    roa_pct: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[Optional[str]] = mapped_column(String(80))
    source_url: Mapped[Optional[str]] = mapped_column(String(300))
    raw_json: Mapped[Optional[str]] = mapped_column(Text)  # every label/value as published
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CompanyReport(Base):
    """A report file (or link) found for a company. Files are stored on disk; the path and hash are recorded."""
    __tablename__ = "company_reports"
    __table_args__ = (UniqueConstraint("company_id", "source_url"),)

    report_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # annual / quarterly
    fiscal_year: Mapped[Optional[str]] = mapped_column(String(10))
    quarter: Mapped[Optional[int]] = mapped_column(Integer)
    title: Mapped[Optional[str]] = mapped_column(String(300))
    source_site: Mapped[Optional[str]] = mapped_column(String(80))  # company website / sharesansar / merolagani
    source_url: Mapped[str] = mapped_column(String(500))
    local_path: Mapped[Optional[str]] = mapped_column(String(400))
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="found")  # found / downloaded / failed / extracted
    note: Mapped[Optional[str]] = mapped_column(String(300))
    found_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CompanyFact(Base):
    """A single fact (project cost, PPA rate, tax holiday...) with where it came from. Auditable by design."""
    __tablename__ = "company_facts"

    fact_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), index=True)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("projects.project_id"), index=True)
    fact_type: Mapped[str] = mapped_column(String(30), index=True)  # project_cost_npr, ppa_rate_npr_kwh, tax_holiday...
    value_num: Mapped[Optional[float]] = mapped_column(Float)
    value_text: Mapped[Optional[str]] = mapped_column(String(200))
    unit: Mapped[Optional[str]] = mapped_column(String(30))
    fiscal_year: Mapped[Optional[str]] = mapped_column(String(10))
    report_id: Mapped[Optional[int]] = mapped_column(ForeignKey("company_reports.report_id"))
    page: Mapped[Optional[int]] = mapped_column(Integer)
    snippet: Mapped[Optional[str]] = mapped_column(String(500))  # the exact text the value was read from
    method: Mapped[str] = mapped_column(String(20), default="auto")  # auto (regex on PDF) / manual
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    extracted_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
