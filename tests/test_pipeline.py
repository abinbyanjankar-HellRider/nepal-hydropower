from datetime import date

import pytest
from sqlalchemy import func, select

from src.collectors.manual_import import import_financials_csv, import_projects_csv, upsert_projects
from src.collectors.news_scraper import attach_news
from src.collectors.sync import sync_records
from src.models import Company, Project, ProjectFinancial, ProjectStatus, ProjectUpdate
from src.processors.validators import validate_database, validate_project_data
from src.seed_data import SEED_PROJECTS


def _count(s, model):
    return s.scalar(select(func.count()).select_from(model))


def test_reference_data_is_idempotent(db):
    assert db.init_reference_data() == {"districts": 0, "rivers": 0, "companies": 0}
    assert db.get_db_stats()["districts"] == 77


def test_seed_load_is_idempotent(db):
    with db.session_scope() as s:
        first = upsert_projects(s, SEED_PROJECTS, "seed-unverified")
    with db.session_scope() as s:
        second = upsert_projects(s, SEED_PROJECTS, "seed-unverified")
        assert _count(s, Project) == len(SEED_PROJECTS)
    assert first["created"] == len(SEED_PROJECTS) and not first["errors"]
    assert second["created"] == 0 and second["updated"] == len(SEED_PROJECTS)


def test_companies_deduplicated_by_normalised_name(db):
    recs = [{"project_id": "HP_900", "project_name_en": "A", "developer": "Himal Power Ltd."},
            {"project_id": "HP_901", "project_name_en": "B", "developer": "Himal Power Limited"}]
    with db.session_scope() as s:
        upsert_projects(s, recs)
        himal = s.scalars(select(Company).where(Company.company_name.like("Himal%"))).all()
        assert len(himal) == 1  # 'Himal Power Limited' from reference data is reused


def test_csv_import_projects_and_financials(db, tmp_path):
    p = tmp_path / "p.csv"
    p.write_text("project_id,project_name_en,capacity_mw,status,district,commissioning_date,grid_connected\n"
                 "HP_950,Test Khola,12.5,Operational,Dolakha,2020-05-01,yes\n"
                 "HP_951,Bad Khola,abc,Nonsense,,,\n", encoding="utf-8")
    f = tmp_path / "f.csv"
    f.write_text("project_id,fiscal_year,energy_generated_gwh,revenue_npr\nHP_950,2080/81,55.2,300000000\n"
                 "HP_404,2080/81,1,1\n", encoding="utf-8")
    with db.session_scope() as s:
        r = import_projects_csv(s, p)
        assert r["created"] == 1 and len(r["errors"]) == 1  # the bad row is reported, not fatal
        proj = s.get(Project, "HP_950")
        assert proj.status == ProjectStatus.OPERATIONAL and proj.commissioning_date == date(2020, 5, 1)
        assert proj.grid_connected is True and proj.district.district_name == "Dolakha"
        fr = import_financials_csv(s, f)
        assert fr["created"] == 1 and len(fr["errors"]) == 1  # unknown project_id reported
        assert s.scalar(select(ProjectFinancial.revenue_npr)) == 300000000


def _rec(name, cap, stage, status, **kw):
    return {"project_name_en": name, "capacity_mw": cap, "status": status, "_stage": stage,
            "data_source": stage, **kw}


def test_sync_merges_across_sources_without_duplicates(db):
    doed = [_rec("Kali Gandaki A", 144.0, "powerplants", ProjectStatus.OPERATIONAL, district="Syangja",
                 license_number="12", developer="NEA")]
    wiki = [_rec("Kaligandaki A Hydroelectric Power Station", 144.0, "wikipedia", ProjectStatus.OPERATIONAL,
                 commissioning_year=2002, province="Gandaki")]
    with db.session_scope() as s:
        res = sync_records(s, doed + wiki)
        assert res.created == 1 and res.updated == 1
        p = s.scalars(select(Project)).one()
        assert p.license_number == "12" and p.commissioning_year == 2002 and p.province == "Gandaki"
        assert p.data_reliability == "doed"


def test_sync_never_merges_upper_and_lower_variants(db):
    recs = [_rec("Tamor Upper", 285.0, "survey", ProjectStatus.PLANNED),
            _rec("Tamor Lower", 280.0, "survey", ProjectStatus.PLANNED)]
    with db.session_scope() as s:
        assert sync_records(s, recs).created == 2


def test_sync_stage_only_moves_forward_and_reports_conflicts(db):
    first = [_rec("Foo Khola", 10.0, "powerplants", ProjectStatus.OPERATIONAL, commissioning_year=2015)]
    second = [_rec("Foo Khola", 10.0, "survey", ProjectStatus.PLANNED),
              _rec("Foo Khola", 10.0, "wikipedia", ProjectStatus.UNDER_CONSTRUCTION, commissioning_year=2019)]
    with db.session_scope() as s:
        sync_records(s, first)
    with db.session_scope() as s:
        res = sync_records(s, second)
        p = s.scalars(select(Project)).one()
        assert p.status == ProjectStatus.OPERATIONAL  # not downgraded
        assert p.commissioning_year == 2015  # Wikipedia only fills blanks
        assert any("commissioning_year 2015 vs 2019" in c for c in res.conflicts)


def test_sync_is_idempotent(db):
    recs = lambda: [_rec("Bar Khola", 5.0, "generation", ProjectStatus.LICENSED, district="Ilam")]
    with db.session_scope() as s:
        sync_records(s, recs())
    with db.session_scope() as s:
        res = sync_records(s, recs())
        assert res.created == 0 and _count(s, Project) == 1


def test_validators(db):
    assert validate_project_data({"project_name_en": "x", "capacity_mw": 10, "latitude": 28, "longitude": 84}) == []
    errs = validate_project_data({"project_name_en": "", "capacity_mw": 99999, "latitude": 10, "longitude": 84})
    assert len(errs) == 3
    with db.session_scope() as s:
        upsert_projects(s, [{"project_id": "HP_1", "project_name_en": "Op Khola", "status": "Operational",
                             "capacity_mw": 5}])
        codes = {i.code for i in validate_database(s)}
    assert {"operational-no-cod", "missing-coordinates", "missing-district"} <= codes


def test_news_attaches_to_most_specific_project_only(db):
    recs = [{"project_id": "HP_1", "project_name_en": "Trishuli"},
            {"project_id": "HP_2", "project_name_en": "Upper Trishuli-1"},
            {"project_id": "HP_3", "project_name_en": "Mai"}]  # too short/ambiguous to match
    items = [{"title": "Tunnel collapse at Upper Trishuli-1 hydropower project", "summary": "Mai river flooding",
              "url": "http://x/1", "date": date(2026, 9, 1), "source": "T"}]
    with db.session_scope() as s:
        upsert_projects(s, recs)
        out = attach_news(s, items)
        assert out["updates_added"] == 1
        assert s.scalars(select(ProjectUpdate)).one().project_id == "HP_2"
        assert attach_news(s, items)["updates_added"] == 0  # de-duplicated on re-run


def test_news_attaches_to_nepse_company_by_name_or_symbol(db):
    with db.session_scope() as s:
        s.add(Company(company_name="Api Power Company Limited", nepse_listed=True, stock_symbol="API"))
    with db.session_scope() as s:
        company_id = s.scalar(select(Company.company_id).where(Company.company_name == "Api Power Company Limited"))
    items = [
        {"title": "Api Power Company posts higher profit", "summary": "the hydropower developer's shares gained",
         "url": "http://x/2", "date": date(2026, 9, 2), "source": "T"},
        {"title": "NEPSE closes flat", "summary": "API was among the top hydropower gainers today",
         "url": "http://x/3", "date": date(2026, 9, 3), "source": "T"},
    ]
    with db.session_scope() as s:
        out = attach_news(s, items)
        assert out["updates_added"] == 2
        rows = s.scalars(select(ProjectUpdate).where(ProjectUpdate.company_id == company_id)).all()
        assert len(rows) == 2 and all(r.project_id is None for r in rows)
        assert attach_news(s, items)["updates_added"] == 0  # de-duplicated on re-run


def test_news_company_hit_skipped_when_covered_by_its_own_matched_project(db):
    with db.session_scope() as s:
        c = Company(company_name="Api Power Company Limited", nepse_listed=True, stock_symbol="API")
        s.add(c)
        s.flush()
        upsert_projects(s, [{"project_id": "HP_9", "project_name_en": "Sanjen Khola",
                             "developer_company_id": c.company_id}])
    items = [{"title": "Api Power's Sanjen Khola project reaches financial closure",
              "summary": "hydropower financing", "url": "http://x/4", "date": date(2026, 9, 4), "source": "T"}]
    with db.session_scope() as s:
        out = attach_news(s, items)
        assert out["updates_added"] == 1  # only the project-linked row, not a duplicate company-linked row
        rows = s.scalars(select(ProjectUpdate)).all()
        assert len(rows) == 1 and rows[0].project_id == "HP_9" and rows[0].company_id is None


def test_sync_matches_on_licence_number_even_when_names_differ(db):
    a = [_rec("Khimti Hydro", 60.0, "generation", ProjectStatus.LICENSED, license_number="1", license_type="Generation")]
    b = [_rec("Khimti I HEP", 60.0, "powerplants", ProjectStatus.OPERATIONAL, license_number="1",
              license_type="Generation")]
    with db.session_scope() as s:
        sync_records(s, a)
    with db.session_scope() as s:
        res = sync_records(s, b)
        assert res.created == 0 and _count(s, Project) == 1
        assert s.scalars(select(Project)).one().status == ProjectStatus.OPERATIONAL


def test_sync_does_not_merge_a_base_name_into_a_numbered_cascade(db):
    """Regression: seed 'Upper Bhotekoshi' was once absorbed by the unrelated licensed 'Bhotekoshi 5 HEP',
    so the real plant was re-created as a duplicate on the next sync."""
    with db.session_scope() as s:
        upsert_projects(s, [{"project_id": "HP_010", "project_name_en": "Upper Bhotekoshi", "capacity_mw": 45.0,
                             "status": "Operational", "district": "Sindhupalchok"}], "seed-unverified")
    recs = lambda: [
        _rec("Bhotekoshi 5 HEP", 46.0, "generation", ProjectStatus.LICENSED, district="Sindhupalchok",
             license_number="303", license_type="Generation"),
        _rec("Upper Bhotekoshi", 45.0, "powerplants", ProjectStatus.OPERATIONAL, district="Sindhupalchok",
             license_number="5", license_type="Generation"),
    ]
    with db.session_scope() as s:
        sync_records(s, recs())
    with db.session_scope() as s:
        again = sync_records(s, recs())
        assert again.created == 0  # idempotent
        rows = {p.project_name_en: p for p in s.scalars(select(Project))}
        assert set(rows) == {"Upper Bhotekoshi", "Bhotekoshi 5 HEP"}
        assert rows["Upper Bhotekoshi"].project_id == "HP_010" and rows["Upper Bhotekoshi"].license_number == "5"
        assert rows["Bhotekoshi 5 HEP"].status == ProjectStatus.LICENSED


def test_sync_keeps_projects_with_different_licence_numbers_apart_even_when_names_and_capacity_are_close(db):
    """DoED lists a 25 MW 'Seti Khola HPP' (licence 304) and a 22 MW 'Seti Khola HEP' (licence 334): two different companies' projects."""
    records = [
        _rec("Seti Khola HPP", 25.0, "powerplants", ProjectStatus.OPERATIONAL, district="Kaski", license_number="304",
             license_type="Generation", developer="Vision Lumbini Urja Company Limited."),
        _rec("Seti Khola HEP", 22.0, "generation", ProjectStatus.LICENSED, district="Kaski", license_number="334",
             license_type="Generation", developer="Setikhola Hydropower Ltd."),
    ]
    with db.session_scope() as s:
        res = sync_records(s, records)
        assert res.created == 2
        rows = {p.license_number: p for p in s.scalars(select(Project))}
        assert rows["304"].status == ProjectStatus.OPERATIONAL and rows["334"].status == ProjectStatus.LICENSED
    with db.session_scope() as s:  # and a second sync stays stable
        assert sync_records(s, records).created == 0


def test_wikipedia_row_with_same_name_and_district_merges_despite_a_different_design_capacity(db):
    doed = [_rec("Bhotekoshi 5 HEP", 46.0, "generation", ProjectStatus.LICENSED, district="Sindhupalchok", license_number="303",
                 license_type="Generation", developer="Kalika Energy Ltd.")]
    wiki = [_rec("Bhotekoshi 5 HEP", 62.0, "wikipedia", ProjectStatus.UNDER_CONSTRUCTION, district="Sindhupalchok"),
            _rec("Bhotekoshi 5 HEP", 62.0, "wikipedia", ProjectStatus.UNDER_CONSTRUCTION, district="Dolakha")]
    with db.session_scope() as s:
        res = sync_records(s, doed + wiki)
        assert res.created == 2  # the licence-303 project and the same-named project in a different district
        p = s.scalars(select(Project).where(Project.license_number == "303")).one()
        assert p.capacity_mw == 46.0 and p.status == ProjectStatus.UNDER_CONSTRUCTION  # DoED capacity kept, stage moved forward
    with db.session_scope() as s:
        assert sync_records(s, doed + wiki).created == 0


def test_wikipedia_row_without_a_district_merges_only_when_the_name_is_unique(db):
    doed = [_rec("Tadi Khola HEP", 6.0, "generation", ProjectStatus.LICENSED, district="Nuwakot", license_number="51", license_type="Generation"),
            _rec("Kulung Khola HEP", 10.0, "generation", ProjectStatus.LICENSED, district="Ilam", license_number="60", license_type="Generation"),
            _rec("Kulung Khola HEP", 10.0, "generation", ProjectStatus.LICENSED, district="Taplejung", license_number="61", license_type="Generation")]
    wiki = [_rec("Tadi Khola HEP", 8.0, "wikipedia", ProjectStatus.UNDER_CONSTRUCTION),   # unique name: same project
            _rec("Kulung Khola HEP", 13.0, "wikipedia", ProjectStatus.UNDER_CONSTRUCTION)]  # two same-named projects: ambiguous
    with db.session_scope() as s:
        res = sync_records(s, doed + wiki)
        assert res.created == 4  # 3 DoED + the ambiguous Kulung Khola row; Tadi Khola merged
        tadi = s.scalars(select(Project).where(Project.license_number == "51")).one()
        assert tadi.status == ProjectStatus.UNDER_CONSTRUCTION and tadi.capacity_mw == 6.0


def test_wikipedia_spelling_variant_with_the_same_capacity_merges_but_a_dissimilar_name_does_not(db):
    doed = [_rec("Chhomron Khola Small HEP", 4.894, "generation", ProjectStatus.LICENSED, district="Kaski", license_number="499", license_type="Generation"),
            _rec("Paara Molung PRoR Hydropower Project", 8.53, "survey", ProjectStatus.PLANNED, district="Okhaldhunga")]
    wiki = [_rec("Chhomoron Khola Small HEP", 4.89, "wikipedia", ProjectStatus.PLANNED),
            _rec("Paara Malun PRoP HEP", 8.53, "wikipedia", ProjectStatus.PLANNED),
            _rec("Sano Aankhu Khola HEP", 8.53, "wikipedia", ProjectStatus.PLANNED)]  # same capacity, unrelated name
    with db.session_scope() as s:
        res = sync_records(s, doed + wiki)
        assert res.created == 3  # 2 DoED + only the unrelated one
        assert not s.scalars(select(Project).where(Project.project_name_en.in_(["Chhomoron Khola Small HEP", "Paara Malun PRoP HEP"]))).all()


def test_company_name_variants_file_keeps_one_promoter_spelled_two_ways_as_one_company(db, tmp_path):
    from src.collectors.manual_import import get_or_create_company, load_company_variants
    f = tmp_path / "variants.csv"
    f.write_text("variant,canonical\nSanjen Jalvidhyut Co,Sanjen Jalvidyut Co.\n", encoding="utf-8")
    with db.session_scope() as s:
        s.info["company_variants"] = load_company_variants(f)
        a = get_or_create_company(s, "Sanjen Jalvidyut Co.")
        b = get_or_create_company(s, "Sanjen Jalvidhyut Co")
        assert a.company_id == b.company_id
        assert get_or_create_company(s, "Sanjen Hydro Co").company_id != a.company_id  # unrelated names are untouched


def test_promoter_spelled_with_and_without_a_space_is_one_company(db):
    """Regression (QA I5): 'Nilgirikhola' and 'Nilgiri Khola' became two company records."""
    from src.collectors.manual_import import get_or_create_company
    with db.session_scope() as s:
        a = get_or_create_company(s, "Nilgiri Khola Hydropower Company Pvt. Ltd.")
        b = get_or_create_company(s, "Nilgirikhola Hydropower Company Limited")
        c = get_or_create_company(s, "Omega Energy Developer Pvt. Ltd")
        d = get_or_create_company(s, "Omega EnergyDeveloper Pvt. Ltd")
        assert a.company_id == b.company_id and c.company_id == d.company_id and a.company_id != c.company_id


def test_a_survey_licence_row_never_overwrites_a_generation_licensed_project(db):
    """Regression (QA C1): Sabha Khola A (generation licence 128) was overwritten by the unrelated survey-licence
    project 'Sabha A Hydropower Project' (licence 1282), and Sani Bheri HEP (389) by 'Rukum Sani Bheri' (1457)."""
    recs = [
        _rec("Sabha Khola A", 10.4, "generation", ProjectStatus.LICENSED, district="Sankhuwasabha",
             license_number="128", license_type="Generation", developer="Deepsabha Hydropower Pvt. Ltd."),
        _rec("Sabha A Hydropower Project", 9.0, "survey", ProjectStatus.PLANNED, district="Sankhuwasabha",
             license_number="1282", license_type="Survey", developer="Standard H. Energy Pvt. Ltd."),
        _rec("Sani Bheri HEP", 44.52, "generation", ProjectStatus.LICENSED, district="Rukum",
             license_number="389", license_type="Generation", developer="Expert Hydro Investment Pvt. Ltd"),
        _rec("Rukum Sani Bheri Hydropower Project", 45.0, "survey", ProjectStatus.PLANNED, district="Rukum",
             license_number="1457", license_type="Survey", developer="O.S.R. Hydro Pvt. Ltd."),
    ]
    with db.session_scope() as s:
        res = sync_records(s, recs)
        assert res.created == 4 and res.updated == 0
        held = {(p.license_type, p.license_number): p for p in s.scalars(select(Project))}
        assert held[("Generation", "128")].capacity_mw == 10.4 and held[("Generation", "389")].capacity_mw == 44.52
        assert held[("Survey", "1282")].capacity_mw == 9.0 and held[("Survey", "1457")].capacity_mw == 45.0
    with db.session_scope() as s:                      # and a second full sync changes nothing (no flip-flopping)
        again = sync_records(s, recs)
        assert again.created == 0
        assert {(p.license_type, p.license_number) for p in s.scalars(select(Project))} == set(held)


def test_a_survey_project_can_still_be_upgraded_by_its_own_generation_licence(db):
    """The guard must not block the normal progression: the same project gets a generation licence after a survey one."""
    with db.session_scope() as s:
        sync_records(s, [_rec("Foo Khola", 10.0, "survey", ProjectStatus.PLANNED, district="Ilam",
                              license_number="900", license_type="Survey")])
    with db.session_scope() as s:
        res = sync_records(s, [_rec("Foo Khola", 10.0, "generation", ProjectStatus.LICENSED, district="Ilam",
                                    license_number="55", license_type="Generation")])
        assert res.created == 0 and res.updated == 1
        p = s.scalars(select(Project)).one()
        assert p.license_type == "Generation" and p.license_number == "55"


def test_a_record_without_capacity_does_not_match_an_arbitrary_same_named_project(db):
    """Regression (QA I1): a missing capacity matched whichever same-named project was closest to 0 MW."""
    with db.session_scope() as s:
        sync_records(s, [_rec("Seti Khola", 1.5, "powerplants", ProjectStatus.OPERATIONAL, district="Kaski"),
                         _rec("Seti Khola", 22.0, "powerplants", ProjectStatus.OPERATIONAL, district="Tanahun")])
    with db.session_scope() as s:
        res = sync_records(s, [_rec("Seti Khola", None, "wikipedia", ProjectStatus.OPERATIONAL, district="Tanahun")])
        assert res.created == 1 and res.updated == 0
        assert sorted(p.capacity_mw for p in s.scalars(select(Project)) if p.capacity_mw) == [1.5, 22.0]


def test_a_resync_does_not_overwrite_hand_corrected_values_or_their_provenance(db):
    """Regression (QA I2): a DoED re-sync replaced manual corrections and reset the reliability tag to 'doed'."""
    with db.session_scope() as s:
        sync_records(s, [_rec("Baz Khola", 10.0, "generation", ProjectStatus.LICENSED, district="Ilam")])
    with db.session_scope() as s:
        p = s.scalars(select(Project)).one()
        p.capacity_mw, p.data_reliability = 12.0, "manual"          # a person corrected the capacity
        p.license_number = None
    with db.session_scope() as s:                     # a DoED row that agrees still fills blanks
        sync_records(s, [_rec("Baz Khola", 12.0, "generation", ProjectStatus.LICENSED, district="Ilam",
                              license_number="77", license_type="Generation")])
        assert s.scalars(select(Project)).one().license_number == "77"
    with db.session_scope() as s:                     # a DoED row that disagrees must not overwrite the correction
        res = sync_records(s, [_rec("Baz Khola", 10.4, "generation", ProjectStatus.LICENSED, district="Ilam")])
        p = s.scalars(select(Project)).one()
        assert res.created == 0 and p.capacity_mw == 12.0   # the correction survives the 10.4 DoED value
        assert p.data_reliability == "manual"               # provenance is not downgraded
        assert any("capacity_mw 12.0 vs 10.4" in c for c in res.conflicts)
