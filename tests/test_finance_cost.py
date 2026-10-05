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
