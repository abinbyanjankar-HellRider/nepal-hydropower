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
