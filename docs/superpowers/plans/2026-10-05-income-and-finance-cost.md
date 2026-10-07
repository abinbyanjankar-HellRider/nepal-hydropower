# Income Forecast and Finance-Cost Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an "Income and finance" dashboard page that shows wet, dry and average capacity usage per NEPSE-listed hydropower company, a calibrated four-quarter income forecast with a company ranking, and a finance-cost analysis with a low-interest-rate scenario (effect on net profit, EPS and book value per share).

**Architecture:** Two new analytics modules (`income.py`, `finance_cost.py`) compute from the database on every request. Two JSON endpoints and one page expose them. Nothing is stored. Pure functions hold the maths so they can be unit-tested without a database; thin DB functions load rows.

**Tech Stack:** Python 3.14, SQLAlchemy 2 (ORM, `Mapped` models), Flask, pytest, Chart.js (already vendored), existing `app.js` helpers (`api`, `el`, `renderTable`, `npr`, `fmt`).

**Spec:** `docs/superpowers/specs/2026-10-05-income-and-finance-cost-design.md`

## Global Constraints

- Season calendar: wet = mid-Apr to mid-Dec = 245 days; dry = mid-Dec to mid-Apr = 120 days; fiscal quarters from Shrawan: Q1 92 wet days, Q2 60 wet + 29 dry, Q3 91 dry, Q4 93 wet. Total 365.
- Annual capacity factor 0.55 and dry-season energy share 0.30 come from `config.ASSUMED_CAPACITY_FACTOR` and `config.ASSUMED_DRY_SEASON_ENERGY_SHARE`; fallback tariffs 4.80 / 8.40 from `config.ASSUMED_WET_TARIFF_NPR_PER_KWH` / `ASSUMED_DRY_TARIFF_NPR_PER_KWH`.
- Average usage = day-weighted: (245 x wet + 120 x dry) / 365 = total energy / (MW x 8760 h). Never a plain mean of the two percentages.
- PPA rate resolution order: project-level verified fact, company-level verified fact, config fallback (flag `assumed_rate`). Only facts with `verified = True` count.
- Calibration: one annual factor per company = reported FY electricity sales / modelled income, clipped to 0.3 - 1.5 (flag `calibration_clipped`). No reported sales: factor 1.0, flag `no_reported_sales`. Any operating plant commissioned in or after the reported fiscal year's starting calendar year: factor 1.0, flag `partial_year`. Unknown commissioning year counts as a full-year plant.
- Forecast horizon: the four fiscal quarters of the year after the latest reported full year (FY 2082/83 reported, so FY 2083/84).
- Finance defaults: rate change -2 pp, repayment 8% of the current loan per year, retention 70%, horizon 5 years. Ranges: rate_delta_pp -10..10, repay_pct 0..50, retention_pct 0..100, years 1..10; out of range or non-numeric (including NaN/inf) returns HTTP 400 with `{"error": ...}`.
- Shares for EPS = paid-up capital / 100. Implied rate = finance cost / year-end loans; flagged outside 2 - 20%.
- Plants under construction are out of scope. Loans and finance cost are allocated to operating plants by MW share and labelled "allocated, not reported".
- All figures are estimates; the page says so. Match the existing code style (type hints, short docstrings, `from __future__ import annotations`).
- Run the whole suite with `python -m pytest -q`; it must stay green (92 passed before this work).

## Review Focus

- Empty database (no companies, no financials): forecast and impact return empty company lists, the API returns 200, the page shows an empty message instead of crashing.
- Operating plant with missing or zero capacity: excluded from the forecast rather than producing NaN or a divide-by-zero.
- Company with loans but zero finance cost (interest capitalised, e.g. CHCL): position is shown with flags, the scenario is `None`, the page shows a dash, not a made-up rate.
- Query parameters `nan`, `inf`, `abc`, and out-of-range numbers: HTTP 400 JSON error, never a 500.
- Loss-making company (net profit plus tax is not positive): effective tax rate 0 with flag `loss_making`; a rate cut still adds to profit without a negative tax charge.

## File Structure

| File | Responsibility |
|---|---|
| `src/analytics/income.py` (create) | calendar, seasonal factors, rate resolution, plant loading, calibration, forecast, ranking |
| `src/analytics/finance_cost.py` (create) | position, scenario maths, plant allocation, `impact()` |
| `src/dashboard.py` (modify) | `float_arg` helper, `/income` page, `/api/income/forecast`, `/api/finance/impact` |
| `dashboards/templates/base.html` (modify) | sidebar entry |
| `dashboards/templates/income.html` (create) | page markup and script |
| `tests/conftest.py` (modify) | shared `income_world` fixture |
| `tests/test_income.py` (create) | income module tests |
| `tests/test_finance_cost.py` (create) | finance module tests |
| `tests/test_income_api.py` (create) | endpoints and page tests |
| `README.md` (modify) | short section describing the page |

---

### Task 1: Season calendar, seasonal factors and rate resolution

**Files:**
- Create: `src/analytics/income.py`
- Create: `tests/test_income.py`

**Interfaces:**
- Produces: `QUARTERS`, `WET_DAYS`, `DRY_DAYS`, `Rate(wet, dry, basis)`, `seasonal_factors(capacity_factor=None, dry_share=None) -> (wet_cf, dry_cf)`, `plant_quarter_energy_gwh(mw, wet_cf, dry_cf) -> list[(wet_gwh, dry_gwh)]` (4 items), `load_ppa_rates(session) -> dict[(company_id, project_id | None), (wet, dry)]`, `resolve_rate(rates, company_id, project_id) -> Rate`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_income.py`:

```python
import pytest

from src import config
from src.analytics import income as inc


def test_season_calendar_adds_up():
    assert sum(w + d for _, w, d in inc.QUARTERS) == 365
    assert inc.WET_DAYS == 245 and inc.DRY_DAYS == 120


def test_seasonal_factors_reproduce_the_annual_capacity_factor():
    wet, dry = inc.seasonal_factors(0.55, 0.30)
    assert wet == pytest.approx(0.5736, abs=1e-3)
    assert dry == pytest.approx(0.5019, abs=1e-3)
    assert (inc.WET_DAYS * wet + inc.DRY_DAYS * dry) / 365 == pytest.approx(0.55)


def test_seasonal_factors_default_to_config():
    assert inc.seasonal_factors() == inc.seasonal_factors(
        config.ASSUMED_CAPACITY_FACTOR, config.ASSUMED_DRY_SEASON_ENERGY_SHARE)


def test_plant_energy_sums_to_the_annual_estimate_and_follows_the_calendar():
    wet, dry = inc.seasonal_factors(0.55, 0.30)
    q = inc.plant_quarter_energy_gwh(10, wet, dry)
    assert len(q) == 4
    assert sum(w + d for w, d in q) == pytest.approx(10 * 8760 * 0.55 / 1000)
    assert q[0][1] == 0 and q[3][1] == 0      # Q1 and Q4 are all wet
    assert q[2][0] == 0                       # Q3 is all dry
    assert q[1][0] > 0 and q[1][1] > 0        # Q2 straddles mid-December


def test_resolve_rate_prefers_project_then_company_then_assumed():
    rates = {(1, "HP_1"): (4.0, 7.0), (1, None): (4.8, 8.4)}
    assert inc.resolve_rate(rates, 1, "HP_1") == inc.Rate(4.0, 7.0, "project")
    assert inc.resolve_rate(rates, 1, "HP_9") == inc.Rate(4.8, 8.4, "company")
    assumed = inc.resolve_rate(rates, 2, "HP_5")
    assert assumed == inc.Rate(config.ASSUMED_WET_TARIFF_NPR_PER_KWH, config.ASSUMED_DRY_TARIFF_NPR_PER_KWH, "assumed")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_income.py -q`
Expected: collection error, `cannot import name 'income'` (module does not exist yet).

- [ ] **Step 3: Write the minimal implementation**

Create `src/analytics/income.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_income.py -q`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/analytics/income.py tests/test_income.py
git commit -m "feat: season calendar, seasonal factors and PPA rate resolution"
```

---

### Task 2: Plant loading, calibration, forecast and ranking

**Files:**
- Modify: `src/analytics/income.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_income.py`
- Modify: `docs/superpowers/specs/2026-10-05-income-and-finance-cost-design.md` (calibration wording)

**Interfaces:**
- Consumes: Task 1 (`QUARTERS`, `WET_DAYS`, `DRY_DAYS`, `seasonal_factors`, `plant_quarter_energy_gwh`, `load_ppa_rates`, `resolve_rate`).
- Produces: `operating_plants(session) -> list[dict]` (keys `company_id, symbol, name, project_id, plant, mw, commissioning_year`), `fiscal_year_start_ad(fy: str) -> int`, `next_fiscal_year(fy: str) -> str`, `calibration(reported_sales, modelled_income, partial_year) -> dict(factor, raw_factor, basis, flags)`, `rank_companies(rows) -> list[dict]`, `shape_gap_pp(cumulative, model_income) -> float | None`, `forecast(session, capacity_factor=None, dry_share=None) -> dict(assumptions, quarters, totals, companies)`. Each company dict has: `company_id, symbol, name, capacity_mw, plants, wet_usage_pct, dry_usage_pct, avg_usage_pct, wet_energy_gwh, dry_energy_gwh, quarters (list of {label, wet_gwh, dry_gwh, income_npr}), annual_income_npr, rank, rate_basis, calibration (factor, raw_factor, basis), flags, profit_shape_gap_pp`.
- Produces for tests: fixture `income_world(db)` returning `{"alpha": id, "beta": id, "unlisted": id}`.

- [ ] **Step 1: Add the shared fixture**

Append to `tests/conftest.py`:

```python
@pytest.fixture()
def income_world(db):
    """Two listed companies with operating plants, PPA facts and one reported year, plus noise rows that must be ignored."""
    from src.models import Company, CompanyFact, CompanyFinancial, Project, ProjectStatus
    ids = {}
    with db.session_scope() as s:
        alpha = Company(company_name="Alpha Power Ltd", nepse_listed=True, stock_symbol="ALPHA", listed_name="Alpha Power Limited")
        beta = Company(company_name="Beta Hydro Ltd", nepse_listed=True, stock_symbol="BETA")
        unlisted = Company(company_name="Unlisted Co", nepse_listed=False)
        s.add_all([alpha, beta, unlisted])
        s.flush()
        ids = {"alpha": alpha.company_id, "beta": beta.company_id, "unlisted": unlisted.company_id}
        op = ProjectStatus.OPERATIONAL
        s.add_all([
            Project(project_id="HP_1", project_name_en="Alpha One", capacity_mw=10, status=op, commissioning_year=2020,
                    developer_company_id=alpha.company_id),
            Project(project_id="HP_2", project_name_en="Alpha Two", capacity_mw=5, status=op, commissioning_year=2021,
                    owner_company_id=alpha.company_id),
            Project(project_id="HP_3", project_name_en="Beta One", capacity_mw=20, status=op, commissioning_year=2019,
                    developer_company_id=beta.company_id),
            Project(project_id="HP_4", project_name_en="Unlisted One", capacity_mw=50, status=op,
                    developer_company_id=unlisted.company_id),
            Project(project_id="HP_5", project_name_en="Alpha Building", capacity_mw=30,
                    status=ProjectStatus.UNDER_CONSTRUCTION, developer_company_id=alpha.company_id),
            Project(project_id="HP_6", project_name_en="Beta No Capacity", capacity_mw=None, status=op,
                    developer_company_id=beta.company_id),
        ])
        for company_id, project_id, wet, dry, verified in [
            (alpha.company_id, "HP_1", 4.0, 7.0, True),   # project-level
            (alpha.company_id, None, 4.8, 8.4, True),     # company-level
            (beta.company_id, None, 1.0, 2.0, False),     # unverified: must be ignored
        ]:
            s.add(CompanyFact(company_id=company_id, project_id=project_id, fact_type="ppa_wet_npr_kwh",
                              value_num=wet, verified=verified))
            s.add(CompanyFact(company_id=company_id, project_id=project_id, fact_type="ppa_dry_npr_kwh",
                              value_num=dry, verified=verified))
        # Alpha: one reported year; cumulative net profit by quarter; loans and finance cost in the Q4 row
        for quarter, profit in ((1, 40e6), (2, 90e6), (3, 130e6)):
            s.add(CompanyFinancial(company_id=alpha.company_id, fiscal_year="2082/83", quarter=quarter, net_profit_npr=profit))
        s.add(CompanyFinancial(
            company_id=alpha.company_id, fiscal_year="2082/83", quarter=4, paid_up_capital_npr=1_000e6,
            reserves_npr=300e6, loans_npr=1_000e6, finance_cost_npr=100e6, net_profit_npr=200e6, tax_provision_npr=50e6,
            eps=20.0, networth_per_share=130.0, electricity_sales_npr=400e6))
        s.add(CompanyFinancial(
            company_id=beta.company_id, fiscal_year="2082/83", quarter=4, paid_up_capital_npr=500e6, reserves_npr=50e6,
            loans_npr=200e6, finance_cost_npr=30e6, net_profit_npr=40e6, tax_provision_npr=0.0, eps=8.0,
            networth_per_share=110.0, electricity_sales_npr=None))
    return ids
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_income.py`:

```python
from src.models import CompanyFinancial, Project


def test_fiscal_year_helpers():
    assert inc.fiscal_year_start_ad("2082/83") == 2025
    assert inc.next_fiscal_year("2082/83") == "2083/84"
    assert inc.next_fiscal_year("2099/00") == "2100/01"


def test_calibration_cases():
    ok = inc.calibration(reported_sales=90.0, modelled_income=100.0, partial_year=False)
    assert ok["factor"] == pytest.approx(0.9) and ok["basis"] == "reported_sales" and ok["flags"] == []
    high = inc.calibration(1000.0, 100.0, False)
    assert high["factor"] == inc.CALIBRATION_MAX and high["raw_factor"] == pytest.approx(10.0)
    assert "calibration_clipped" in high["flags"]
    low = inc.calibration(1.0, 100.0, False)
    assert low["factor"] == inc.CALIBRATION_MIN and "calibration_clipped" in low["flags"]
    for sales in (None, 0, -5.0):
        none = inc.calibration(sales, 100.0, False)
        assert none["factor"] == 1.0 and none["basis"] == "uncalibrated" and none["flags"] == ["no_reported_sales"]
    partial = inc.calibration(90.0, 100.0, True)
    assert partial["factor"] == 1.0 and partial["flags"] == ["partial_year"]


def test_rank_companies_orders_by_income_then_symbol():
    rows = [{"symbol": "B", "annual_income_npr": 5.0}, {"symbol": "A", "annual_income_npr": 5.0},
            {"symbol": "C", "annual_income_npr": 9.0}]
    ranked = inc.rank_companies(rows)
    assert [r["symbol"] for r in ranked] == ["C", "A", "B"] and [r["rank"] for r in ranked] == [1, 2, 3]


def test_shape_gap_pp():
    assert inc.shape_gap_pp([25, 50, 75, 100], [1, 1, 1, 1]) == 0.0
    assert inc.shape_gap_pp([None, 50, 75, 100], [1, 1, 1, 1]) is None
    assert inc.shape_gap_pp([100, 50, 75, 100], [1, 1, 1, 1]) is None   # cumulative went down
    assert inc.shape_gap_pp([0, 0, 0, 0], [1, 1, 1, 1]) is None
    assert inc.shape_gap_pp([100, 100, 100, 100], [1, 1, 1, 1]) == pytest.approx(37.5)


def _expected_alpha_income():
    wet, dry = inc.seasonal_factors()
    hp1 = 10 * 24 * (245 * wet * 4.0 + 120 * dry * 7.0) * 1e6 / 1000     # project-level rate
    hp2 = 5 * 24 * (245 * wet * 4.8 + 120 * dry * 8.4) * 1e6 / 1000      # company-level rate
    return hp1 + hp2


def test_forecast_scope_and_labels(db, income_world):
    with db.session_scope() as s:
        out = inc.forecast(s)
    symbols = {c["symbol"] for c in out["companies"]}
    assert symbols == {"ALPHA", "BETA"}                       # unlisted, under-construction and no-capacity ignored
    alpha = next(c for c in out["companies"] if c["symbol"] == "ALPHA")
    beta = next(c for c in out["companies"] if c["symbol"] == "BETA")
    assert alpha["capacity_mw"] == 15 and alpha["plants"] == 2 and beta["capacity_mw"] == 20 and beta["plants"] == 1
    assert alpha["name"] == "Alpha Power Limited" and beta["name"] == "Beta Hydro Ltd"
    assert out["quarters"] == ["2083/84 Q1", "2083/84 Q2", "2083/84 Q3", "2083/84 Q4"]
    assert out["assumptions"]["reported_fiscal_year"] == "2082/83"
    assert [c["rank"] for c in out["companies"]] == [1, 2]
    assert out["companies"][0]["annual_income_npr"] >= out["companies"][1]["annual_income_npr"]


def test_forecast_rates_flags_and_unverified_facts(db, income_world):
    with db.session_scope() as s:
        out = inc.forecast(s)
    alpha = next(c for c in out["companies"] if c["symbol"] == "ALPHA")
    beta = next(c for c in out["companies"] if c["symbol"] == "BETA")
    assert alpha["rate_basis"] == "verified" and "assumed_rate" not in alpha["flags"]
    assert beta["rate_basis"] == "assumed" and "assumed_rate" in beta["flags"]   # the unverified 1.0/2.0 was ignored
    assert beta["calibration"]["basis"] == "uncalibrated" and "no_reported_sales" in beta["flags"]


def test_forecast_calibrates_to_reported_sales(db, income_world):
    expected = _expected_alpha_income()
    with db.session_scope() as s:
        row = s.query(CompanyFinancial).filter_by(company_id=income_world["alpha"], quarter=4).one()
        row.electricity_sales_npr = 0.9 * expected
    with db.session_scope() as s:
        alpha = next(c for c in inc.forecast(s)["companies"] if c["symbol"] == "ALPHA")
    assert alpha["calibration"]["factor"] == pytest.approx(0.9)
    assert alpha["calibration"]["basis"] == "reported_sales"
    assert alpha["annual_income_npr"] == pytest.approx(0.9 * expected)
    assert sum(q["income_npr"] for q in alpha["quarters"]) == pytest.approx(alpha["annual_income_npr"])


def test_forecast_usage_percentages(db, income_world):
    expected = _expected_alpha_income()
    with db.session_scope() as s:
        s.query(CompanyFinancial).filter_by(company_id=income_world["alpha"], quarter=4).one().electricity_sales_npr = expected
    with db.session_scope() as s:
        alpha = next(c for c in inc.forecast(s)["companies"] if c["symbol"] == "ALPHA")
    wet, dry = inc.seasonal_factors()
    assert alpha["wet_usage_pct"] == pytest.approx(wet * 100) and alpha["dry_usage_pct"] == pytest.approx(dry * 100)
    total = alpha["wet_energy_gwh"] + alpha["dry_energy_gwh"]
    assert alpha["avg_usage_pct"] == pytest.approx(total * 1000 / (15 * 8760) * 100)
    assert alpha["dry_usage_pct"] < alpha["avg_usage_pct"] < alpha["wet_usage_pct"]


def test_forecast_partial_year_plant_skips_calibration(db, income_world):
    with db.session_scope() as s:
        s.query(Project).filter_by(project_id="HP_2").one().commissioning_year = 2025   # inside FY 2082/83
    with db.session_scope() as s:
        alpha = next(c for c in inc.forecast(s)["companies"] if c["symbol"] == "ALPHA")
    assert alpha["calibration"]["factor"] == 1.0 and "partial_year" in alpha["flags"]


def test_forecast_clips_extreme_calibration(db, income_world):
    expected = _expected_alpha_income()
    with db.session_scope() as s:
        s.query(CompanyFinancial).filter_by(company_id=income_world["alpha"], quarter=4).one().electricity_sales_npr = 10 * expected
    with db.session_scope() as s:
        alpha = next(c for c in inc.forecast(s)["companies"] if c["symbol"] == "ALPHA")
    assert alpha["calibration"]["factor"] == inc.CALIBRATION_MAX and "calibration_clipped" in alpha["flags"]


def test_forecast_profit_shape_indicator(db, income_world):
    with db.session_scope() as s:
        out = inc.forecast(s)
    alpha = next(c for c in out["companies"] if c["symbol"] == "ALPHA")
    beta = next(c for c in out["companies"] if c["symbol"] == "BETA")
    assert alpha["profit_shape_gap_pp"] is not None and alpha["profit_shape_gap_pp"] >= 0
    assert beta["profit_shape_gap_pp"] is None


def test_forecast_on_an_empty_database(db):
    with db.session_scope() as s:
        out = inc.forecast(s)
    assert out["companies"] == [] and out["quarters"] == ["Q1", "Q2", "Q3", "Q4"]
    assert out["totals"]["companies"] == 0 and out["totals"]["annual_income_npr"] == 0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_income.py -q`
Expected: the new tests FAIL with `AttributeError: module 'src.analytics.income' has no attribute ...`.

- [ ] **Step 4: Write the implementation**

In `src/analytics/income.py` change the import block at the top to:

```python
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import config
from ..models import Company, CompanyFact, CompanyFinancial, Project, ProjectStatus
```

and append to the file:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_income.py -q`
Expected: all pass (about 16 passed).

- [ ] **Step 6: Align the spec with the calibration rule**

In `docs/superpowers/specs/2026-10-05-income-and-finance-cost-design.md`, replace the sentence in Section 1 item 4 that begins "using only plants commissioned before that year began" with: "If any operating plant began in or after the reported year's starting calendar year, calibration is skipped (factor 1.0, flag `partial_year`), because reported sales include that plant's partial-year revenue." Use the Edit tool; the sentence reads: `using only plants commissioned before that year began (others would distort a full-year comparison and are flagged `partial_year`).`

- [ ] **Step 7: Run the whole suite, then commit**

Run: `python -m pytest -q`
Expected: all green.

```bash
git add src/analytics/income.py tests/conftest.py tests/test_income.py docs/superpowers/specs/2026-10-05-income-and-finance-cost-design.md
git commit -m "feat: calibrated seasonal income forecast and company ranking"
```

---

### Task 3: Finance position and low-rate scenario maths

**Files:**
- Create: `src/analytics/finance_cost.py`
- Create: `tests/test_finance_cost.py`

**Interfaces:**
- Produces: constants `DEFAULT_RATE_DELTA_PP = -2.0`, `DEFAULT_REPAY_PCT = 8.0`, `DEFAULT_RETENTION_PCT = 70.0`, `DEFAULT_YEARS = 5`; `build_position(loans, finance_cost, net_profit, tax_provision, paid_up, eps, bvps) -> dict` with keys `loans, finance_cost, implied_rate_pct, tax_rate, shares, eps, bvps, interest_cover, flags`; `scenario(position, rate_delta_pp=..., repay_pct=..., retention_pct=..., years=...) -> dict | None` with keys `rate_scenario_pct, years (list of year dicts), eps_baseline, eps_after_year1, bvps_baseline, bvps_after_horizon`; year dict keys `year, opening_loans, base_finance_cost, scenario_finance_cost, saving, net_profit_change, eps_change, bvps_change`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_finance_cost.py`:

```python
import pytest

from src.analytics import finance_cost as fc

# loans 1,000m at 10% (finance cost 100m), net profit 200m, tax 50m (20% effective), 10m shares
POSITION = dict(loans=1_000e6, finance_cost=100e6, net_profit=200e6, tax_provision=50e6, paid_up=1_000e6,
                eps=20.0, bvps=130.0)


def test_position_basics():
    p = fc.build_position(**POSITION)
    assert p["implied_rate_pct"] == pytest.approx(10.0)
    assert p["tax_rate"] == pytest.approx(0.2)
    assert p["shares"] == pytest.approx(10e6)
    assert p["interest_cover"] == pytest.approx((250e6 + 100e6) / 100e6)
    assert p["flags"] == []


def test_position_flags():
    zero_cost = fc.build_position(**{**POSITION, "finance_cost": 0.0})
    assert zero_cost["implied_rate_pct"] is None and "zero_finance_cost" in zero_cost["flags"]
    assert zero_cost["interest_cover"] is None
    no_loans = fc.build_position(**{**POSITION, "loans": 0.0})
    assert no_loans["implied_rate_pct"] is None and "no_loans" in no_loans["flags"]
    tiny = fc.build_position(**{**POSITION, "loans": 500.0})
    assert tiny["implied_rate_pct"] is None and "tiny_loans" in tiny["flags"]
    cheap = fc.build_position(**{**POSITION, "finance_cost": 10e6})          # 1%
    assert "rate_out_of_range" in cheap["flags"] and cheap["implied_rate_pct"] == pytest.approx(1.0)
    dear = fc.build_position(**{**POSITION, "finance_cost": 300e6})          # 30%
    assert "rate_out_of_range" in dear["flags"]


def test_loss_making_company_has_zero_tax_rate():
    p = fc.build_position(**{**POSITION, "net_profit": -80e6, "tax_provision": 10e6})
    assert p["tax_rate"] == 0.0 and "loss_making" in p["flags"]


def test_scenario_year_by_year_numbers():
    p = fc.build_position(**POSITION)
    s = fc.scenario(p, rate_delta_pp=-2, repay_pct=8, retention_pct=70, years=3)
    assert s["rate_scenario_pct"] == pytest.approx(8.0)
    y1, y2, y3 = s["years"]
    assert y1["opening_loans"] == pytest.approx(1_000e6) and y2["opening_loans"] == pytest.approx(920e6)
    assert y1["base_finance_cost"] == pytest.approx(100e6)              # equals the reported finance cost
    assert y1["saving"] == pytest.approx(20e6) and y2["saving"] == pytest.approx(18.4e6)
    assert y1["net_profit_change"] == pytest.approx(16e6)               # saving x (1 - 20% tax)
    assert y1["eps_change"] == pytest.approx(1.6)
    assert y1["bvps_change"] == pytest.approx(16e6 * 0.7 / 10e6)        # 1.12
    assert y2["bvps_change"] == pytest.approx((16e6 + 14.72e6) * 0.7 / 10e6)
    assert s["eps_after_year1"] == pytest.approx(21.6)
    assert s["bvps_after_horizon"] == pytest.approx(130.0 + y3["bvps_change"])
    assert len(s["years"]) == 3


def test_scenario_rate_cannot_go_below_zero():
    p = fc.build_position(**POSITION)
    s = fc.scenario(p, rate_delta_pp=-15, years=1)
    assert s["rate_scenario_pct"] == 0.0
    assert s["years"][0]["scenario_finance_cost"] == 0.0 and s["years"][0]["saving"] == pytest.approx(100e6)


def test_scenario_zero_and_positive_changes():
    p = fc.build_position(**POSITION)
    flat = fc.scenario(p, rate_delta_pp=0, years=2)
    assert all(y["saving"] == 0 and y["eps_change"] == 0 for y in flat["years"])
    worse = fc.scenario(p, rate_delta_pp=1, years=1)
    assert worse["years"][0]["saving"] == pytest.approx(-10e6) and worse["years"][0]["eps_change"] < 0


def test_scenario_is_none_when_no_rate_can_be_implied():
    assert fc.scenario(fc.build_position(**{**POSITION, "finance_cost": 0.0})) is None
    assert fc.scenario(fc.build_position(**{**POSITION, "loans": 0.0})) is None
    assert fc.scenario(fc.build_position(**{**POSITION, "paid_up": None})) is None


def test_loss_making_company_still_gains_from_a_rate_cut():
    p = fc.build_position(**{**POSITION, "net_profit": -80e6, "tax_provision": 10e6})
    s = fc.scenario(p, rate_delta_pp=-2, years=1)
    assert s["years"][0]["net_profit_change"] == pytest.approx(s["years"][0]["saving"])   # no tax charge
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_finance_cost.py -q`
Expected: collection error, `cannot import name 'finance_cost'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/analytics/finance_cost.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_finance_cost.py -q`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/analytics/finance_cost.py tests/test_finance_cost.py
git commit -m "feat: finance position and low-interest-rate scenario maths"
```

---

### Task 4: Database layer, plant allocation and burden ranking

**Files:**
- Modify: `src/analytics/finance_cost.py`
- Modify: `tests/test_finance_cost.py`

**Interfaces:**
- Consumes: Task 2 (`operating_plants`), Task 3 (`build_position`, `scenario`, defaults), fixture `income_world`.
- Produces: `latest_positions(session) -> dict[int, dict]` (company_id -> position dict plus `fiscal_year`), `allocate_to_plants(loans, finance_cost, plants) -> list[dict]` (keys `project_id, plant, mw, share_pct, loans, finance_cost`), `impact(session, rate_delta_pp=..., repay_pct=..., retention_pct=..., years=..., income_by_company=None) -> dict(assumptions, companies)`. Each company dict has: `company_id, symbol, name, fiscal_year, loans, finance_cost, implied_rate_pct, tax_rate, interest_cover, burden_pct, burden_rank, flags, scenario, plants`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_finance_cost.py`:

```python
from src.models import CompanyFinancial


def test_allocate_to_plants_splits_by_mw_and_sums_back():
    plants = [{"project_id": "A", "plant": "A", "mw": 10.0}, {"project_id": "B", "plant": "B", "mw": 30.0}]
    out = fc.allocate_to_plants(100e6, 8e6, plants)
    assert [round(p["share_pct"], 1) for p in out] == [25.0, 75.0]
    assert sum(p["loans"] for p in out) == pytest.approx(100e6)
    assert sum(p["finance_cost"] for p in out) == pytest.approx(8e6)
    assert fc.allocate_to_plants(100e6, 8e6, []) == []


def test_latest_positions_use_the_newest_full_year(db, income_world):
    with db.session_scope() as s:
        s.add(CompanyFinancial(company_id=income_world["alpha"], fiscal_year="2081/82", quarter=4, loans_npr=1.0,
                               finance_cost_npr=1.0, paid_up_capital_npr=1.0))
    with db.session_scope() as s:
        positions = fc.latest_positions(s)
    alpha = positions[income_world["alpha"]]
    assert alpha["fiscal_year"] == "2082/83" and alpha["loans"] == pytest.approx(1_000e6)
    assert income_world["unlisted"] not in positions


def test_impact_ranks_by_burden_and_allocates_plants(db, income_world):
    income = {income_world["alpha"]: 500e6, income_world["beta"]: 100e6}
    with db.session_scope() as s:
        out = fc.impact(s, income_by_company=income)
    assert out["assumptions"] == {"rate_delta_pp": -2.0, "repay_pct": 8.0, "retention_pct": 70.0, "years": 5}
    by = {c["symbol"]: c for c in out["companies"]}
    assert by["ALPHA"]["burden_pct"] == pytest.approx(20.0) and by["BETA"]["burden_pct"] == pytest.approx(30.0)
    assert [c["symbol"] for c in out["companies"]] == ["BETA", "ALPHA"]          # heaviest burden first
    assert by["BETA"]["burden_rank"] == 1 and by["ALPHA"]["burden_rank"] == 2
    alpha_plants = by["ALPHA"]["plants"]
    assert {p["project_id"] for p in alpha_plants} == {"HP_1", "HP_2"}           # HP_5 is under construction
    assert sum(p["loans"] for p in alpha_plants) == pytest.approx(1_000e6)
    assert by["ALPHA"]["scenario"]["years"][0]["saving"] == pytest.approx(20e6)
    assert len(by["ALPHA"]["scenario"]["years"]) == 5


def test_impact_without_income_has_no_burden(db, income_world):
    with db.session_scope() as s:
        out = fc.impact(s, years=2)
    assert all(c["burden_pct"] is None for c in out["companies"])
    assert [c["symbol"] for c in out["companies"]] == ["ALPHA", "BETA"]           # symbol order when no burden
    assert len(out["companies"][0]["scenario"]["years"]) == 2


def test_impact_handles_zero_finance_cost_and_empty_database(db, income_world):
    with db.session_scope() as s:
        s.query(CompanyFinancial).filter_by(company_id=income_world["beta"], quarter=4).one().finance_cost_npr = 0.0
    with db.session_scope() as s:
        beta = next(c for c in fc.impact(s)["companies"] if c["symbol"] == "BETA")
    assert beta["scenario"] is None and "zero_finance_cost" in beta["flags"] and beta["implied_rate_pct"] is None


def test_impact_on_an_empty_database(db):
    with db.session_scope() as s:
        assert fc.impact(s)["companies"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_finance_cost.py -q`
Expected: new tests FAIL with `AttributeError ... 'allocate_to_plants'` / `'latest_positions'` / `'impact'`.

- [ ] **Step 3: Write the implementation**

In `src/analytics/finance_cost.py` add the imports directly under the module docstring (before `DEFAULT_RATE_DELTA_PP`):

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Company, CompanyFinancial
from .income import operating_plants
```

and append:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_finance_cost.py -q`
Expected: all pass (14 passed).

- [ ] **Step 5: Run the whole suite and commit**

Run: `python -m pytest -q`
Expected: all green.

```bash
git add src/analytics/finance_cost.py tests/test_finance_cost.py
git commit -m "feat: finance impact per company with plant allocation and burden ranking"
```

---

### Task 5: API endpoints, page route and sidebar entry

**Files:**
- Modify: `src/dashboard.py`
- Modify: `dashboards/templates/base.html:19-20`
- Create: `dashboards/templates/income.html` (placeholder shell here, full page in Task 6)
- Create: `tests/test_income_api.py`

**Interfaces:**
- Consumes: `income.forecast`, `finance_cost.impact`, `finance_cost.DEFAULT_*`.
- Produces: `GET /api/income/forecast`, `GET /api/finance/impact?rate_delta_pp&repay_pct&retention_pct&years`, `GET /income`, helper `float_arg(name, default, lo, hi, integer=False)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_income_api.py`:

```python
import json

import pytest

from src.dashboard import create_app


@pytest.fixture()
def client(db, income_world):
    app = create_app(db)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def empty_client(db):
    app = create_app(db)
    app.config["TESTING"] = True
    return app.test_client()


def test_forecast_endpoint_contract(client):
    r = client.get("/api/income/forecast")
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) == {"assumptions", "quarters", "totals", "companies"}
    assert len(body["quarters"]) == 4 and body["companies"][0]["rank"] == 1
    first = body["companies"][0]
    for key in ("wet_usage_pct", "dry_usage_pct", "avg_usage_pct", "annual_income_npr", "quarters", "calibration", "flags"):
        assert key in first
    json.dumps(body, allow_nan=False)           # no NaN/Infinity leaks into the JSON


def test_finance_endpoint_defaults_and_burden(client):
    body = client.get("/api/finance/impact").get_json()
    assert body["assumptions"] == {"rate_delta_pp": -2.0, "repay_pct": 8.0, "retention_pct": 70.0, "years": 5}
    assert {c["symbol"] for c in body["companies"]} == {"ALPHA", "BETA"}
    assert all(c["burden_pct"] is not None for c in body["companies"])      # income comes from the forecast
    json.dumps(body, allow_nan=False)


def test_finance_endpoint_accepts_parameters(client):
    body = client.get("/api/finance/impact?rate_delta_pp=-1&repay_pct=0&retention_pct=100&years=2").get_json()
    assert body["assumptions"]["years"] == 2
    alpha = next(c for c in body["companies"] if c["symbol"] == "ALPHA")
    assert len(alpha["scenario"]["years"]) == 2 and alpha["scenario"]["years"][0]["saving"] == pytest.approx(10e6)


@pytest.mark.parametrize("query", [
    "rate_delta_pp=abc", "rate_delta_pp=nan", "rate_delta_pp=inf", "rate_delta_pp=-11", "rate_delta_pp=11",
    "repay_pct=-1", "repay_pct=51", "retention_pct=101", "retention_pct=x", "years=0", "years=11", "years=2.5",
    "years=abc"])
def test_finance_endpoint_rejects_bad_input(client, query):
    r = client.get("/api/finance/impact?" + query)
    assert r.status_code == 400 and r.get_json()["error"]


def test_empty_database_still_answers(empty_client):
    f = empty_client.get("/api/income/forecast")
    assert f.status_code == 200 and f.get_json()["companies"] == []
    i = empty_client.get("/api/finance/impact")
    assert i.status_code == 200 and i.get_json()["companies"] == []


def test_income_page_renders_and_is_in_the_sidebar(client):
    r = client.get("/income")
    assert r.status_code == 200 and b"Income" in r.data
    assert b'href="/income"' in client.get("/").data
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_income_api.py -q`
Expected: FAIL with 404 responses (routes do not exist).

- [ ] **Step 3: Add the helper, routes and imports to `src/dashboard.py`**

Change the analytics import block to include the two modules (keep existing lines, add these two):

```python
from .analytics import finance_cost
from .analytics import income
```

Add the helper directly after `int_arg`:

```python
def float_arg(name: str, default: float, lo: float, hi: float, integer: bool = False) -> float:
    """Numeric query parameter that must lie inside [lo, hi]; anything else (including nan/inf) is a 400."""
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        abort(400, f"{name} must be a number")
    if not math.isfinite(value) or not lo <= value <= hi:
        abort(400, f"{name} must be between {lo:g} and {hi:g}")
    if integer and value != int(value):
        abort(400, f"{name} must be a whole number")
    return int(value) if integer else value
```

Add the page route after `analytics_page`:

```python
    @app.get("/income")
    def income_page():
        return render_template("income.html")
```

Add the endpoints after `api_capacity` (before `api_unknowns`):

```python
    @app.get("/api/income/forecast")
    def api_income_forecast():
        with db.session_scope() as s:
            return jsonify(json_safe(income.forecast(s)))

    @app.get("/api/finance/impact")
    def api_finance_impact():
        delta = float_arg("rate_delta_pp", finance_cost.DEFAULT_RATE_DELTA_PP, -10, 10)
        repay = float_arg("repay_pct", finance_cost.DEFAULT_REPAY_PCT, 0, 50)
        retention = float_arg("retention_pct", finance_cost.DEFAULT_RETENTION_PCT, 0, 100)
        years = float_arg("years", finance_cost.DEFAULT_YEARS, 1, 10, integer=True)
        with db.session_scope() as s:
            forecast = income.forecast(s)
            income_by_company = {c["company_id"]: c["annual_income_npr"] for c in forecast["companies"]}
            return jsonify(json_safe(finance_cost.impact(
                s, delta, repay, retention, years, income_by_company=income_by_company)))
```

- [ ] **Step 4: Add the sidebar entry and a minimal template**

In `dashboards/templates/base.html` lines 19-20 replace the `nav` list with (adds one entry; reuses the existing `chart-column` icon because the sprite has no finance icon):

```jinja
{% set nav = [('/', 'Overview', 'layout-dashboard'), ('/map', 'Map', 'map'), ('/projects', 'Projects', 'table-2'),
              ('/companies', 'Companies', 'building-2'), ('/income', 'Income & finance', 'chart-column'),
              ('/news', 'News', 'newspaper'), ('/analytics', 'Analytics', 'chart-column')] %}
```

Create `dashboards/templates/income.html` with this placeholder (Task 6 replaces it):

```jinja
{% extends "base.html" %}
{% block title %}Income &amp; finance · Nepal Hydropower{% endblock %}
{% block content %}
<h1>Income and finance cost</h1>
{% endblock %}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_income_api.py -q`
Expected: all pass (about 20 passed).

- [ ] **Step 6: Run the whole suite and commit**

Run: `python -m pytest -q`
Expected: all green. `test_pages_render` still passes (`/income` is not in its list, base.html still renders).

```bash
git add src/dashboard.py dashboards/templates/base.html dashboards/templates/income.html tests/test_income_api.py
git commit -m "feat: income forecast and finance impact endpoints, page route and sidebar entry"
```

---

### Task 6: The Income and finance page

**Files:**
- Modify (replace): `dashboards/templates/income.html`
- Modify: `tests/test_income_api.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: the two endpoints from Task 5 and `app.js` helpers `api(path, params)`, `el(tag, attrs, ...children)`, `renderTable(container, columns, rows, opts)`, `npr(v)`, `fmt(n, d)`, `companyLink(id, name)`, `projectLink(id, name)`, `baseChartOptions(extra)`, `CHART_SERIES()`, `cssVar(name)`, `showError(container, err)`.

- [ ] **Step 1: Add the page content test**

Append to `tests/test_income_api.py`:

```python
def test_income_page_has_the_sections_and_controls(client):
    html = client.get("/income").get_data(as_text=True)
    for needle in ('id="kpis"', 'id="c-usage"', 'id="c-quarters"', 'id="rank"', 'id="fin-controls"',
                   'name="rate_delta_pp"', 'name="repay_pct"', 'name="retention_pct"', 'name="years"',
                   'id="fin"', 'id="plants"', "estimate"):
        assert needle in html, needle
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_income_api.py::test_income_page_has_the_sections_and_controls -q`
Expected: FAIL (placeholder template lacks the sections).

- [ ] **Step 3: Write the page**

Replace `dashboards/templates/income.html` entirely with:

```jinja
{% extends "base.html" %}
{% block title %}Income &amp; finance · Nepal Hydropower{% endblock %}
{% block content %}
<h1>Income and finance cost</h1>
<p class="sub">Every figure here is an estimate, not a reported result. Production is modelled from installed capacity and assumed seasonal capacity factors, priced at each plant's verified PPA rate (or an assumed tariff, flagged), then scaled once per company to its last reported year of electricity sales. Wet season is mid-April to mid-December, dry season mid-December to mid-April. Fiscal quarters start in Shrawan (mid-July).</p>

<div class="grid kpis" id="kpis"></div>

<div class="grid two">
  <div class="card"><h2>Capacity usage by season (15 largest companies)</h2><div class="chart-box"><canvas id="c-usage"></canvas></div></div>
  <div class="card"><h2 id="q-title">Forecast income by quarter (NPR)</h2><div class="chart-box"><canvas id="c-quarters"></canvas></div></div>
</div>

<div class="card" style="margin-top:16px">
  <h2>Companies ranked by forecast annual income</h2>
  <p class="sub" id="rank-note" style="font-size:13px"></p>
  <div id="rank"></div>
</div>

<div class="card" style="margin-top:16px">
  <h2>Finance cost and the effect of lower interest rates</h2>
  <p class="sub" style="font-size:13px">Implied rate = finance cost ÷ year-end loans (an approximation: the average balance is not reported). The loan is assumed to be repaid by a fixed share of the current balance each year. A lower rate raises profit after the company's own effective tax rate; EPS uses paid-up capital ÷ 100 shares; book value per share grows by the retained part of the extra profit.</p>
  <form class="filters" id="fin-controls">
    <label>Rate change (percentage points)<input name="rate_delta_pp" type="number" step="0.25" min="-10" max="10" value="-2" style="width:110px"></label>
    <label>Loan repaid per year (%)<input name="repay_pct" type="number" step="1" min="0" max="50" value="8" style="width:110px"></label>
    <label>Extra profit retained (%)<input name="retention_pct" type="number" step="5" min="0" max="100" value="70" style="width:110px"></label>
    <label>Horizon (years)<input name="years" type="number" step="1" min="1" max="10" value="5" style="width:90px"></label>
    <button type="submit">Update</button>
  </form>
  <p class="sub" id="fin-note" style="font-size:13px"></p>
  <div id="fin"></div>
</div>

<div class="card" style="margin-top:16px">
  <h2 id="plant-title">Plant allocation</h2>
  <p class="sub" style="font-size:13px">Loans and finance cost are reported per company. They are spread across operating plants by MW share, so these plant figures are allocated, not reported. Pick a company in the table above.</p>
  <div id="plants"></div>
</div>
{% endblock %}
{% block scripts %}
<script src="{{ url_for('static', filename='vendor/chart.umd.min.js') }}"></script>
<script>
const FLAG_TEXT = {
  assumed_rate: "assumed tariff", partial_year: "plant started in the reported year", no_reported_sales: "no reported sales",
  calibration_clipped: "calibration clipped", no_loans: "no loans", tiny_loans: "tiny loans",
  zero_finance_cost: "no finance cost reported", rate_out_of_range: "rate outside 2-20%", loss_making: "loss-making",
};
const flagText = flags => flags && flags.length ? flags.map(f => FLAG_TEXT[f] || f).join(", ") : "–";
const pct = (v, d = 1) => (v === null || v === undefined) ? "–" : fmt(v, d) + "%";
let financeRows = [];

async function loadForecast() {
  const data = await api("/api/income/forecast");
  const a = data.assumptions, t = data.totals;
  document.getElementById("kpis").replaceChildren(
    kpi("Companies with an operating plant", fmt(t.companies), "NEPSE-listed"),
    kpi("Operating capacity", fmt(t.capacity_mw, 0) + " MW", "owned or developed by those companies"),
    kpi("Forecast income, " + (a.forecast_fiscal_year || "next year"), npr(t.annual_income_npr), "NPR, estimated"),
    kpi("Average capacity usage", pct(t.avg_usage_pct), "wet " + pct(t.wet_usage_pct) + " · dry " + pct(t.dry_usage_pct) + ", day-weighted"));
  document.getElementById("q-title").textContent = "Forecast income by quarter, FY " + (a.forecast_fiscal_year || "") + " (NPR)";
  document.getElementById("rank-note").textContent =
    `Model: annual capacity factor ${fmt(a.capacity_factor * 100, 0)}%, ${fmt(a.dry_energy_share * 100, 0)}% of energy in the dry season. ` +
    `Fallback tariff ${a.fallback_wet_tariff} / ${a.fallback_dry_tariff} NPR/kWh where no verified PPA rate exists. ` +
    `"Calibration" is reported FY ${a.reported_fiscal_year || "–"} sales ÷ modelled income, limited to ${a.calibration_range[0]}–${a.calibration_range[1]}.`;

  const top = [...data.companies].sort((x, y) => y.capacity_mw - x.capacity_mw).slice(0, 15);
  const series = CHART_SERIES();
  new Chart("c-usage", { type: "bar",
    data: { labels: top.map(c => c.symbol), datasets: [
      { label: "Wet season", data: top.map(c => c.wet_usage_pct), backgroundColor: series[0] },
      { label: "Dry season", data: top.map(c => c.dry_usage_pct), backgroundColor: series[1] },
      { label: "Average", data: top.map(c => c.avg_usage_pct), backgroundColor: series[2] }] },
    options: baseChartOptions({ scales: { y: { title: { display: true, text: "% of installed capacity" }, beginAtZero: true } } }) });

  const perQuarter = data.quarters.map((_, i) => data.companies.reduce((sum, c) => sum + c.quarters[i].income_npr, 0));
  new Chart("c-quarters", { type: "bar",
    data: { labels: data.quarters, datasets: [{ data: perQuarter, backgroundColor: series[0] }] },
    options: baseChartOptions({ plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => npr(ctx.parsed.y) + " NPR" } } },
                                scales: { y: { ticks: { callback: v => npr(v) } } } }) });

  const qCols = data.quarters.map((label, i) => ({ label: label.split(" ").pop(), num: true, render: r => npr(r.quarters[i].income_npr) }));
  renderTable(document.getElementById("rank"), [
    { label: "#", num: true, key: "rank" },
    { label: "Company", render: r => companyLink(r.company_id, r.symbol + " · " + r.name) },
    { label: "MW", num: true, render: r => fmt(r.capacity_mw, 1) },
    { label: "Wet usage", num: true, render: r => pct(r.wet_usage_pct) },
    { label: "Dry usage", num: true, render: r => pct(r.dry_usage_pct) },
    { label: "Average usage", num: true, render: r => pct(r.avg_usage_pct) },
    ...qCols,
    { label: "Annual income", num: true, render: r => npr(r.annual_income_npr) },
    { label: "Calibration", num: true, render: r => r.calibration.basis === "reported_sales" ? "×" + fmt(r.calibration.factor, 2) : "none" },
    { label: "Notes", render: r => flagText(r.flags) },
  ], data.companies, { empty: "No listed company has an operating plant with capacity." });
}

function kpi(label, value, note) {
  return el("div", { class: "card kpi" }, el("div", { class: "label" }, label), el("div", { class: "value" }, value), el("div", { class: "note" }, note || ""));
}

async function loadFinance() {
  const form = document.getElementById("fin-controls");
  const params = Object.fromEntries(new FormData(form).entries());
  const data = await api("/api/finance/impact", params);
  financeRows = data.companies;
  const a = data.assumptions;
  document.getElementById("fin-note").textContent =
    `Scenario: rate ${a.rate_delta_pp > 0 ? "+" : ""}${a.rate_delta_pp} points, ${a.repay_pct}% of the loan repaid each year, ${a.retention_pct}% of extra profit retained, ${a.years}-year horizon. ` +
    `Companies are ordered by finance cost as a share of forecast income (heaviest first).`;
  renderTable(document.getElementById("fin"), [
    { label: "Company", render: r => el("button", { type: "button", class: "sort-btn", title: "Show plant allocation", onclick: () => showPlants(r) }, r.symbol + " · " + r.name) },
    { label: "Loans", num: true, render: r => npr(r.loans) },
    { label: "Implied rate", num: true, render: r => pct(r.implied_rate_pct) },
    { label: "Finance cost", num: true, render: r => npr(r.finance_cost) },
    { label: "% of income", num: true, render: r => pct(r.burden_pct) },
    { label: "Interest cover", num: true, render: r => r.interest_cover === null ? "–" : fmt(r.interest_cover, 1) + "×" },
    { label: "Year-1 saving", num: true, render: r => r.scenario ? npr(r.scenario.years[0].saving) : "–" },
    { label: "EPS now → year 1", num: true, render: r => r.scenario && r.scenario.eps_after_year1 !== null ? `${fmt(r.scenario.eps_baseline, 2)} → ${fmt(r.scenario.eps_after_year1, 2)}` : "–" },
    { label: "Book value/share now → end", num: true, render: r => r.scenario && r.scenario.bvps_after_horizon !== null ? `${fmt(r.scenario.bvps_baseline, 1)} → ${fmt(r.scenario.bvps_after_horizon, 1)}` : "–" },
    { label: "Notes", render: r => flagText(r.flags) },
  ], financeRows, { empty: "No company has reported loans." });
}

function showPlants(row) {
  document.getElementById("plant-title").textContent = "Plant allocation: " + row.symbol + " (FY " + row.fiscal_year + ")";
  renderTable(document.getElementById("plants"), [
    { label: "Plant", render: p => projectLink(p.project_id, p.plant) },
    { label: "MW", num: true, render: p => fmt(p.mw, 1) },
    { label: "Share", num: true, render: p => pct(p.share_pct) },
    { label: "Allocated loans", num: true, render: p => npr(p.loans) },
    { label: "Allocated finance cost", num: true, render: p => npr(p.finance_cost) },
  ], row.plants, { empty: "This company has no operating plant with capacity, so nothing to allocate to." });
}

document.getElementById("fin-controls").addEventListener("submit", e => {
  e.preventDefault();
  loadFinance().catch(err => showError(document.getElementById("fin"), err));
});
loadForecast().catch(err => showError(document.getElementById("rank"), err));
loadFinance().catch(err => showError(document.getElementById("fin"), err));
</script>
{% endblock %}
```

- [ ] **Step 4: Run the page test and the whole suite**

Run: `python -m pytest tests/test_income_api.py -q` then `python -m pytest -q`
Expected: all green.

- [ ] **Step 5: Check the real data without a browser**

Run this to confirm the endpoints answer on the real database and the numbers look sane (no server needed):

```bash
python - <<'EOF'
from src.dashboard import create_app
c = create_app().test_client()
f = c.get("/api/income/forecast").get_json()
print(f["assumptions"]["reported_fiscal_year"], f["assumptions"]["forecast_fiscal_year"], f["totals"])
for r in f["companies"][:8]:
    print(r["rank"], r["symbol"], round(r["capacity_mw"], 1), round(r["avg_usage_pct"], 1), round(r["annual_income_npr"] / 1e6), r["calibration"]["basis"], r["flags"])
i = c.get("/api/finance/impact").get_json()
for r in i["companies"][:5]:
    print(r["symbol"], r["implied_rate_pct"], r["burden_pct"], r["flags"])
EOF
```

Expected: forecast FY `2083/84`, 100+ companies, annual incomes in the hundreds of millions to billions of NPR, calibration factors mostly between 0.3 and 1.5. Report anything surprising (very many `calibration_clipped`, or income far from the reported sales) instead of hiding it.

- [ ] **Step 6: Document the page**

Append to `README.md`:

```markdown
## Income and finance page

`/income` shows, for every NEPSE-listed company with an operating plant: wet, dry and day-weighted average capacity
usage; a four-quarter income forecast (production modelled from capacity and assumed seasonal capacity factors, priced
at verified PPA rates, scaled to the last reported year of electricity sales); and a ranking by forecast annual income.
A second panel shows each company's implied interest rate and finance-cost burden and what a lower rate would do to
net profit, EPS and book value per share (defaults: -2 percentage points, 8% of the loan repaid per year, 70% of the
extra profit retained, 5 years). Everything is an estimate; plants without a verified PPA rate use the assumed tariff
and are flagged. API: `GET /api/income/forecast`, `GET /api/finance/impact`.
```

- [ ] **Step 7: Commit**

```bash
git add dashboards/templates/income.html tests/test_income_api.py README.md
git commit -m "feat: income and finance page with usage, income ranking and low-rate scenario"
```

---

## Self-review

**Spec coverage**
- Wet, dry and average usage: Task 2 (`forecast`, usage fields), Task 6 (table, chart, KPI).
- Quarterly income forecast, calibration, horizon, fallback flag, ranking: Tasks 1-2.
- Finance position, base path, scenario, EPS and book value, retention, horizon: Tasks 3-4.
- Plant allocation by MW share and labelling: Task 4 (`allocate_to_plants`), Task 6 (plant panel and its wording).
- Burden and interest-cover ranking next to income ranking: Task 4 (`impact`), Task 6 (finance table). The burden uses forecast income; the finance table is ordered by it.
- API ranges and 400 behaviour: Task 5.
- Profit-shape sanity check: Task 2 (`shape_gap_pp`, field `profit_shape_gap_pp`). The page does not display it yet; it is available in the API only. Showing it is a one-column addition if wanted.
- Spec deviation recorded and fixed: calibration is skipped, not restricted to full-year plants, when any plant started in the reported year (Task 2, Step 6 edits the spec).
- Not in v1 (stated in the spec): per-company seasonal overrides, loans on plants under construction.

**Type consistency:** `Rate(wet, dry, basis)`, `calibration()` returns `factor/raw_factor/basis/flags`, forecast company keys, `build_position` keys, `scenario` keys and `impact` company keys are used with identical names in later tasks and in the page script.

**Placeholders:** none; every code step contains the code.
