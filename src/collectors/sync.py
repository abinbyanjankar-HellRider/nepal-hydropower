"""Merge scraped records (DoED, Wikipedia) into the database without creating duplicates.

Rules:
  * Records are matched to existing projects by normalised name + capacity + district agreement.
    Names that differ in a discriminator (Upper/Lower, 1/2/3, A/B) are never merged.
  * DoED is authoritative: its values overwrite. Wikipedia only fills empty fields.
  * A project's stage only ever moves forward (Planned < Licensed < Under Construction < Operational).
  * Every material disagreement between sources is returned in `conflicts` for human review.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import District, Project, ProjectStatus
from ..processors.cleaners import discriminators, name_tokens
from .manual_import import PROJECT_COLUMNS, resolve_relations

STATUS_RANK = {
    ProjectStatus.PLANNED: 0, ProjectStatus.LICENSED: 1,
    ProjectStatus.UNDER_CONSTRUCTION: 2, ProjectStatus.OPERATIONAL: 3,
}
STAGE_ORDER = {"powerplants": 0, "generation": 1, "survey": 2, "wikipedia": 3}
AUTHORITATIVE_STAGES = {"powerplants", "generation", "survey"}  # i.e. DoED
# A DoED table row is one distinct project, so one existing project can be claimed once per stage.
# Wikipedia may list the same project in two sections, so it is exempt.
CLAIMING_STAGES = AUTHORITATIVE_STAGES


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    conflicts: list[str] = field(default_factory=list)


def _next_id(session: Session) -> int:
    ids = session.scalars(select(Project.project_id)).all()
    nums = [int(m.group(1)) for i in ids if (m := re.fullmatch(r"HP_(\d+)", i))]
    return max(nums, default=0) + 1


def _cap_close(a: float | None, b: float | None, tol: float) -> bool:
    if a is None or b is None:
        return True
    return abs(a - b) <= tol * max(a, b, 1e-9)


class _Entry:
    __slots__ = ("project", "tokens", "key", "flat", "disc")

    def __init__(self, project: Project):
        self.project = project
        ordered = name_tokens(project.project_name_en)
        self.tokens = set(ordered)
        self.key = " ".join(sorted(self.tokens))
        self.flat = "".join(ordered)  # spacing-insensitive: 'Kali Gandaki A' == 'Kaligandaki A'
        self.disc = discriminators(self.tokens)


class _Matcher:
    def __init__(self, projects: list[Project], district_name: dict[int, str]):
        self.entries: list[_Entry] = []
        self.district_name = district_name
        self.claimed: dict[str, set[str]] = {}
        self.by_license: dict[tuple[str, str], Project] = {}
        for p in projects:
            self.add(p)

    def add(self, p: Project) -> None:
        self.entries.append(_Entry(p))
        self.index_license(p)

    def index_license(self, p: Project) -> None:
        """Numeric licence numbers are unique per licence type ('1_gtd'-style shared numbers are skipped)."""
        if p.license_type and p.license_number and p.license_number.isdigit():
            self.by_license[(p.license_type, p.license_number)] = p

    def _district_ok(self, e: _Entry, rec_district: str | None) -> tuple[bool, bool]:
        """(compatible, both_known_and_equal)"""
        pd_name = self.district_name.get(e.project.district_id)
        if rec_district and pd_name:
            return rec_district == pd_name, rec_district == pd_name
        return True, False

    @staticmethod
    def _licence_conflict(e: _Entry, rec: dict) -> bool:
        """Two different numeric licence numbers of the same type belong to two different licensed projects, however alike the names."""
        lic, ltype = rec.get("license_number"), rec.get("license_type")
        p = e.project
        return bool(lic and lic.isdigit() and ltype and p.license_type == ltype
                    and p.license_number and p.license_number.isdigit() and p.license_number != lic)

    def find(self, rec: dict, stage: str) -> Project | None:
        ordered = name_tokens(rec["project_name_en"])
        tokens = set(ordered)
        if not tokens:
            return None
        key = " ".join(sorted(tokens))
        flat = "".join(ordered)
        disc = discriminators(tokens)
        cap = rec.get("capacity_mw")
        claimed = self.claimed.setdefault(stage, set()) if stage in CLAIMING_STAGES else set()
        free = [e for e in self.entries if e.project.project_id not in claimed and not self._licence_conflict(e, rec)]

        # 0. same licence number + type and similar capacity: the same licensed project, whatever it is called
        lic = rec.get("license_number")
        if lic and lic.isdigit() and rec.get("license_type"):
            owner = self.by_license.get((rec["license_type"], lic))
            if owner and owner.project_id not in claimed and _cap_close(owner.capacity_mw, cap, 0.10):
                return owner

        def closest(cands: list[_Entry]) -> Project | None:
            if not cands:
                return None
            return min(cands, key=lambda e: abs((e.project.capacity_mw or 0) - (cap or 0))).project

        # 1. identical folded name, similar capacity
        # Same name and near-identical capacity: districts may legitimately disagree between sources
        # (projects straddle boundaries), so they only matter when the capacity is looser.
        hit = closest([e for e in free if (e.key == key or e.flat == flat)
                       and (_cap_close(e.project.capacity_mw, cap, 0.03)
                            or (_cap_close(e.project.capacity_mw, cap, 0.15)
                                and self._district_ok(e, rec.get("district"))[0]))])
        if hit:
            return hit

        # 2. one name's tokens contain the other's, discriminators (Upper/Lower, 1/2/3, A/B) must agree.
        #    Names that differ only by a discriminator are different projects and are never merged.
        loose = []
        for e in free:
            if not e.tokens or not (e.tokens <= tokens or tokens <= e.tokens):
                continue
            ok, _ = self._district_ok(e, rec.get("district"))
            if ok and cap is not None and e.project.capacity_mw is not None and e.disc == disc                     and _cap_close(e.project.capacity_mw, cap, 0.08):
                loose.append(e)
        hit = closest(loose)
        if hit:
            return hit

        # 2b. Wikipedia rows only: the very same name in the very same district is the same project even when Wikipedia's
        #     capacity is a different design figure (up to 40% apart); DoED keeps the authoritative capacity.
        if stage == "wikipedia" and cap is not None:
            same = [e for e in free if (e.key == key or e.flat == flat) and e.disc == disc and e.project.capacity_mw is not None
                    and _cap_close(e.project.capacity_mw, cap, 0.40)]
            if rec.get("district"):
                same = [e for e in same if self._district_ok(e, rec["district"])[1]]
            elif len(same) != 1:  # no district to go on: only merge when the name is unique
                same = []
            hit = closest(same)
            if hit:
                return hit

        # 3. typo-level fuzzy match, Wikipedia rows only (DoED names are treated as distinct projects)
        if stage != "wikipedia" or cap is None:
            return None
        fuzzy = []
        for e in free:
            if e.project.capacity_mw is None or e.disc != disc or not _cap_close(e.project.capacity_mw, cap, 0.05):
                continue
            ok, exact_district = self._district_ok(e, rec.get("district"))
            if not ok:
                continue
            ratio = difflib.SequenceMatcher(None, e.flat, flat).ratio()
            if ratio >= (0.88 if exact_district else 0.94):
                fuzzy.append(e)
            elif exact_district and _cap_close(e.project.capacity_mw, cap, 0.01) and ratio >= 0.6:
                fuzzy.append(e)  # same capacity (+-1%) and same district: very likely the same plant
            elif _cap_close(e.project.capacity_mw, cap, 0.01) and ratio >= 0.80:
                fuzzy.append(e)  # same capacity (+-1%) and a similar spelling, ratio >= 0.80 (Chhomoron/Chhomron, Malun/Molung)
        return closest(fuzzy)


def sync_records(session: Session, records: list[dict]) -> SyncResult:
    result = SyncResult()
    district_name = {d.district_id: d.district_name for d in session.scalars(select(District))}
    matcher = _Matcher(list(session.scalars(select(Project))), district_name)
    next_id = _next_id(session)

    for rec in sorted(records, key=lambda r: STAGE_ORDER[r["_stage"]]):
        stage = rec["_stage"]
        authoritative = stage in AUTHORITATIVE_STAGES
        source = rec.get("data_source", stage)
        rec = resolve_relations(session, {k: v for k, v in rec.items() if k != "_stage"})
        values = {k: v for k, v in rec.items() if k in PROJECT_COLUMNS and v is not None
                  and k not in ("project_id", "status", "data_source")}

        project = matcher.find(rec, stage)
        if project is None:
            project = Project(project_id=f"HP_{next_id:03d}", status=rec["status"], **values,
                              data_source=source, data_reliability="doed" if authoritative else "wikipedia")
            next_id += 1
            session.add(project)
            matcher.add(project)
            result.created += 1
        else:
            for k, v in values.items():
                cur = getattr(project, k)
                if cur is not None and cur != v and _material(k, cur, v):
                    result.conflicts.append(
                        f"{project.project_id} {project.project_name_en}: {k} {cur} vs {v} ({source})")
                if authoritative or cur is None:
                    setattr(project, k, v)
            if STATUS_RANK.get(rec["status"], -1) > STATUS_RANK.get(project.status, -1):
                project.status = rec["status"]
            if authoritative:
                project.data_reliability = "doed"
            elif project.data_reliability in (None, "seed-unverified"):
                project.data_reliability = "wikipedia"  # a scrape has now corroborated the seed row
            sources = set(filter(None, (project.data_source or "").split("; ")))
            sources.add(source)
            project.data_source = "; ".join(sorted(sources))[:200]
            result.updated += 1
        matcher.index_license(project)
        if stage in CLAIMING_STAGES:
            matcher.claimed.setdefault(stage, set()).add(project.project_id)
    return result


def _material(field_name: str, cur, new) -> bool:
    if field_name == "capacity_mw":
        return not _cap_close(cur, new, 0.10)
    if field_name == "commissioning_year":
        return cur != new
    return False
