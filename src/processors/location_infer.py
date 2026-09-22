"""Fill in missing project location / river / type from traceable evidence, and record how each value was obtained.

Evidence used, in this order (a value already known from DoED or a CSV is never overwritten):
  1. data/location_overrides.csv     manual corrections: project_id,district,province,river,source
  2. reverse geocoding (OpenStreetMap Nominatim) of the project's coordinates
  3. the same project name in DoED, which has a district (capacity may differ between the two lists)
     and the DoED project-bank lists (same name and a similar capacity)
  4. a district named in the project name ("Humla Karnali-2" -> Humla)
  5. a short curated list of well-known projects
  6. river from a known river named in the project name; project type from explicit words in the name
Whatever cannot be found this way stays Unknown. Every inferred field is noted in Project.inference_note.
"""
from __future__ import annotations

import csv
import json
import re
import time
from dataclasses import dataclass, field

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..database import RIVERS_BY_BASIN
from ..models import District, Project, ProjectType
from .cleaners import discriminators, name_tokens, normalize_project_name, province_of, resolve_district
from .geo import ALL_DISTRICTS

OVERRIDES = config.DATA_DIR / "location_overrides.csv"
CACHE = config.RAW_DIR / "nominatim_cache.json"
UA = "NepalHydropowerDB/1.0 (research project)"

# Well-known projects whose location is public knowledge. district may be None when the project straddles districts.
CURATED = {
    "pancheshwar": (None, "Sudurpashchim", "Mahakali", "Mahakali border project (Darchula/Baitadi side)"),
    "sapta koshi": ("Sunsari", "Koshi", "Sapta Koshi", "high dam at Barahakshetra, Sunsari"),
    "mugu karnali": ("Mugu", "Karnali", "Karnali", "Mugu district, Karnali river"),
    "phukot karnali": ("Kalikot", "Karnali", "Karnali", "Phukot, Kalikot"),
    "kimathanka": ("Sankhuwasabha", "Koshi", "Arun", "Kimathanka, Sankhuwasabha"),
    "langtang": ("Rasuwa", "Bagmati", "Langtang", "Langtang valley, Rasuwa"),
}
PROVINCES = {"bagm": "Bagmati", "koshi": "Koshi", "madhesh": "Madhesh", "gandaki": "Gandaki", "lumbini": "Lumbini",
             "karnali": "Karnali", "sudur": "Sudurpashchim"}
RIVERS = sorted({r for rs in RIVERS_BY_BASIN.values() for r in rs if "(" not in r}, key=len, reverse=True)
TYPE_RULES = [  # (regex on lowercased name, type, the word that justified it)
    (r"\bstorage\b", ProjectType.STORAGE, "storage"), (r"\breservoir\b", ProjectType.RESERVOIR, "reservoir"),
    (r"\b(pror|prop|peaking)\b", ProjectType.PEAKING_ROR, "PRoR"), (r"\b(ror|run[- ]of[- ]river)\b", ProjectType.RUN_OF_RIVER, "RoR"),
]


@dataclass
class Report:
    counts: dict[str, int] = field(default_factory=dict)
    examples: dict[str, list[str]] = field(default_factory=dict)

    def add(self, kind: str, label: str) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.examples.setdefault(kind, [])
        if len(self.examples[kind]) < 4:
            self.examples[kind].append(label)


def _note(p: Project, text: str) -> None:
    p.inference_note = "; ".join(x for x in (p.inference_note, text) if x)[:500]


def _set_location(session: Session, p: Project, district: str | None, province: str | None, districts: dict[str, int]) -> bool:
    changed = False
    if district and p.district_id is None and district in districts:
        p.district_id, changed = districts[district], True
        province = province or province_of(district)
    if province and not p.province:
        p.province, changed = province, True
    return changed


def _province_from_state(state: str | None) -> str | None:
    low = (state or "").lower()
    return next((v for k, v in PROVINCES.items() if k in low), None)


def _geocode(lat: float, lon: float, cache: dict, session_http: requests.Session) -> dict | None:
    key = f"{lat:.4f},{lon:.4f}"
    if key not in cache:
        time.sleep(1.2)  # Nominatim usage policy: at most one request per second
        try:
            r = session_http.get("https://nominatim.openstreetmap.org/reverse", timeout=20, headers={"User-Agent": UA},
                                 params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 10, "addressdetails": 1, "accept-language": "en"})
            cache[key] = r.json().get("address") if r.ok else None
        except (requests.RequestException, ValueError):
            return None
    return cache[key]


def infer(session: Session, geocode: bool = True, dry_run: bool = False, bank: list[dict] | None = None) -> Report:
    rep = Report()
    districts = {d.district_name: d.district_id for d in session.scalars(select(District))}
    projects = list(session.scalars(select(Project)))

    # 0. a known district implies its province
    id_to_name = {i: n for n, i in districts.items()}
    for p in projects:
        if p.district_id is not None and not p.province and (prov := province_of(id_to_name.get(p.district_id))):
            p.province = prov
            _note(p, "province derived from its district")
            rep.add("province derived from district", p.project_name_en)

    # 1. manual overrides
    if OVERRIDES.exists():
        by_id = {p.project_id: p for p in projects}
        with open(OVERRIDES, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                p = by_id.get(row.get("project_id", "").strip())
                if p is None:
                    continue
                district = resolve_district(row.get("district"))
                if _set_location(session, p, district, row.get("province") or province_of(district), districts):
                    _note(p, f"location from manual override ({row.get('source') or 'no source given'})")
                    rep.add("manual override", p.project_name_en)
                if row.get("river") and not p.river_name_raw:
                    p.river_name_raw = row["river"].strip()

    # 1b. DoED project-bank lists: same name and similar capacity -> copy district / coordinates / river
    def bank_key(name: str) -> str:  # order- and space-insensitive: 'Chhum Chhum Gad' == 'Chhumchhum Gad'
        return "".join(sorted(name_tokens(name)))

    bank_index: dict[str, list[dict]] = {}
    for b in bank or []:
        bank_index.setdefault(bank_key(b["name"]), []).append(b)
    for p in projects:
        if p.district_id is not None and p.province:
            continue
        cands = [b for b in bank_index.get(bank_key(p.project_name_en), [])
                 if discriminators(name_tokens(b["name"])) == discriminators(name_tokens(p.project_name_en))
                 and (b["capacity_mw"] is None or p.capacity_mw is None or abs(b["capacity_mw"] - p.capacity_mw) <= 0.15 * max(b["capacity_mw"], p.capacity_mw))]
        if not cands or len({(b["district"], b["latitude"]) for b in cands}) > 1:
            continue  # no match, or the same name points at two different places: leave it Unknown
        b = cands[0]
        changed = _set_location(session, p, b["district"], None, districts)
        for attr, key in (("latitude", "latitude"), ("longitude", "longitude"), ("river_name_raw", "river")):
            if getattr(p, attr) is None and b[key] is not None:
                setattr(p, attr, b[key])
                changed = True
        if changed:
            _note(p, f"location copied from the {b['source']} list (same name, {b['capacity_mw']} MW)")
            rep.add("DoED project-bank list (same name and capacity)", f"{p.project_name_en} -> {b['district'] or 'coordinates only'}")

    # 2. reverse geocode projects that have coordinates but no district
    if geocode:
        cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
        http = requests.Session()
        for p in projects:
            if p.district_id is None and p.latitude is not None and p.longitude is not None:
                addr = _geocode(p.latitude, p.longitude, cache, http)
                if not addr:
                    continue
                district = resolve_district(addr.get("county"), addr.get("state_district"))
                if _set_location(session, p, district, _province_from_state(addr.get("state")), districts):
                    _note(p, f"district/province from reverse geocoding its coordinates (OpenStreetMap): {addr.get('county') or addr.get('municipality')}")
                    rep.add("reverse geocoded from coordinates", f"{p.project_name_en} -> {district}")
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    # 3. same name as a located DoED project
    located: dict[str, list[Project]] = {}
    for p in projects:
        if p.data_reliability == "doed" and p.district_id is not None:
            located.setdefault(normalize_project_name(p.project_name_en), []).append(p)
    for p in projects:
        if p.district_id is not None and p.province:
            continue
        twins = [t for t in located.get(normalize_project_name(p.project_name_en), []) if t.project_id != p.project_id
                 and discriminators(name_tokens(t.project_name_en)) == discriminators(name_tokens(p.project_name_en))]
        if twins and len({t.district_id for t in twins}) == 1:
            t = twins[0]
            changed = _set_location(session, p, next(n for n, i in districts.items() if i == t.district_id), t.province, districts)
            if changed:
                for attr in ("latitude", "longitude", "river_name_raw"):
                    if getattr(p, attr) is None:
                        setattr(p, attr, getattr(t, attr))
                _note(p, f"location copied from DoED project {t.project_id} of the same name (capacity differs: {t.capacity_mw} MW)")
                rep.add("same name as a located DoED project", f"{p.project_name_en} -> {t.district.district_name if t.district else ''}")

    # 4. a district named in the project name, 5. curated well-known projects
    for p in projects:
        if p.district_id is not None and p.province:
            continue
        low = p.project_name_en.lower()
        cur = next((v for k, v in CURATED.items() if k in low), None)
        if cur:
            if _set_location(session, p, cur[0], cur[1], districts):
                _note(p, f"location from curated list: {cur[3]}")
                rep.add("curated well-known project", p.project_name_en)
            if cur[2] and not p.river_name_raw and p.river_id is None:
                p.river_name_raw = cur[2]
            continue
        named = [d for d in ALL_DISTRICTS if len(d) >= 5 and re.search(rf"\b{re.escape(d.lower())}\b", low)]
        if len(named) == 1 and _set_location(session, p, named[0], None, districts):
            _note(p, f"district taken from the project name ('{named[0]}')")
            rep.add("district named in project name", f"{p.project_name_en} -> {named[0]}")

    # 6. river and type from the name
    for p in projects:
        low = p.project_name_en.lower()
        if not p.river_name_raw and p.river_id is None:  # a river linked from a source counts as known
            river = next((r for r in RIVERS if re.search(rf"\b{re.escape(r.lower())}\b", low)), None)
            if river:
                p.river_name_raw = river
                _note(p, f"river taken from the project name ('{river}')")
                rep.add("river named in project name", f"{p.project_name_en} -> {river}")
        if p.project_type is None:
            for pattern, ptype, word in TYPE_RULES:
                if re.search(pattern, low):
                    p.project_type = ptype
                    _note(p, f"type from the project name ('{word}')")
                    rep.add(f"type from name: {ptype.value}", p.project_name_en)
                    break
    if dry_run:
        session.rollback()
    return rep
