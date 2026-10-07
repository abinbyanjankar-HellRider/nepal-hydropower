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


def test_deferred_tax_credit_is_not_loss_making():
    """Regression (final review): a profitable company with a negative tax provision was labelled loss-making."""
    p = fc.build_position(**{**POSITION, "net_profit": 79.7e6, "tax_provision": -9.1e6})
    assert "loss_making" not in p["flags"] and p["tax_rate"] == 0.0


def test_net_loss_with_a_positive_tax_charge_is_loss_making_with_no_tax_on_extra_profit():
    """Regression (final review): net loss plus a positive provision left a tax rate near 100%, wiping out the gain."""
    p = fc.build_position(**{**POSITION, "net_profit": -20e6, "tax_provision": 50e6})
    assert "loss_making" in p["flags"] and p["tax_rate"] == 0.0
    s = fc.scenario(p, rate_delta_pp=-2, years=1)
    assert s["years"][0]["net_profit_change"] == pytest.approx(s["years"][0]["saving"])


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


from src.models import CompanyFinancial  # noqa: E402


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


@pytest.mark.parametrize("net_profit, tax, expected", [
    (200e6, 0.5e6, "Tax holiday"),            # 0.25% effective
    (190e6, 10e6, "Partly exempt"),           # 5%
    (175e6, 25e6, "Partly exempt"),           # 12.5%: half the standard rate
    (150e6, 50e6, "Taxed"),                   # 25%
    (-20e6, 5e6, "Loss-making"),
    (30e6, -9e6, "Tax credit"),               # profitable with a deferred-tax credit
    (1.7e6, -6.7e6, "Loss-making"),           # profit only because of the credit: a pre-tax loss
    (None, None, "No data"),
])
def test_tax_status_from_reported_net_profit_and_tax(net_profit, tax, expected):
    assert fc.tax_status(net_profit, tax) == expected


def test_forecast_year_follows_the_agreed_formula():
    """income +40m over last year's sales, finance cost 96m (10% on the average balance after 8% repayment)."""
    p = fc.build_position(**POSITION, electricity_sales=400e6)
    f = fc.forecast_year(p, forecast_income=440e6, repay_pct=8, retention_pct=70)
    assert f["finance_cost"] == pytest.approx(96e6)
    assert f["net_profit"] == pytest.approx(200e6 + (40e6 + 4e6) * 0.8)       # 235.2m
    assert f["eps"] == pytest.approx(23.52)
    assert f["bvps"] == pytest.approx(130.0 + 235.2e6 * 0.7 / 10e6)           # 146.464


def test_forecast_year_with_flat_income_and_no_repayment_leaves_profit_unchanged():
    p = fc.build_position(**POSITION, electricity_sales=400e6)
    f = fc.forecast_year(p, forecast_income=400e6, repay_pct=0, retention_pct=70)
    assert f["net_profit"] == pytest.approx(200e6) and f["eps"] == pytest.approx(20.0)


def test_forecast_year_is_taxed_for_taxed_companies_and_not_for_holiday_companies():
    taxed = fc.build_position(**{**POSITION, "net_profit": 150e6, "tax_provision": 50e6}, electricity_sales=400e6)
    holiday = fc.build_position(**{**POSITION, "net_profit": 200e6, "tax_provision": 0.0}, electricity_sales=400e6)
    gain_taxed = fc.forecast_year(taxed, 440e6, repay_pct=0)["net_profit"] - 150e6
    gain_holiday = fc.forecast_year(holiday, 440e6, repay_pct=0)["net_profit"] - 200e6
    assert gain_taxed == pytest.approx(40e6 * 0.75) and gain_holiday == pytest.approx(40e6)


def test_forecast_year_is_none_without_an_anchor():
    assert fc.forecast_year(fc.build_position(**POSITION), 440e6) is None                       # no reported sales
    assert fc.forecast_year(fc.build_position(**{**POSITION, "paid_up": None}, electricity_sales=400e6), 440e6) is None
    assert fc.forecast_year(fc.build_position(**POSITION, electricity_sales=400e6), None) is None


def test_enrich_forecast_adds_tax_status_eps_and_book_value_and_lists_every_listed_company(db, income_world):
    from src.analytics import income as inc
    from src.models import Company
    with db.session_scope() as s:
        s.add(Company(company_name="Gamma Power Ltd", nepse_listed=True, stock_symbol="GAMMA"))   # listed, no plant, no financials
    with db.session_scope() as s:
        out = fc.enrich_forecast(s, inc.forecast(s))
    by = {c["symbol"]: c for c in out["companies"]}
    alpha, beta = by["ALPHA"], by["BETA"]
    assert alpha["tax_status"] == "Taxed" and alpha["tax_rate_pct"] == pytest.approx(20.0)     # 50 / (200 + 50)
    # income equals the reported 400m (calibrated), so only the finance cost changes: (-4m) x (1 - 20%)
    assert alpha["eps_reported"] == 20.0 and alpha["eps_forecast"] == pytest.approx(20.0 + 4e6 * 0.8 / 10e6)
    assert alpha["bvps_forecast"] == pytest.approx(130.0 + (200e6 + 3.2e6) * 0.7 / 10e6)
    assert alpha["net_profit_forecast"] == pytest.approx(203.2e6)
    assert beta["tax_status"] == "Tax holiday" and beta["eps_forecast"] is None and "no_reported_sales" in beta["forecast_flags"]
    others = {c["symbol"]: c for c in out["others"]}
    assert set(others) == {"GAMMA"} and others["GAMMA"]["flags"] == ["no_operating_plant"]
    assert "no_financials" in others["GAMMA"]["forecast_flags"] and others["GAMMA"]["tax_status"] == "No data"


def test_enrich_forecast_holds_income_flat_when_the_calibration_was_clipped(db, income_world):
    """Found on real data: HURJA/CHCL/BHDC/MAKAR/CHL had a clipped calibration, so forecast income sat far from last
    year's sales and the forecast EPS fell or collapsed. A clipped company has no trustworthy income change."""
    from src.analytics import income as inc
    from src.models import CompanyFinancial
    with db.session_scope() as s:                                  # reported sales far below the model: calibration clips
        s.query(CompanyFinancial).filter_by(company_id=income_world["alpha"], quarter=4).one().electricity_sales_npr = 10e6
    with db.session_scope() as s:
        out = fc.enrich_forecast(s, inc.forecast(s))
    alpha = next(c for c in out["companies"] if c["symbol"] == "ALPHA")
    assert "calibration_clipped" in alpha["flags"] and alpha["annual_income_npr"] > 10e6      # the income itself is untouched
    assert "flat_income" in alpha["forecast_flags"]
    assert alpha["eps_forecast"] == pytest.approx(20.0 + 4e6 * 0.8 / 10e6)                    # only the finance cost moves


def test_enrich_forecast_flags_income_that_is_not_anchored_on_reported_sales(db, income_world):
    from src.analytics import income as inc
    from src.models import Project
    with db.session_scope() as s:
        s.query(Project).filter_by(project_id="HP_2").one().commissioning_year = 2025       # started in the reported year
    with db.session_scope() as s:
        out = fc.enrich_forecast(s, inc.forecast(s))
    alpha = next(c for c in out["companies"] if c["symbol"] == "ALPHA")
    assert "partial_year" in alpha["flags"] and "income_not_anchored" in alpha["forecast_flags"]
    assert alpha["eps_forecast"] is not None                                                  # still forecast, but flagged


def test_enrich_forecast_keeps_listed_companies_that_have_financials_but_no_plant(db, income_world):
    from src.analytics import income as inc
    from src.models import Company, CompanyFinancial
    with db.session_scope() as s:
        c = Company(company_name="Delta Hydro Ltd", nepse_listed=True, stock_symbol="DELTA")
        s.add(c)
        s.flush()
        s.add(CompanyFinancial(company_id=c.company_id, fiscal_year="2082/83", quarter=4, paid_up_capital_npr=100e6,
                               loans_npr=50e6, finance_cost_npr=5e6, net_profit_npr=-3e6, tax_provision_npr=0.0,
                               eps=-3.0, networth_per_share=90.0))
    with db.session_scope() as s:
        out = fc.enrich_forecast(s, inc.forecast(s))
    delta = next(c for c in out["others"] if c["symbol"] == "DELTA")
    assert delta["tax_status"] == "Loss-making" and delta["eps_reported"] == -3.0 and delta["eps_forecast"] is None
    assert "no_operating_plant" in delta["flags"]


def test_impact_lists_every_listed_company_with_its_tax_status(db, income_world):
    from src.models import Company
    with db.session_scope() as s:
        s.add(Company(company_name="Gamma Power Ltd", nepse_listed=True, stock_symbol="GAMMA"))
    with db.session_scope() as s:
        out = fc.impact(s)
    by = {c["symbol"]: c for c in out["companies"]}
    assert set(by) == {"ALPHA", "BETA", "GAMMA"}
    assert by["ALPHA"]["tax_status"] == "Taxed" and by["BETA"]["tax_status"] == "Tax holiday"
    assert by["GAMMA"]["tax_status"] == "No data" and "no_financials" in by["GAMMA"]["flags"] and by["GAMMA"]["scenario"] is None
