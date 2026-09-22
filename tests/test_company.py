import pytest
from sqlalchemy import func, select

from src import config
from src.analytics import company_profiles as cp
from src.collectors import company_reports as cr
from src.collectors import sharesansar as ss
from src.collectors.manual_import import upsert_projects
from src.dashboard import create_app
from src.models import Company, CompanyFact, CompanyFinancial, CompanyReport
from src.processors import report_extract as rx

QUARTERLY_HTML = """
<table><tr><th>Particulars (Rs. in '000)</th><th>4th Quarter 2082/2083</th></tr>
<tr><td>Paid Up Capital</td><td>1,000,000.00</td></tr><tr><td>Reserve &amp; Surplus</td><td>500,000.00</td></tr>
<tr><td>Loans &amp; Long-Term Liabilities</td><td>3,000,000.00</td></tr>
<tr><td>Property, Plant &amp; Equipment - Net Block (Net Fixed Assets)</td><td>4,000,000.00</td></tr>
<tr><td>Capital Work in Progress</td><td>500,000.00</td></tr></table>
<table><tr><th>Particulars (Rs. in '000)</th><th>4th Quarter 2082/2083</th></tr>
<tr><td>Income from Sales of Electricity</td><td>900,000.00</td></tr><tr><td>Financial Expenses</td><td>250,000.00</td></tr>
<tr><td>Provision for Tax</td><td>0.00</td></tr><tr><td>Net Profit</td><td>300,000.00</td></tr></table>
<table><tr><th>Particulars</th><th>4th Quarter 2082/2083</th></tr>
<tr><td>Networth Per Share</td><td>150.00</td></tr><tr><td>Earnings Per Share (EPS Annualized -Rs.)</td><td>3.00</td></tr>
<tr><td>Return on Equity (ROE %)</td><td>20.00</td></tr></table>"""


# ------------------------------------------------------------------------------- parsing
def test_quarterly_parser_converts_thousands_and_period():
    q = ss.parse_quarterly_html(QUARTERLY_HTML)
    assert (q["fiscal_year"], q["quarter"]) == ("2082/83", 4)
    assert q["loans_npr"] == 3_000_000_000 and q["finance_cost_npr"] == 250_000_000  # Rs '000 -> NPR
    assert q["ppe_npr"] == 4_000_000_000 and q["electricity_sales_npr"] == 900_000_000 and q["eps"] == 3.0
    assert ss.parse_quarterly_html("<p>nothing</p>") is None


def test_profit_headline_and_website_hint():
    h = ss.parse_profit_headline("X Ltd has posted a net profit of Rs 720.05 million and published its 4th quarter "
                                 "company analysis of the fiscal year 2082/83.")
    assert h == {"fiscal_year": "2082/83", "quarter": 4, "net_profit_npr": 720_050_000.0, "published": None}
    assert ss.parse_profit_headline("posted a net loss of Rs 1.2 arba ... 2nd quarter ... 2081/82")["net_profit_npr"] == -1.2e9
    assert ss.parse_profit_headline("Company announces AGM") is None
    assert ss.website_from_email("a@wlink.com.np, info@chilime.com.np") == "https://chilime.com.np"
    assert ss.website_from_email("someone@gmail.com") is None


def test_report_link_classification_and_fiscal_year():
    assert cr.bs_fiscal_year("Annual Report 2080-81") == "2080/81"
    assert cr.bs_fiscal_year("FY 2023-24 annual") == "2080/81"  # Gregorian FY converted to Bikram Sambat
    assert cr.bs_fiscal_year("no year here") is None
    assert cr.classify_link("Annual Report FY 2079/80", "/a.pdf") == ("annual", "2079/80", None)
    assert cr.classify_link("Third Quarter Report 2081/82", "/q.pdf") == ("quarterly", "2081/82", 3)
    assert cr.classify_link("Q2 2080-81", "/x.pdf") == ("quarterly", "2080/81", 2)
    assert cr.classify_link("Citizen charter", "/charter.pdf") is None


def test_fact_extraction_finds_values_with_snippets_and_rejects_implausible():
    text = ("The company signed a PPA with NEA at Rs. 8.40 per unit for dry season energy. "
            "The total project cost of Rs 5.2 billion was funded 70:30. "
            "The company is entitled to a tax holiday of 100% for the first ten years of operation. "
            "The tariff was Rs. 900 per unit in an unrelated typo.")
    facts = rx.extract_from_text(text, page=12)
    by = {f["fact_type"]: f for f in facts}
    assert by["ppa_rate_npr_kwh"]["value_num"] == 8.4 and by["ppa_rate_npr_kwh"]["page"] == 12
    assert "8.40 per unit" in by["ppa_rate_npr_kwh"]["snippet"]
    assert by["project_cost_npr"]["value_num"] == 5.2e9
    assert "tax_holiday_mention" in by
    assert all(f.get("value_num") != 900 for f in facts)  # outside the plausible NPR/kWh range
    assert len(rx.dedupe(facts * 5)) == len(facts)


# ------------------------------------------------------------------------------ storage
def _company(db, symbol="TST", name="Test Hydropower Limited"):
    with db.session_scope() as s:
        c = Company(company_name=name, stock_symbol=symbol, nepse_listed=True)
        s.add(c)
        s.flush()
        return c.company_id


def test_store_financials_is_idempotent_and_keeps_history(db):
    cid = _company(db)
    result = {"quarterly": ss.parse_quarterly_html(QUARTERLY_HTML),
              "history": [{"fiscal_year": "2081/82", "quarter": 4, "net_profit_npr": 250e6, "published": None},
                          {"fiscal_year": "2082/83", "quarter": 4, "net_profit_npr": 299e6, "published": None}]}
    for _ in range(2):
        with db.session_scope() as s:
            ss.store_financials(s, s.get(Company, cid), {**result, "quarterly": ss.parse_quarterly_html(QUARTERLY_HTML)})
    with db.session_scope() as s:
        assert s.scalar(select(func.count()).select_from(CompanyFinancial)) == 2
        latest = s.scalars(select(CompanyFinancial).where(CompanyFinancial.fiscal_year == "2082/83")).one()
        assert latest.net_profit_npr == 300e6  # the full quarterly report overrides the headline figure
        assert latest.loans_npr == 3e9


# ------------------------------------------------------------------------------ profiles
@pytest.fixture()
def profile_db(db):
    cid = _company(db)
    with db.session_scope() as s:
        upsert_projects(s, [
            dict(project_id="HP_1", project_name_en="Alpha", capacity_mw=50, status="Operational", developer="Test Hydropower Limited",
                 commissioning_year=2020),
            dict(project_id="HP_2", project_name_en="Beta", capacity_mw=100, status="Under Construction",
                 developer="Test Hydropower Limited", expected_completion_year=2027, estimated_project_cost_npr=15e9),
            dict(project_id="HP_3", project_name_en="Gamma", capacity_mw=30, status="Licensed", owner="Test Hydropower Limited"),
            dict(project_id="HP_4", project_name_en="Delta", capacity_mw=200, status="Planned", developer="Test Hydropower Limited"),
            dict(project_id="HP_5", project_name_en="Other", capacity_mw=999, status="Operational", developer="Someone Else Ltd")])
        ss.store_financials(s, s.get(Company, cid), {"quarterly": ss.parse_quarterly_html(QUARTERLY_HTML), "history": []})
    return db, cid


def test_profile_groups_projects_by_stage_and_ignores_other_companies(profile_db):
    db, cid = profile_db
    with db.session_scope() as s:
        p = cp.build_profile(s, cid)
    assert p["portfolio"]["mw"] == {"Operational": 50, "Under Construction": 100, "Licensed": 30, "Planned": 200}
    assert [r["project_id"] for r in p["portfolio"]["stages"]["Licensed"]] == ["HP_3"]  # owner role counts too
    assert p["portfolio"]["projects"] == 4


def test_profile_metrics_from_reported_financials(profile_db):
    db, cid = profile_db
    with db.session_scope() as s:
        m = cp.build_profile(s, cid)["metrics"]
    assert m["loans_npr"] == 3e9 and m["finance_cost_npr"] == 250e6
    assert m["debt_to_equity"] == 2.0  # 3.0bn / (1.0bn + 0.5bn)
    assert m["finance_cost_pct_of_sales"] == pytest.approx(27.8, abs=0.1)
    assert m["tax"]["label"] == "Tax-free (holiday)" and m["tax"]["effective_rate_pct"] == 0.0
    assert "not a legal determination" in m["tax"]["basis"]


def test_cost_per_mw_prefers_reported_project_cost_over_capital_proxy(profile_db):
    from src.models import Project
    db, cid = profile_db
    with db.session_scope() as s:
        cpm = cp.build_profile(s, cid)["metrics"]["cost_per_mw"]
    assert cpm["npr"] == 15e9 / 100 and "reported project costs" in cpm["basis"]  # Beta only; Alpha has no cost
    with db.session_scope() as s:  # no project cost left; fixture capital is small -> implausible proxy is hidden
        s.get(Project, "HP_2").estimated_project_cost_npr = None
    with db.session_scope() as s:
        m = cp.build_profile(s, cid)["metrics"]
    assert m["cost_per_mw"] is None and "outside the typical" in m["cost_per_mw_note"]  # 4.5bn / 150 MW = 30m/MW
    with db.session_scope() as s:  # raise loans so the proxy is plausible: (1.0 + 0.5 + 20)bn / 150 MW
        s.scalars(select(CompanyFinancial)).one().loans_npr = 20e9
    with db.session_scope() as s:
        cpm = cp.build_profile(s, cid)["metrics"]["cost_per_mw"]
    assert cpm["npr"] == pytest.approx(21.5e9 / 150) and cpm["basis"].startswith("CAPITAL-EMPLOYED PROXY")


def test_future_scenario_and_taxable_company(profile_db):
    db, cid = profile_db
    with db.session_scope() as s:
        f = cp.build_profile(s, cid)["future"]
    assert f["pipeline_mw"] == 130 and f["capacity_if_pipeline_lands_mw"] == 180 and f["capacity_multiple"] == 3.6
    assert "ASSUMED" in f["revenue_per_mw_basis"] or "reported" in f["revenue_per_mw_basis"]
    with db.session_scope() as s:  # a taxpaying profile: provision is 20% of pre-tax profit
        row = s.scalars(select(CompanyFinancial)).one()
        row.tax_provision_npr, row.net_profit_npr = 75e6, 300e6
    with db.session_scope() as s:
        tax = cp.build_profile(s, cid)["metrics"]["tax"]
    assert tax["label"] == "Taxable" and tax["effective_rate_pct"] == 20.0


def test_profile_of_company_without_financials_says_unknown(db):
    cid = _company(db, "NOFIN", "Nofin Ltd")
    with db.session_scope() as s:
        p = cp.build_profile(s, cid)
    assert p["metrics"]["tax"]["label"] == "Unknown" and p["metrics"]["loans_npr"] is None
    assert p["metrics"]["cost_per_mw"] is None and p["reports"] == [] and p["facts"] == []


def test_only_verified_ppa_rate_is_the_headline_unverified_is_a_candidate(profile_db):
    db, cid = profile_db
    with db.session_scope() as s:
        for v in (8.4, 8.4, 4.8):
            s.add(CompanyFact(company_id=cid, fact_type="ppa_rate_npr_kwh", value_num=v, unit="NPR/kWh", fiscal_year="2081/82",
                              page=10, snippet="PPA at Rs 8.40 per unit", method="auto"))
    with db.session_scope() as s:
        m = cp.build_profile(s, cid)["metrics"]
    assert m["ppa_rate"] is None  # regex candidates never become the headline number
    assert m["ppa_candidate"]["npr_per_kwh"] == 8.4  # most frequently reported value, shown as a candidate to check
    with db.session_scope() as s:
        s.add(CompanyFact(company_id=cid, fact_type="ppa_rate_npr_kwh", value_num=7.9, fiscal_year="2081/82", method="manual",
                          verified=True, snippet="Annual report p.31"))
    with db.session_scope() as s:
        m = cp.build_profile(s, cid)["metrics"]
    assert m["ppa_rate"]["npr_per_kwh"] == 7.9 and m["ppa_rate"]["verified"] is True and m["ppa_candidate"] is None


# ---------------------------------------------------------------------------------- web
def test_company_pages_and_profile_api(profile_db, tmp_path, monkeypatch):
    db, cid = profile_db
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "reports").mkdir()
    pdf = tmp_path / "reports" / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    with db.session_scope() as s:
        rep = CompanyReport(company_id=cid, kind="annual", fiscal_year="2080/81", source_url="http://x/a.pdf",
                            local_path="reports/a.pdf", status="downloaded")
        evil = CompanyReport(company_id=cid, kind="annual", source_url="http://x/b.pdf", local_path="../../secret.txt", status="downloaded")
        s.add_all([rep, evil])
        s.flush()
        rid, eid = rep.report_id, evil.report_id
    client = create_app(db).test_client()  # created first so it still finds the real templates folder
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)  # report files are then resolved under the temp dir
    for path in ("/companies", f"/companies/{cid}"):
        assert client.get(path).status_code == 200
    body = client.get(f"/api/companies/{cid}/profile").get_data(as_text=True)
    assert "NaN" not in body and "Infinity" not in body
    prof = client.get("/api/companies/profiles").get_json()
    assert prof[0]["symbol"] == "TST" and prof[0]["loans_npr"] == 3e9 and prof[0]["operational_mw"] == 50
    assert client.get("/api/companies/profiles?scope=bogus").status_code == 400
    assert client.get("/api/companies/99999/profile").status_code == 404
    assert client.get(f"/reports/{rid}/file").data.startswith(b"%PDF")
    assert client.get(f"/reports/{eid}/file").status_code == 404  # path escaping the data dir is refused
    assert client.get("/reports/424242/file").status_code == 404


def test_website_candidates_are_derived_from_distinctive_name_parts():
    doms = cr.candidate_domains("Chilime Hydropower Company Limited")
    assert doms[0] == "https://chilime.com.np" and "https://chilimehydropower.com.np" in doms
    short = cr.candidate_domains("Api Power Company Pvt. Ltd", "API")
    assert "https://api.com.np" in short and "https://apipower.com.np" in short and "https://api.org.np" in short  # 3-letter names and symbols are tried
    words, core = cr.name_tokens_for_domain("Upper Solu Hydro Electric Company Limited")
    assert core == ["upper", "solu"] and "limited" not in words


def test_search_results_exclude_aggregators_and_parked_pages(monkeypatch):
    class R:
        text = """<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fmerolagani.com%2Fx">a</a>
                  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Futkhpl.org.np%2F">b</a>
                  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.sharesansar.com%2Fc">c</a>
                  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fmiddletamor.com%2F">d</a>"""
    monkeypatch.setattr(cr.requests, "post", lambda *a, **k: R())
    assert cr.search_candidates("Upper Tamakoshi Hydropower Limited", pause=0) == ["utkhpl.org.np", "middletamor.com"]

    class Page:
        ok, url, headers = True, "https://x.com/", {"content-type": "text/html"}
        def __init__(self, body): self.text = body
    parked = Page("<html><title>x.com</title>" + "Buy this domain. Hydro power energy. " * 30 + "</html>")
    monkeypatch.setattr(cr.requests, "get", lambda *a, **k: parked)
    assert cr._verify_site("https://x.com", "Sanima Middle Tamor Hydropower Ltd") is None  # parked domain
    real = Page("<html><title>Middle Tamor Hydropower</title>" + "Middle Tamor hydropower project, 73 MW power plant, Taplejung. " * 15 + "</html>")
    monkeypatch.setattr(cr.requests, "get", lambda *a, **k: real)
    assert cr._verify_site("https://x.com", "Sanima Middle Tamor Hydropower Ltd", min_frac=0.6) == "https://x.com"  # brand omits 'Sanima'
    assert cr._verify_site("https://x.com", "Sanima Middle Tamor Hydropower Ltd") is None  # strict mode needs every token


# ------------------------------------------------------------------- geographic zones
def test_every_district_has_exactly_one_ecological_belt():
    from collections import Counter

    from src.processors.geo import ALL_DISTRICTS, MOUNTAIN, TERAI, ecological_belt
    assert len(ALL_DISTRICTS) == 77 and MOUNTAIN <= ALL_DISTRICTS and TERAI <= ALL_DISTRICTS and not (MOUNTAIN & TERAI)
    assert all(ecological_belt(d) in ("Mountain", "Hill", "Terai") for d in ALL_DISTRICTS)
    assert Counter(ecological_belt(d) for d in ALL_DISTRICTS) == {"Mountain": 16, "Hill": 40, "Terai": 21}
    assert (ecological_belt("Dolakha"), ecological_belt("Kathmandu"), ecological_belt("Chitwan")) == ("Mountain", "Hill", "Terai")
    assert ecological_belt(None) is None and ecological_belt("Atlantis") is None  # unknown stays unknown, never guessed


def test_profile_geo_groups_projects_by_belt_and_province_and_counts_unmapped(db):
    cid = _company(db, "GEO", "GEO Hydropower Limited")
    with db.session_scope() as s:
        upsert_projects(s, [
            dict(project_id="G1", project_name_en="Hilltop", capacity_mw=40, status="Operational", district="Syangja",
                 province="Gandaki", latitude=28.0, longitude=83.6, developer="GEO Hydropower Limited"),
            dict(project_id="G2", project_name_en="Highland", capacity_mw=60, status="Under Construction", district="Dolakha",
                 province="Bagmati", latitude=27.6, longitude=86.2, developer="GEO Hydropower Limited"),
            dict(project_id="G3", project_name_en="Plains", capacity_mw=10, status="Licensed", district="Chitwan", province="Bagmati",
                 developer="GEO Hydropower Limited"),  # no coordinates
            dict(project_id="G4", project_name_en="Nowhere", capacity_mw=5, status="Planned", developer="GEO Hydropower Limited")])
    with db.session_scope() as s:
        g = cp.build_profile(s, cid)["geo"]
    belts = {z["zone"]: z for z in g["by_belt"]}
    assert [z["zone"] for z in g["by_belt"]] == ["Mountain", "Hill", "Terai", "Unknown"]  # fixed order, unknowns kept last
    assert belts["Hill"]["Operational"] == 40 and belts["Mountain"]["Under Construction"] == 60 and belts["Terai"]["Licensed"] == 10
    assert belts["Unknown"]["projects"] == 1 and belts["Unknown"]["Planned"] == 5  # a project with no district is shown, not dropped
    prov = {z["zone"]: z for z in g["by_province"]}
    assert prov["Bagmati"]["projects"] == 2 and prov["Bagmati"]["total_mw"] == 70
    assert (g["mapped"], g["unmapped"]) == (2, 2)


# ------------------------------------------------------------------ reviewing extracted facts
def test_verified_cost_linked_to_a_project_feeds_cost_per_mw_even_from_another_companys_report(db):
    owner = _company(db, "OWN", "Own Hydropower Limited")
    other = _company(db, "OTH", "Other Holdings Limited")
    with db.session_scope() as s:
        upsert_projects(s, [dict(project_id="P1", project_name_en="Plant One", capacity_mw=40, status="Operational",
                                 developer="Own Hydropower Limited")])
        for value, verified, fy in ((9.0e9, False, "2082/83"), (8.0e9, True, "2080/81"), (7.0e9, True, "2081/82")):
            s.add(CompanyFact(company_id=other, project_id="P1", fact_type="project_cost_npr", value_num=value, fiscal_year=fy,
                              page=5, verified=verified, method="manual" if verified else "auto",
                              value_text="revised cost, excluding interest during construction" if verified else None))
        s.add(CompanyFact(company_id=other, project_id="P1", fact_type="rejected_project_cost_npr", value_num=1e9, fiscal_year="2083/84"))
    with db.session_scope() as s:
        p = cp.build_profile(s, owner)
    row = p["portfolio"]["stages"]["Operational"][0]
    assert row["cost_npr"] == 7.0e9 and row["cost_per_mw_npr"] == 175e6  # newest VERIFIED figure; the unverified 9.0bn and the rejected one are ignored
    assert row["cost_basis"].startswith("verified from a report (FY 2081/82, p.5)") and "excluding interest" in row["cost_basis"]
    assert "excluding interest" in p["metrics"]["cost_per_mw"]["basis"]  # the caveat travels to the headline number


def test_verify_and_reject_commands_record_decisions(db, monkeypatch):
    from click.testing import CliRunner

    from src import cli_reports
    cid = _company(db, "REV", "Rev Hydropower Limited")
    with db.session_scope() as s:
        upsert_projects(s, [dict(project_id="R1", project_name_en="Plant", capacity_mw=10, status="Operational")])
        a = CompanyFact(company_id=cid, fact_type="project_cost_npr", value_num=2e9, method="auto")
        b = CompanyFact(company_id=cid, fact_type="ppa_rate_npr_kwh", value_num=10.8, method="auto")
        s.add_all([a, b])
        s.flush()
        ida, idb = a.fact_id, b.fact_id
    monkeypatch.setattr(cli_reports, "db_manager", db)
    run = CliRunner().invoke
    assert run(cli_reports.reports, ["verify-fact", str(ida), "--project", "NOPE", "--note", "x"]).exit_code != 0  # unknown project refused
    assert run(cli_reports.reports, ["verify-fact", str(ida), "--project", "R1", "--note", "table on p.4"]).exit_code == 0
    assert run(cli_reports.reports, ["reject-fact", str(idb), "--reason", "retail tariff"]).exit_code == 0
    with db.session_scope() as s:
        fa, fb = s.get(CompanyFact, ida), s.get(CompanyFact, idb)
        assert fa.verified and fa.project_id == "R1" and fa.method == "manual (reviewed)" and fa.value_text == "table on p.4"
        assert fb.fact_type == "rejected_ppa_rate_npr_kwh" and not fb.verified and fb.value_text == "retail tariff"
    with db.session_scope() as s:
        m = cp.build_profile(s, cid)["metrics"]
    assert m["ppa_rate"] is None and m["ppa_candidate"] is None  # a rejected value is not even offered as a candidate


def test_quarterly_response_saying_no_data_is_treated_as_no_report():
    """Newly listed companies (SGHL, YMHL, ULHC, KAHL) get ShareSansar's 'No data available' tables: no report, not an error."""
    html = ('<div class="tab-content"><table><thead><tr><th>Particulars</th><th>Duration</th></tr></thead>'
            '<tbody><tr><td colspan="2">No data available</td></tr></tbody></table></div>')
    assert ss.parse_quarterly_html(html) is None


def test_verified_seasonal_ppa_is_shown_as_wet_and_dry_with_a_blended_table_figure(profile_db):
    db, cid = profile_db
    with db.session_scope() as s:
        for ftype, val, text in (("ppa_wet_npr_kwh", 3.63, None), ("ppa_dry_npr_kwh", 6.96, None),
                                 ("ppa_escalation", None, "3% a year for nine years after COD")):
            s.add(CompanyFact(company_id=cid, fact_type=ftype, value_num=val, value_text=text, verified=True, method="manual",
                              fiscal_year="2080/81", page=2, snippet="rating report p.2"))
        s.add(CompanyFact(company_id=cid, fact_type="ppa_rate_npr_kwh", value_num=99.0, verified=False, method="auto"))  # candidate ignored
    with db.session_scope() as s:
        m = cp.build_profile(s, cid)["metrics"]
        row = next(r for r in cp.list_profiles(s) if r["company_id"] == cid)
    ppa = m["ppa_rate"]
    assert (ppa["wet"], ppa["dry"]) == (3.63, 6.96) and ppa["text"] == "wet NPR 3.63 / dry NPR 6.96 per kWh"
    assert ppa["npr_per_kwh"] == pytest.approx(3.63 * 0.7 + 6.96 * 0.3, abs=0.01) and "nine years" in ppa["escalation"]
    assert m["ppa_candidate"] is None  # a verified rate exists, so the unverified candidate is not offered
    assert row["ppa_text"] == ppa["text"] and row["ppa_rate"] == ppa["npr_per_kwh"]
