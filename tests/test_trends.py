import pytest

from src.analytics import company_profiles as cp
from src.analytics.company_trends import build_trend
from src.collectors.manual_import import import_company_financials_csv
from src.dashboard import create_app
from src.models import Company, CompanyFinancial

M = 1e6


def _company(db, symbol="TRD"):
    with db.session_scope() as s:
        c = Company(company_name=f"{symbol} Hydropower Limited", stock_symbol=symbol, nepse_listed=True)
        s.add(c)
        s.flush()
        return c.company_id


def _seed(db, cid):
    ytd = {("2080/81", 4): -50, ("2081/82", 1): 100, ("2081/82", 2): 250, ("2081/82", 3): 300, ("2081/82", 4): 500,
           ("2082/83", 1): 150, ("2082/83", 2): 260}  # Q3 comes from the full report row below
    with db.session_scope() as s:
        for (fy, q), v in ytd.items():
            s.add(CompanyFinancial(company_id=cid, fiscal_year=fy, quarter=q, net_profit_npr=v * M))
        s.add(CompanyFinancial(company_id=cid, fiscal_year="2082/83", quarter=3, net_profit_npr=250 * M, paid_up_capital_npr=1000 * M,
                               reserves_npr=500 * M, loans_npr=3000 * M, electricity_sales_npr=900 * M, operating_income_npr=1000 * M,
                               finance_cost_npr=150 * M, tax_provision_npr=50 * M, roe_pct=12.0, eps=2.5))
    return cid


@pytest.fixture()
def trend(db):
    cid = _seed(db, _company(db))
    with db.session_scope() as s:
        return build_trend(s, cid)


def test_standalone_quarters_are_differences_of_cumulative_figures(trend):
    q81, q82 = trend["quarters"]["2081/82"], trend["quarters"]["2082/83"]
    assert [q81[q]["standalone_npr"] / M for q in (1, 2, 3, 4)] == [100, 150, 50, 200]
    assert [q82[q]["standalone_npr"] / M for q in (1, 2, 3)] == [150, 110, -10]  # Q3 is a loss quarter (cumulative fell)
    assert q82[4]["standalone_npr"] is None and q82[4]["ytd_npr"] is None  # not reported yet: blank, never estimated


def test_yoy_uses_same_quarter_last_year_and_skips_non_positive_bases(trend):
    q = trend["quarters"]["2082/83"]
    assert q[1]["standalone_yoy_pct"] == 50.0 and q[2]["standalone_yoy_pct"] == pytest.approx(-26.7, abs=0.1)
    assert q[1]["ytd_yoy_pct"] == 50.0 and q[3]["ytd_yoy_pct"] == pytest.approx(-16.7, abs=0.1)
    assert trend["quarters"]["2081/82"][4]["ytd_yoy_pct"] is None  # last year's base (2080/81 Q4) was a loss: no percentage


def test_annual_rows_flag_partial_current_year_and_scale_by_current_capital(trend):
    ann = {a["fiscal_year"]: a for a in trend["annual"]}
    assert trend["current_fiscal_year"] == "2082/83" and trend["years"][0] == "2082/83"
    assert ann["2081/82"]["complete"] and ann["2081/82"]["net_profit_npr"] == 500 * M and ann["2081/82"]["yoy_pct"] is None
    cur = ann["2082/83"]
    assert not cur["complete"] and cur["quarters_reported"] == 3 and cur["yoy_pct"] is None  # no YoY on a partial year
    assert cur["profit_pct_of_current_capital"] == 25.0  # 250m on today's 1,000m paid-up capital


def test_full_report_ratios_appear_only_for_years_that_have_one(trend):
    ann = {a["fiscal_year"]: a["full_report"] for a in trend["annual"]}
    m = ann["2082/83"]
    assert m["debt_to_equity"] == 2.0 and m["net_margin_pct"] == 25.0 and m["finance_cost_pct_of_sales"] == pytest.approx(16.7, abs=0.1)
    assert m["effective_tax_rate_pct"] == pytest.approx(16.7, abs=0.1) and m["interest_cover"] == 3.0  # (pre-tax 300 + finance cost 150) / 150
    assert ann["2081/82"] == {}  # nothing stored, so nothing invented


def test_latest_analysis_compares_with_last_year_and_is_factual(trend):
    L = trend["latest"]
    assert L["period"] == "FY 2082/83 Q3" and L["has_full_report"]
    assert L["net_profit_ytd_npr"] == 250 * M and L["same_period_last_year_npr"] == 300 * M and L["yoy_pct"] == pytest.approx(-16.7, abs=0.1)
    assert L["standalone_npr"] == -10 * M and L["previous_quarter_standalone_npr"] == 110 * M
    text = " ".join(L["bullets"])
    assert "-16.7%" in text and "debt-to-equity ratio of 2.0" in text and "buy" not in text.lower()
    assert "prior-year comparison" in L["comparison_note"]


def test_company_without_results_reports_unavailable(db):
    cid = _company(db, "EMP")
    with db.session_scope() as s:
        t = build_trend(s, cid)
    assert t["available"] is False and t["annual"] == [] and t["latest"] is None


def test_history_csv_fills_earlier_years_and_rejects_bad_rows(db, tmp_path):
    cid = _seed(db, _company(db))
    f = tmp_path / "hist.csv"
    f.write_text("symbol,fiscal_year,quarter,paid_up_capital_npr,reserves_npr,loans_npr,electricity_sales_npr,finance_cost_npr,eps,source\n"
                 "TRD,2081/82,4,900000000,400000000,2600000000,850000000,140000000,3.1,Annual report 2081/82 p.40\n"
                 "TRD,2081-82,4,1,1,1,1,1,1,bad fiscal year format\n"
                 "NOPE,2081/82,4,1,1,1,1,1,1,unknown symbol\n"
                 "TRD,2081/82,9,1,1,1,1,1,1,bad quarter\n", encoding="utf-8")
    with db.session_scope() as s:
        r = import_company_financials_csv(s, f)
    assert r["updated"] == 1 and r["created"] == 0 and len(r["errors"]) == 3  # existing 2081/82 Q4 row is updated, not duplicated
    with db.session_scope() as s:
        t = build_trend(s, cid)
    m = {a["fiscal_year"]: a["full_report"] for a in t["annual"]}["2081/82"]
    assert m["debt_to_equity"] == 2.0 and m["eps"] == 3.1 and m["finance_cost_npr"] == 140 * M
    assert {a["fiscal_year"]: a for a in t["annual"]}["2081/82"]["net_profit_npr"] == 500 * M  # profit kept from the earlier record


def test_profile_api_and_list_carry_trend_without_invalid_json(db):
    cid = _seed(db, _company(db))
    client = create_app(db).test_client()
    body = client.get(f"/api/companies/{cid}/profile").get_data(as_text=True)
    assert "NaN" not in body and "Infinity" not in body
    assert client.get(f"/api/companies/{cid}/profile").get_json()["trend"]["years"][0] == "2082/83"
    row = next(r for r in client.get("/api/companies/profiles").get_json() if r["company_id"] == cid)
    assert row["profit_yoy_pct"] == pytest.approx(-16.7, abs=0.1) and row["latest_period"] == "FY 2082/83 Q3"
    with db.session_scope() as s:
        assert cp.build_profile(s, cid)["trend"]["available"] is True
