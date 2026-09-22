"""Scrapers for DoED (licences, plants) and Wikipedia (station lists).

Every fetch is saved to data/raw/ so any value in the database can be traced back to its source table.
Scrapers return plain record dicts; merging into the database lives in sync.py.
"""
from __future__ import annotations

import io
import logging
import time
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .. import config
from ..models import ProjectStatus
from ..processors import cleaners as c

log = logging.getLogger(__name__)


def _get(url: str, retries: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=config.REQUEST_TIMEOUT)
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            return r.text
        except requests.RequestException as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last}")


def _save_raw(df: pd.DataFrame, name: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d")
    df.to_csv(config.RAW_DIR / f"{name}_{stamp}.csv", index=False, encoding="utf-8-sig")


# --------------------------------------------------------------------------------------- DoED
class DoEDScraper:
    BASE = "https://www.doed.gov.np/pages/"
    # page slug -> (stage, status, license_type)
    PAGES = {
        "powerplantsmorethan1": ("powerplants", ProjectStatus.OPERATIONAL, "Generation"),
        "powerplantslessthan1": ("powerplants", ProjectStatus.OPERATIONAL, "Generation"),
        "clhydromorethan1": ("generation", ProjectStatus.LICENSED, "Generation"),
        "clhydrolessthan1": ("generation", ProjectStatus.LICENSED, "Generation"),
        "hydromorethan1": ("survey", ProjectStatus.PLANNED, "Survey"),
        "hydrolessthan1": ("survey", ProjectStatus.PLANNED, "Survey"),
    }

    def fetch_page(self, slug: str) -> pd.DataFrame:
        html = _get(self.BASE + slug)
        for df in pd.read_html(io.StringIO(html)):
            if "Project" in df.columns and "Capacity (MW)" in df.columns:
                _save_raw(df, f"doed_{slug}")
                return df
        raise RuntimeError(f"No project table found on DoED page {slug}")

    @staticmethod
    def row_to_record(row: pd.Series, status: ProjectStatus, license_type: str, stage: str, slug: str) -> dict | None:
        name = c.strip_refs(row.get("Project"))
        if not name:
            return None
        lat = c.midpoint(c.dms_to_decimal(row.get("Latitiude N")), c.dms_to_decimal(row.get("Latitiude N.1")))
        lon = c.midpoint(c.dms_to_decimal(row.get("Longitude E")), c.dms_to_decimal(row.get("Longitude E.1")))
        location = c.strip_refs(row.get("VDC/District"))
        district = c.resolve_district(location, row.get("Address"))
        promoter = c.normalize_company_name(row.get("Promoter"))
        lic_no = c.strip_refs(row.get("Lic No"))
        cod = c.bs_to_ad(row.get("C O D")) if "C O D" in row else None
        rec = {
            "project_name_en": name,
            "capacity_mw": c.parse_capacity(row.get("Capacity (MW)")),
            "status": status,
            "license_type": license_type,
            "license_number": lic_no,
            "license_issue_date": c.bs_to_ad(row.get("Isuue Date")),
            "license_expiry_date": c.bs_to_ad(row.get("Validity")),
            "river_name_raw": c.strip_refs(row.get("River")),
            "location_raw": location,
            "latitude": lat, "longitude": lon,
            "district": district,
            "province": c.province_of(district),
            "developer": promoter, "owner": promoter,
            "commissioning_date": cod,
            "commissioning_year": cod.year if cod else None,
            "data_source": f"DoED:{slug}",
            "_stage": stage,
        }
        return rec

    def scrape(self, pages: list[str] | None = None) -> list[dict]:
        records: list[dict] = []
        for slug in pages or list(self.PAGES):
            stage, status, ltype = self.PAGES[slug]
            df = self.fetch_page(slug)
            n_before = len(records)
            for _, row in df.iterrows():
                rec = self.row_to_record(row, status, ltype, stage, slug)
                if rec:
                    records.append(rec)
            log.info("DoED %s: %d rows", slug, len(records) - n_before)
        return records


def fetch_project_bank() -> list[dict]:
    """DoED project-bank tables: government-studied, under-study and generation-licence-application projects.

    These carry a name, capacity, river, coordinates and district text, but are not licensed projects, so they are used only
    to locate projects already in the database (see processors/location_infer.py), not to create new ones.
    """
    sources = {"gonstudied": "DoED GoN-studied projects", "gonunderstudy": "DoED GoN under-study projects",
               "appclhydro": "DoED generation-licence applications"}
    out: list[dict] = []
    for slug, label in sources.items():
        html = _get(DoEDScraper.BASE + slug)
        for df in pd.read_html(io.StringIO(html)):
            if df.shape[1] < 9:
                continue
            if "Project" not in df.columns:  # the GoN-studied table has no header row
                df = df.copy()
                df.columns = ["S No", "Project", "Capacity (MW)", "River", "Latitiude N", "Latitiude N.1", "Longitude E", "Longitude E.1",
                              "VDC/District", "Status"][: df.shape[1]]
            _save_raw(df, f"doed_{slug}")
            for _, r in df.iterrows():
                name = c.strip_refs(r.get("Project"))
                if not name:
                    continue
                loc = c.strip_refs(r.get("VDC/District"))
                out.append({"name": name, "capacity_mw": c.parse_capacity(r.get("Capacity (MW)")), "river": c.strip_refs(r.get("River")),
                            "latitude": c.midpoint(c.dms_to_decimal(r.get("Latitiude N")), c.dms_to_decimal(r.get("Latitiude N.1"))),
                            "longitude": c.midpoint(c.dms_to_decimal(r.get("Longitude E")), c.dms_to_decimal(r.get("Longitude E.1"))),
                            "district": c.resolve_district(loc, r.get("Address")), "source": label})
            break
    return out


# ----------------------------------------------------------------------------------- Wikipedia
class WikipediaScraper:
    URL = "https://en.wikipedia.org/wiki/List_of_power_stations_in_Nepal"
    # section heading -> (status, name column, year column)
    SECTIONS = {
        "Hydroelectric stations": (ProjectStatus.OPERATIONAL, "commissioning"),
        "Hydropower stations under construction": (ProjectStatus.UNDER_CONSTRUCTION, "expected"),
        "Upcoming hydroelectricity projects": (ProjectStatus.PLANNED, "expected"),
        "Special projects": (ProjectStatus.PLANNED, None),
    }

    def _tables(self) -> dict[str, pd.DataFrame]:
        soup = BeautifulSoup(_get(self.URL), "lxml")
        found = {}
        for tb in soup.select("table.wikitable"):
            h = tb.find_previous(["h2", "h3", "h4"])
            title = h.get_text(" ", strip=True).replace("[edit]", "").strip() if h else ""
            if title in self.SECTIONS and title not in found:
                found[title] = pd.read_html(io.StringIO(str(tb)))[0]
        return found

    @staticmethod
    def _col(df: pd.DataFrame, *needles: str) -> str | None:
        for col in df.columns:
            if any(n in str(col).lower() for n in needles):
                return col
        return None

    def scrape(self) -> list[dict]:
        records: list[dict] = []
        for title, df in self._tables().items():
            status, year_kind = self.SECTIONS[title]
            _save_raw(df, "wikipedia_" + title.lower().replace(" ", "_"))
            name_c = self._col(df, "name of station", "hydropower station", "hydro-power", "project", "hydropower", "name")
            cap_c = self._col(df, "capacity")
            prov_c = self._col(df, "province")
            loc_c = self._col(df, "location")
            own_c = self._col(df, "owner")
            year_c = self._col(df, "commissioned", "estimated date", "commissioning year")
            for _, row in df.iterrows():
                name = c.strip_refs(row.get(name_c))
                cap = c.parse_capacity(row.get(cap_c))
                if not name or cap is None or name.lower() == "total" \
                        or str(row.get(loc_c, "")).strip().lower() == "total":
                    continue
                location = c.strip_refs(row.get(loc_c)) if loc_c else None
                district = c.resolve_district(location)
                year = c.parse_year(row.get(year_c)) if year_c else None
                owner = c.normalize_company_name(row.get(own_c)) if own_c else None
                province = c.strip_refs(row.get(prov_c)) if prov_c else None
                rec = {
                    "project_name_en": name, "capacity_mw": cap, "status": status,
                    "location_raw": location, "district": district,
                    "province": province or c.province_of(district),
                    "developer": owner, "owner": owner,
                    "data_source": "Wikipedia:List_of_power_stations_in_Nepal", "_stage": "wikipedia",
                }
                if year_kind == "commissioning":
                    rec["commissioning_year"] = year
                elif year:
                    rec["expected_completion_year"] = year
                records.append(rec)
        log.info("Wikipedia: %d rows", len(records))
        return records
