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
