"""Flask dashboard + JSON API.  Run with `python main.py web`."""
from __future__ import annotations

import hmac
import math
import os
from datetime import date, datetime

import pandas as pd
from flask import Flask, abort, jsonify, render_template, request, send_file
from sqlalchemy import select

from . import config
from .analytics import company_profiles as profiles
from .analytics import financials as fin
from .analytics import load_projects_df
from .analytics import ownership as own
from .analytics import technical as tech
from .analytics import unknowns as unk
from .collectors.manual_import import upsert_project
from .database import DatabaseManager, db_manager as default_db
from .models import Company, CompanyReport, Project, ProjectStatus

STATUSES = [s.value for s in ProjectStatus]
SORTABLE = {"name", "capacity_mw", "status", "province", "district", "developer", "commissioning_year"}
LIST_COLUMNS = ["project_id", "name", "capacity_mw", "status", "project_type", "province", "district", "river",
                "developer", "symbol", "commissioning_year", "expected_completion_year", "license_type",
                "latitude", "longitude", "data_reliability"]


def _clean(value):
    """Make pandas/numpy/date values JSON-safe (NaN -> None, dates -> ISO strings)."""
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):  # NaN and +/-Infinity are not valid JSON
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalar
        return _clean(value.item())
    if hasattr(value, "value") and not isinstance(value, (str, int, float)):  # Enum
        return value.value
    if value is pd.NaT:
        return None
    return value


def json_safe(obj):
    """Recursively make nested dict/list data JSON-valid (no NaN/Infinity, dates as ISO strings)."""
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return _clean(obj)


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in row.items()} for row in df.to_dict("records")]


def create_app(db: DatabaseManager | None = None) -> Flask:
    db = db or default_db
    root = config.BASE_DIR / "dashboards"
    app = Flask(__name__, template_folder=str(root / "templates"), static_folder=str(root / "static"))
    app.json.sort_keys = False

    def frame() -> pd.DataFrame:
        with db.session_scope() as s:
            return load_projects_df(s)

    # ------------------------------------------------------------------------------- pages
    @app.get("/")
    def home():
        return render_template("home.html", statuses=STATUSES)

    @app.get("/map")
    def map_page():
        return render_template("map.html", statuses=STATUSES)

    @app.get("/projects")
    def projects_page():
        return render_template("projects.html", statuses=STATUSES)

    @app.get("/projects/<project_id>")
    def project_page(project_id):
        return render_template("project.html", project_id=project_id)

    @app.get("/companies")
    def companies_page():
        return render_template("companies.html")

    @app.get("/companies/<int:company_id>")
    def company_page(company_id):
        return render_template("company.html", company_id=company_id)

    @app.get("/analytics")
    def analytics_page():
        return render_template("analytics.html", statuses=STATUSES)

    @app.get("/news")
    def news_page():
        return render_template("news.html")

    # --------------------------------------------------------------------------------- API
    @app.get("/api/projects")
    def api_projects():
        df = frame()
        a = request.args
        if a.getlist("status"):
            df = df[df["status"].isin(a.getlist("status"))]
        for arg, col in (("province", "province"), ("district", "district"), ("project_type", "project_type")):
            if a.get(arg):
                df = df[df[col] == a[arg]]
        if a.get("q"):
            df = df[df["name"].str.contains(a["q"], case=False, regex=False, na=False)]
        if a.get("developer"):
            df = df[df["developer"].fillna("").str.contains(a["developer"], case=False, regex=False)]
        if a.get("symbol"):
            df = df[df["symbol"].fillna("").str.upper() == a["symbol"].upper()]
        for arg, op in (("capacity_min", "ge"), ("capacity_max", "le")):
            if a.get(arg):
                try:
                    v = float(a[arg])
                except ValueError:
                    abort(400, f"{arg} must be a number")
                df = df[df["capacity_mw"] >= v] if op == "ge" else df[df["capacity_mw"] <= v]
        sort = a.get("sort", "capacity_mw")
        if sort not in SORTABLE:
            abort(400, f"sort must be one of {sorted(SORTABLE)}")
        df = df.rename(columns={"name": "name"}).sort_values(
            sort, ascending=a.get("order", "desc") == "asc", na_position="last")
        try:
            limit = min(int(a.get("limit", 100)), 1000)
            offset = max(int(a.get("offset", 0)), 0)
        except ValueError:
            abort(400, "limit/offset must be integers")
        return jsonify({"total": len(df), "items": records(df.iloc[offset:offset + limit][LIST_COLUMNS])})

    @app.get("/api/projects/statistics")
    def api_stats():
        df = frame()
        out = {
            "summary": {k: _clean(v) for k, v in tech.summary(df).items()},
            "by_status": records(tech.capacity_by(df, "status")),
            "operational_by_province": records(tech.capacity_by(df, "province", "Operational")),
            "operational_by_size": records(tech.capacity_by(df, "size", "Operational")),
            "timeline": records(tech.commissioning_timeline(df)),
            "pipeline": records(tech.pipeline_by_year(df)),
            "top_operational": records(tech.top_projects(df, 10, "Operational")),
        }
        return jsonify(out)

    @app.get("/api/projects/<project_id>")
    def api_project(project_id):
        with db.session_scope() as s:
            p = s.get(Project, project_id)
            if p is None:
                abort(404, "unknown project")
            data = {c.name: _clean(getattr(p, c.name)) for c in p.__table__.columns
                    if c.name not in ("created_at", "updated_at")}
            data["district"] = p.district.district_name if p.district else None
            data["developer"] = p.developer.company_name if p.developer else None
            data["developer_symbol"] = p.developer.stock_symbol if p.developer else None
            data["owner"] = p.owner.company_name if p.owner else None
            data["financials"] = records(fin.financial_history(s, project_id))
            data["shareholders"] = records(own.project_shareholders(s, project_id))
            data["updates"] = [{"date": _clean(u.update_date), "title": u.title, "source": u.source_name,
                                "url": u.source_url} for u in sorted(p.updates, key=lambda u: u.update_date, reverse=True)]
        return jsonify(data)

    @app.get("/api/analytics/capacity")
    def api_capacity():
        by = request.args.get("by", "status")
        status = request.args.get("status") or None
        if by not in (*tech.GROUPINGS, "size"):
            abort(400, f"by must be one of {[*tech.GROUPINGS, 'size']}")
        if status and status not in STATUSES:
            abort(400, f"status must be one of {STATUSES}")
        return jsonify(records(tech.capacity_by(frame(), by, status).head(int(request.args.get("top", 20)))))

    @app.get("/api/analytics/unknowns")
    def api_unknowns():
        by = request.args.get("by", "province")
        if by not in unk.DIMENSIONS:
            abort(400, f"by must be one of {list(unk.DIMENSIONS)}")
        df = frame()
        return jsonify({"summary": records(pd.DataFrame(unk.summary(df))[["dimension", "label", "projects", "share_of_projects_pct", "mw", "share_of_mw_pct", "why"]]),
                        "detail": json_safe(unk.summary(df)), "projects": records(unk.unknown_projects(df, by, int(request.args.get("limit", 30))))})

    @app.get("/api/analytics/licences")
    def api_licences():
        return jsonify(records(tech.expiring_licences(frame(), int(request.args.get("days", 365))).head(50)))

    @app.get("/api/geojson")
    def api_geojson():
        df = frame()
        df = df[df["latitude"].notna() & df["longitude"].notna()]
        if request.args.getlist("status"):
            df = df[df["status"].isin(request.args.getlist("status"))]
        features = [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["longitude"], r["latitude"]]},
            "properties": {k: _clean(r[k]) for k in ("project_id", "name", "capacity_mw", "status", "district",
                                                     "developer", "symbol", "data_reliability")},
        } for r in df.to_dict("records")]
        return jsonify({"type": "FeatureCollection", "features": features})

    @app.get("/api/companies")
    def api_companies():
        df = frame()
        with db.session_scope() as s:
            exposure = own.nepse_exposure(df, s)
        return jsonify({"developers": records(own.developer_ranking(df, 30)), "nepse": records(exposure)})

    @app.get("/api/companies/profiles")
    def api_company_profiles():
        scope = request.args.get("scope", "listed")
        if scope not in ("listed", "all"):
            abort(400, "scope must be 'listed' or 'all'")
        with db.session_scope() as s:
            return jsonify(json_safe(profiles.list_profiles(s, listed_only=scope == "listed")))

    @app.get("/api/companies/<int:company_id>/profile")
    def api_company_profile(company_id):
        with db.session_scope() as s:
            p = profiles.build_profile(s, company_id)
        if p is None:
            abort(404, "unknown company")
        return jsonify(json_safe(p))

    @app.get("/reports/<int:report_id>/file")
    def report_file(report_id):
        """Serve a downloaded report PDF. The path comes from our own database, never from the request."""
        with db.session_scope() as s:
            rep = s.get(CompanyReport, report_id)
            path = (config.BASE_DIR / rep.local_path) if rep and rep.local_path else None
        if path is None or not path.is_file() or config.DATA_DIR not in path.resolve().parents:
            abort(404, "report file not available")
        return send_file(path, mimetype="application/pdf")

    @app.get("/api/companies/<int:company_id>")
    def api_company(company_id):
        with db.session_scope() as s:
            c = s.get(Company, company_id)
            if c is None:
                abort(404, "unknown company")
            projects = [{"project_id": p.project_id, "name": p.project_name_en, "capacity_mw": p.capacity_mw,
                         "status": p.status.value} for p in c.developed_projects]
            return jsonify({"company_id": c.company_id, "name": c.company_name, "nepse_listed": c.nepse_listed,
                            "symbol": c.stock_symbol, "projects": projects})

    @app.get("/api/news")
    def api_news():
        from .models import ProjectUpdate
        scope = request.args.get("scope", "all")
        if scope not in ("all", "projects", "companies"):
            abort(400, "scope must be 'all', 'projects' or 'companies'")
        limit = min(int(request.args.get("limit", 30)), 200)
        with db.session_scope() as s:
            stmt = select(ProjectUpdate).order_by(ProjectUpdate.update_date.desc())
            if scope == "projects":
                stmt = stmt.where(ProjectUpdate.project_id.isnot(None))
            elif scope == "companies":
                stmt = stmt.where(ProjectUpdate.company_id.isnot(None))
            rows = s.scalars(stmt.limit(limit)).all()
            return jsonify([{"date": _clean(u.update_date), "title": u.title, "source": u.source_name,
                             "url": u.source_url, "project_id": u.project_id,
                             "project": u.project.project_name_en if u.project else None,
                             "company_id": u.company_id, "company": u.company.company_name if u.company else None,
                             "company_symbol": u.company.stock_symbol if u.company else (
                                 u.project.developer.stock_symbol if u.project and u.project.developer else None)}
                             for u in rows])

    @app.get("/api/export/excel")
    def api_export():
        from .exports.excel_builder import build_workbook
        kind = request.args.get("format", "master")
        try:
            path = build_workbook(kind, db=db)
        except ValueError as exc:
            abort(400, str(exc))
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.post("/api/projects/add")
    def api_add_project():
        token = os.getenv("ADMIN_TOKEN")
        supplied = request.headers.get("X-Admin-Token", "")
        if not token or not hmac.compare_digest(token, supplied):
            abort(403, "admin token required (set ADMIN_TOKEN and send X-Admin-Token)")
        data = request.get_json(silent=True) or {}
        from .processors.validators import validate_project_data
        errors = validate_project_data(data)
        if errors:
            return jsonify({"errors": errors}), 400
        with db.session_scope() as s:
            if not data.get("project_id"):
                count = s.scalar(select(Project.project_id).order_by(Project.project_id.desc()).limit(1)) or "HP_000"
                data["project_id"] = f"HP_{int(count.split('_')[1]) + 1:03d}"
            try:
                project, created = upsert_project(s, data, default_reliability="api")
            except ValueError as exc:
                return jsonify({"errors": [str(exc)]}), 400
            return jsonify({"project_id": project.project_id, "created": created}), 201 if created else 200

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def http_error(err):
        if request.path.startswith("/api/"):
            return jsonify({"error": err.description}), err.code
        return render_template("error.html", code=err.code, message=err.description), err.code

    return app


def run() -> None:
    create_app().run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=config.DEBUG)
