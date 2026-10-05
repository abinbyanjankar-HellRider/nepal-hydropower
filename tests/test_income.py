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


from src.models import CompanyFinancial, Project  # noqa: E402


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
