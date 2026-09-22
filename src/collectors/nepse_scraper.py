"""NEPSE-listed hydropower companies (symbol, name, listed shares) from Merolagani's sector listing.

Merolagani republishes NEPSE's listing; it is a third-party mirror, so treat symbols as
"as published there" and re-run to refresh.
"""
from __future__ import annotations

import csv
import io
import logging

import pandas as pd
import requests
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import Company, CompanyType
from ..processors.cleaners import company_key
from .web_scraper import _save_raw

log = logging.getLogger(__name__)

URL = "https://merolagani.com/CompanyList.aspx"
SECTOR = "Hydro Power"


def scrape_listed_hydropower() -> list[dict]:
    r = requests.get(URL, headers={"User-Agent": "Mozilla/5.0 (compatible; NepalHydropowerDB/1.0)"},
                     timeout=config.REQUEST_TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    for table in soup.find_all("table"):
        # Each sector is its own table, preceded by a panel heading naming the sector.
        for prev in table.find_all_previous(["h2", "h3", "h4", "a", "div", "span"], limit=25):
            cls = " ".join(prev.get("class", []))
            txt = prev.get_text(" ", strip=True)
            if 3 < len(txt) < 40 and ("panel" in cls or "title" in cls or "heading" in cls
                                       or prev.name in ("h2", "h3", "h4")):
                heading = txt
                break
        else:
            continue
        if heading != SECTOR:
            continue
        df = pd.read_html(io.StringIO(str(table)))[0]
        _save_raw(df, "nepse_hydropower_listed")
        return [{"symbol": str(row["Symbol"]).strip(), "name": str(row["Company Name"]).strip(),
                 "listed_shares": int(row["Listed Shares"]) if pd.notna(row["Listed Shares"]) else None,
                 "paidup_value": float(row["Paidup Value"]) if pd.notna(row["Paidup Value"]) else None}
                for _, row in df.iterrows()]
    raise RuntimeError(f"'{SECTOR}' table not found on {URL}; the page layout may have changed")


def _squash(key: str) -> str:
    return key.replace(" ", "")


ALIAS_FILE = config.DATA_DIR / "company_aliases.csv"


def load_aliases(path=ALIAS_FILE) -> dict[str, str]:
    """User-confirmed links: CSV with columns `symbol,company_name`, where company_name is the promoter name
    as it appears in DoED. Use `analyze nepse --suggest` to find candidates, then confirm them here."""
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["symbol"].strip(): r["company_name"].strip() for r in csv.DictReader(f)
                if r.get("symbol") and r.get("company_name")}


def sync_listed_companies(session: Session, listed: list[dict], aliases: dict[str, str] | None = None) -> dict:
    """Mark matching companies as NEPSE-listed (creating any that don't exist yet).

    Matching is deliberately strict (normalised name, ignoring spacing): a wrong company<->symbol link
    silently corrupts NEPSE exposure, whereas a missed match only creates a separate company row.
    Fuzzy matching was removed after it linked 'Super Madi Hydropower' (SMHL) to 'Supermai Hydropower'.
    The listing is the source of truth, so all listing flags are reset first.
    """
    companies = list(session.scalars(select(Company)))
    for c in companies:
        c.nepse_listed, c.stock_symbol, c.listed_shares, c.paidup_value = False, None, None, None
    by_key = {company_key(c.company_name): c for c in companies}
    by_squashed = {_squash(k): c for k, c in by_key.items()}
    aliases = load_aliases() if aliases is None else aliases
    result = {"matched": 0, "created": 0}
    for row in listed:
        key = company_key(row["name"])
        alias_key = company_key(aliases.get(row["symbol"], ""))
        company = (by_key.get(alias_key) if alias_key else None) or by_key.get(key) or by_squashed.get(_squash(key))
        if company is None or company.nepse_listed:  # never let two symbols share one company
            company = Company(company_name=row["name"][:200], company_type=CompanyType.IPP)
            session.add(company)
            by_key[key] = by_squashed[_squash(key)] = company
            result["created"] += 1
        else:
            result["matched"] += 1
        company.nepse_listed = True
        company.stock_symbol = row["symbol"]
        company.listed_shares = row["listed_shares"]
        company.paidup_value = row["paidup_value"]
    return result
