"""Data-quality checks: per-record validation before insert, and a whole-database audit."""
from __future__ import annotations

import difflib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import Project, ProjectStatus
from .cleaners import discriminators, name_tokens, normalize_project_name


@dataclass
class Issue:
    severity: str  # error | warning | info
    code: str
    project_id: str
    message: str


def validate_project_data(rec: dict) -> list[str]:
    """Validate a single record dict. Returns a list of error strings (empty = valid)."""
    errors = []
    if not rec.get("project_name_en"):
        errors.append("missing project_name_en")
    cap = rec.get("capacity_mw")
    if cap is not None and not (config.MIN_PROJECT_CAPACITY_MW <= cap <= config.MAX_PROJECT_CAPACITY_MW):
        errors.append(f"capacity_mw {cap} outside {config.MIN_PROJECT_CAPACITY_MW}-{config.MAX_PROJECT_CAPACITY_MW}")
    lat, lon = rec.get("latitude"), rec.get("longitude")
    if lat is not None and not (config.NEPAL_LAT_RANGE[0] <= lat <= config.NEPAL_LAT_RANGE[1]):
        errors.append(f"latitude {lat} outside Nepal")
    if lon is not None and not (config.NEPAL_LON_RANGE[0] <= lon <= config.NEPAL_LON_RANGE[1]):
        errors.append(f"longitude {lon} outside Nepal")
    return errors


def validate_database(session: Session) -> list[Issue]:
    issues: list[Issue] = []
    today = date.today()
    projects = list(session.scalars(select(Project)))

    for p in projects:
        pid = p.project_id
        for err in validate_project_data({
            "project_name_en": p.project_name_en, "capacity_mw": p.capacity_mw,
            "latitude": p.latitude, "longitude": p.longitude,
        }):
            issues.append(Issue("error", "invalid-value", pid, err))
        if p.capacity_mw is None:
            issues.append(Issue("warning", "missing-capacity", pid, "no capacity"))
        if p.district_id is None:
            issues.append(Issue("warning", "missing-district", pid, "no district resolved"))
        if p.developer_company_id is None:
            issues.append(Issue("info", "missing-developer", pid, "no developer/promoter"))
        if p.latitude is None or p.longitude is None:
            issues.append(Issue("info", "missing-coordinates", pid, "no coordinates (cannot be mapped)"))
        if p.status == ProjectStatus.OPERATIONAL and p.commissioning_year is None:
            issues.append(Issue("warning", "operational-no-cod", pid, "operational but no commissioning year"))
        if p.commissioning_year and p.commissioning_year > today.year and p.status == ProjectStatus.OPERATIONAL:
            issues.append(Issue("warning", "future-cod", pid,
                                f"operational but commissioning year {p.commissioning_year} is in the future"))
        if p.status in (ProjectStatus.LICENSED, ProjectStatus.PLANNED, ProjectStatus.UNDER_CONSTRUCTION) \
                and p.license_expiry_date and p.license_expiry_date < today:
            issues.append(Issue("warning", "licence-expired", pid,
                                f"{p.license_type} licence expired {p.license_expiry_date}"))
        if p.data_reliability == "seed-unverified":
            issues.append(Issue("info", "seed-unverified", pid, "hand-entered seed row not confirmed by a scrape"))

    # Duplicates: identical normalised names, or near-identical names at near-identical capacity.
    by_key: dict[str, list[Project]] = defaultdict(list)
    for p in projects:
        by_key[normalize_project_name(p.project_name_en)].append(p)
    for key, group in by_key.items():
        if key and len(group) > 1:
            for a in group[1:]:
                if group[0].capacity_mw and a.capacity_mw and abs(group[0].capacity_mw - a.capacity_mw) <= 0.1 * a.capacity_mw:
                    issues.append(Issue("warning", "duplicate-suspect", a.project_id,
                                        f"same name/capacity as {group[0].project_id} ({group[0].project_name_en})"))
    sized = sorted((p for p in projects if p.capacity_mw), key=lambda p: p.capacity_mw)
    for i, a in enumerate(sized):
        ka = normalize_project_name(a.project_name_en)
        for b in sized[i + 1:]:
            if b.capacity_mw > a.capacity_mw * 1.05:
                break
            kb = normalize_project_name(b.project_name_en)
            same_disc = discriminators(name_tokens(a.project_name_en)) == discriminators(name_tokens(b.project_name_en))
            if ka != kb and same_disc and difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.9:
                issues.append(Issue("warning", "duplicate-suspect", b.project_id,
                                    f"near-identical name to {a.project_id} ({a.project_name_en}) at same capacity"))
    return issues
