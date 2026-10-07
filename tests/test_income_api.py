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
    assert set(body) == {"assumptions", "quarters", "totals", "companies", "others"}
    assert len(body["quarters"]) == 4 and body["companies"][0]["rank"] == 1
    first = body["companies"][0]
    for key in ("wet_usage_pct", "dry_usage_pct", "avg_usage_pct", "annual_income_npr", "quarters", "calibration", "flags"):
        assert key in first
    json.dumps(body, allow_nan=False)           # no NaN/Infinity leaks into the JSON


def test_forecast_endpoint_carries_tax_status_and_forecast_eps_and_book_value(client):
    body = client.get("/api/income/forecast").get_json()
    alpha = next(c for c in body["companies"] if c["symbol"] == "ALPHA")
    for key in ("tax_status", "tax_rate_pct", "eps_reported", "eps_forecast", "bvps_reported", "bvps_forecast",
                "net_profit_forecast", "forecast_flags"):
        assert key in alpha
    assert alpha["tax_status"] == "Taxed" and alpha["eps_forecast"] > alpha["eps_reported"]
    assert body["assumptions"]["repay_pct"] == 8.0 and body["assumptions"]["retention_pct"] == 70.0


def test_forecast_endpoint_takes_repayment_and_retention_controls(client):
    flat = client.get("/api/income/forecast?repay_pct=0&retention_pct=0").get_json()
    alpha = next(c for c in flat["companies"] if c["symbol"] == "ALPHA")
    assert alpha["eps_forecast"] == pytest.approx(alpha["eps_reported"])        # no repayment, income = last year's sales
    assert alpha["bvps_forecast"] == pytest.approx(alpha["bvps_reported"])      # nothing retained


@pytest.mark.parametrize("query", ["repay_pct=-1", "repay_pct=51", "repay_pct=nan", "retention_pct=101",
                                   "retention_pct=abc", "retention_pct=inf"])
def test_forecast_endpoint_rejects_bad_controls(client, query):
    r = client.get("/api/income/forecast?" + query)
    assert r.status_code == 400 and r.get_json()["error"]


def test_every_listed_company_appears_in_the_forecast_and_finance_endpoints(db, income_world):
    from src.models import Company
    with db.session_scope() as s:
        s.add(Company(company_name="Gamma Power Ltd", nepse_listed=True, stock_symbol="GAMMA"))
    app = create_app(db)
    app.config["TESTING"] = True
    c = app.test_client()
    f = c.get("/api/income/forecast").get_json()
    assert {x["symbol"] for x in f["companies"] + f["others"]} == {"ALPHA", "BETA", "GAMMA"}
    assert {x["symbol"] for x in c.get("/api/finance/impact").get_json()["companies"]} == {"ALPHA", "BETA", "GAMMA"}


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


def test_income_page_has_the_sections_and_controls(client):
    html = client.get("/income").get_data(as_text=True)
    for needle in ('id="kpis"', 'id="c-usage"', 'id="c-quarters"', 'id="rank"', 'id="fin-controls"',
                   'name="rate_delta_pp"', 'name="repay_pct"', 'name="retention_pct"', 'name="years"',
                   'id="fin"', 'id="plants"', "estimate"):
        assert needle in html, needle


def test_income_page_shows_tax_status_forecast_eps_book_value_and_says_what_the_forecast_is(client):
    html = client.get("/income").get_data(as_text=True)
    for needle in ("Tax status", "EPS reported → forecast", "Book value/share reported → forecast", 'id="tax-tally"',
                   "All NEPSE-listed hydropower companies", "equals last year's reported sales", "no operating plant",
                   "raw ×"):                       # a clipped calibration shows the unclipped factor too
        assert needle in html, needle


def test_income_page_shows_the_profit_shape_indicator(client):
    """Spec section 1: the shape check is reported on the page, not only in the API."""
    html = client.get("/income").get_data(as_text=True)
    assert "Profit-shape gap" in html and "profit_shape_gap_pp" in html
