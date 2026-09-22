import pytest
from sqlalchemy import select

from src.analytics import load_projects_df
from src.analytics import unknowns as unk
from src.collectors.manual_import import upsert_projects
from src.dashboard import create_app
from src.models import Project, ProjectType
from src.processors import location_infer as li


def _load(db, recs, reliability="wikipedia"):
    with db.session_scope() as s:
        upsert_projects(s, recs, reliability)


def _run(db, **kw):
    with db.session_scope() as s:
        return li.infer(s, geocode=False, **kw)


def test_district_river_and_type_come_only_from_explicit_words_in_the_name(db):
    _load(db, [dict(project_id="H1", project_name_en="Humla Karnali-2 HEP", capacity_mw=335, status="Planned"),
               dict(project_id="H2", project_name_en="Uttarganga Storage Hydropower Project", capacity_mw=828, status="Planned"),
               dict(project_id="H3", project_name_en="Foo Khola PRoR Cascade HEP", capacity_mw=30, status="Planned"),
               dict(project_id="H4", project_name_en="Mystery Project", capacity_mw=50, status="Planned")])
    rep = _run(db)
    with db.session_scope() as s:
        p = {x.project_id: x for x in s.scalars(select(Project))}
        assert p["H1"].district.district_name == "Humla" and p["H1"].province == "Karnali" and p["H1"].river_name_raw == "Karnali"
        assert p["H2"].project_type == ProjectType.STORAGE and p["H3"].project_type == ProjectType.PEAKING_ROR
        assert "Humla" in p["H1"].inference_note and "storage" in p["H2"].inference_note  # every inference says how it was made
        assert p["H4"].district_id is None and p["H4"].province is None and p["H4"].project_type is None and p["H4"].inference_note is None
    assert rep.counts["district named in project name"] == 1


def test_known_values_are_never_overwritten_and_rerun_is_a_no_op(db):
    _load(db, [dict(project_id="K1", project_name_en="Humla Karnali Storage HEP", capacity_mw=100, status="Operational",
                    district="Dolakha", province="Bagmati", project_type="Run-of-River", river="Tamakoshi")], "doed")
    _run(db)
    with db.session_scope() as s:
        p = s.get(Project, "K1")
        assert p.district.district_name == "Dolakha" and p.project_type == ProjectType.RUN_OF_RIVER  # name says Humla/Storage: ignored
        assert p.inference_note is None
    assert _run(db).counts == {}  # nothing left to infer


def test_location_copied_from_same_named_doed_project_but_not_across_upper_lower(db):
    _load(db, [dict(project_id="D1", project_name_en="Chujung Khola HEP", capacity_mw=10, status="Licensed", district="Sankhuwasabha",
                    latitude=27.5, longitude=87.2, river="Chujung")], "doed")
    _load(db, [dict(project_id="W1", project_name_en="Chujung Khola HEP", capacity_mw=14, status="Planned"),
               dict(project_id="W2", project_name_en="Upper Chujung Khola HEP", capacity_mw=14, status="Planned")])
    _run(db)
    with db.session_scope() as s:
        w1, w2 = s.get(Project, "W1"), s.get(Project, "W2")
        assert w1.district.district_name == "Sankhuwasabha" and w1.province == "Koshi" and w1.latitude == 27.5
        assert "same name" in w1.inference_note and "D1" in w1.inference_note
        assert w2.district_id is None  # 'Upper' is a different project


def test_curated_and_manual_override_locations(db, tmp_path, monkeypatch):
    _load(db, [dict(project_id="C1", project_name_en="Pancheshwar Multipurpose Project", capacity_mw=3240, status="Planned"),
               dict(project_id="C2", project_name_en="Some Unnamed HEP", capacity_mw=20, status="Planned")])
    override = tmp_path / "ov.csv"
    override.write_text("project_id,district,province,river,source\nC2,Ilam,,Mai,DoED licence page 12\n", encoding="utf-8")
    monkeypatch.setattr(li, "OVERRIDES", override)
    _run(db)
    with db.session_scope() as s:
        c1, c2 = s.get(Project, "C1"), s.get(Project, "C2")
        assert c1.province == "Sudurpashchim" and c1.district_id is None and c1.river_name_raw == "Mahakali"
        assert c2.district.district_name == "Ilam" and c2.province == "Koshi" and "DoED licence page 12" in c2.inference_note


def test_dry_run_changes_nothing(db):
    _load(db, [dict(project_id="X1", project_name_en="Humla Karnali HEP", capacity_mw=10, status="Planned")])
    assert _run(db, dry_run=True).counts
    with db.session_scope() as s:
        assert s.get(Project, "X1").district_id is None


def test_unknown_summary_explains_the_causes(db):
    _load(db, [dict(project_id="U1", project_name_en="No Location HEP", capacity_mw=500, status="Planned"),
               dict(project_id="U2", project_name_en="Located HEP", capacity_mw=100, status="Planned", district="Ilam", province="Koshi", river="Mai",
                    developer="Mai Power Ltd", project_type="Run-of-River")], "doed")
    with db.session_scope() as s:
        df = load_projects_df(s)
    by = {r["dimension"]: r for r in unk.summary(df)}
    assert by["province"]["projects"] == 1 and by["province"]["mw"] == 500 and by["province"]["share_of_mw_pct"] == pytest.approx(83.3, abs=0.1)
    assert by["province"]["by_source"][0]["source"].startswith("DoED") and by["type"]["projects"] == 1
    assert unk.unknown_projects(df, "province")["project_id"].tolist() == ["U1"]
    client = create_app(db).test_client()
    body = client.get("/api/analytics/unknowns?by=river").get_json()
    assert [x["project_id"] for x in body["projects"]] == ["U1"] and len(body["summary"]) == 5
    assert client.get("/api/analytics/unknowns?by=bogus").status_code == 400


def test_project_bank_location_needs_same_name_similar_capacity_and_one_place(db):
    _load(db, [dict(project_id="B1", project_name_en="Upper Marsyangdi-2", capacity_mw=600, status="Planned"),
               dict(project_id="B2", project_name_en="Bheri-1 HEP", capacity_mw=270, status="Planned"),
               dict(project_id="B3", project_name_en="Upper Foo HEP", capacity_mw=50, status="Planned"),
               dict(project_id="B4", project_name_en="Dual Khola HEP", capacity_mw=20, status="Planned")])
    bank = [
        dict(name="Upper Marsyangdi -2", capacity_mw=600.0, river="Marsyangdi", latitude=28.4, longitude=84.5, district="Lamjung", source="DoED test list"),
        dict(name="Bheri-1 HEP", capacity_mw=900.0, river="Bheri", latitude=28.6, longitude=82.2, district="Jajarkot", source="DoED test list"),  # capacity far off
        dict(name="Lower Foo HEP", capacity_mw=50.0, river=None, latitude=None, longitude=None, district="Ilam", source="DoED test list"),  # 'Lower' != 'Upper'
        dict(name="Dual Khola HEP", capacity_mw=20.0, river=None, latitude=None, longitude=None, district="Ilam", source="DoED test list"),
        dict(name="Dual Khola HEP", capacity_mw=20.0, river=None, latitude=None, longitude=None, district="Dolpa", source="DoED test list"),  # two places
    ]
    rep = _run(db, bank=bank)
    with db.session_scope() as s:
        b = {x.project_id: x for x in s.scalars(select(Project))}
        assert b["B1"].district.district_name == "Lamjung" and b["B1"].province == "Gandaki" and b["B1"].latitude == 28.4
        assert b["B1"].river_name_raw == "Marsyangdi" and "DoED test list" in b["B1"].inference_note
        assert b["B2"].district_id is None  # capacity 270 vs 900 MW: not the same project
        assert b["B3"].district_id is None  # Upper vs Lower
        assert b["B4"].district_id is None  # the same name points at two districts: left Unknown, not guessed
    assert rep.counts["DoED project-bank list (same name and capacity)"] == 1
