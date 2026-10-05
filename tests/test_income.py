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
