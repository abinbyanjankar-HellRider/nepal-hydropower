import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import DatabaseManager  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    """A fresh, isolated SQLite database with reference data loaded."""
    manager = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    manager.init_db()
    manager.init_reference_data()
    return manager


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
        s.flush()  # CompanyFact has no relationship to Project, so persist projects before the facts that point at them
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
