"""ShareSansar: structured quarterly financials, company details and announcement history.

ShareSansar publishes each listed company's latest quarterly balance sheet / P&L / ratios (in Rs '000) through
an AJAX endpoint that needs the page's CSRF token and session cookie. It does NOT host annual-report PDFs.
Raw responses are saved under data/raw/sharesansar/ so every stored number can be traced back.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import Company, CompanyFinancial

log = logging.getLogger(__name__)
BASE = "https://www.sharesansar.com"
RAW = config.RAW_DIR / "sharesansar"
POLITE_DELAY = 0.6  # seconds between requests

# label regex (lowercase) -> (CompanyFinancial attribute, scale). Amounts are published in Rs '000.
AMOUNT_FIELDS = [
    (r"^paid up capital", "paid_up_capital_npr"), (r"^reserve", "reserves_npr"),
    (r"^loans", "loans_npr"), (r"^property, plant", "ppe_npr"), (r"^capital work", "cwip_npr"),
    (r"^investments", "investments_npr"), (r"^total current liabilities", "current_liabilities_npr"),
    (r"^operating income", "operating_income_npr"), (r"^income from sales of electricity", "electricity_sales_npr"),
    (r"^financial expenses", "finance_cost_npr"), (r"^depreciation", "depreciation_npr"),
    (r"^provision for tax", "tax_provision_npr"), (r"^net profit", "net_profit_npr"),
]
RATIO_FIELDS = [
    (r"^networth per share", "networth_per_share"), (r"^earnings per share", "eps"),
    (r"^return on equity", "roe_pct"), (r"^return on assets", "roa_pct"),
]
PERIOD_RE = re.compile(r"(\d)(?:st|nd|rd|th)\s+quarter\s+(\d{4})\s*/\s*(\d{2,4})", re.I)


def parse_number(text: str) -> float | None:
    t = re.sub(r"[,\s]", "", text or "")
    if t in ("", "-", "--"):
        return None
    m = re.fullmatch(r"\(?(-?\d+(?:\.\d+)?)\)?", t)
    if not m:
        return None
    v = float(m.group(1))
    return -abs(v) if t.startswith("(") else v


def parse_quarterly_html(html: str) -> dict | None:
    """Parse the quarterly-report fragment into {'fiscal_year', 'quarter', fields..., 'raw'}. None if empty."""
    soup = BeautifulSoup(html, "lxml")
    out: dict = {}
    raw: dict[str, str] = {}
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) < 2:
                continue
            label, value = cells[0], cells[1]
            m = PERIOD_RE.search(value)
            if m and "quarter" not in out:
                y2 = m.group(3)[-2:]
                out["quarter"], out["fiscal_year"] = int(m.group(1)), f"{m.group(2)}/{y2}"
                continue
            raw[label] = value
            low = label.lower()
            for pattern, attr in AMOUNT_FIELDS:
                num = parse_number(value)
                if re.search(pattern, low) and attr not in out and num is not None:
                    out[attr] = num * 1000  # Rs '000 -> NPR
            for pattern, attr in RATIO_FIELDS:
                num = parse_number(value)
                if re.search(pattern, low) and attr not in out and num is not None:
                    out[attr] = num
    if "quarter" not in out:
        return None
    out["raw"] = raw
    return out


@dataclass
class CompanyInfo:
    sharesansar_id: int | None
    symbol: str
    email: str | None
    website: str | None
    address: str | None


GENERIC_MAIL = re.compile(r"(gmail|yahoo|hotmail|outlook|wlink|ntc|mos|live|icloud|ymail)\.", re.I)


def website_from_email(email: str | None) -> str | None:
    """A company's own mail domain is a good hint for its website; free/ISP mail domains are ignored."""
    for addr in re.findall(r"[\w.+-]+@([\w.-]+\.\w+)", email or ""):
        if not GENERIC_MAIL.search(addr + "."):
            return "https://" + addr.lower().removeprefix("www.")
    return None


def parse_company_info(html: str, symbol: str) -> CompanyInfo:
    soup = BeautifulSoup(html, "lxml")
    cid = soup.find(id="companyid")
    info = soup.find(id="cinfo")
    text = re.sub(r"\s+", " ", info.get_text(" ", strip=True)) if info else ""

    def field(label: str, nxt: tuple[str, ...]) -> str | None:
        m = re.search(label + r"\s+(.*?)\s+(?:" + "|".join(nxt) + r")\b", text)
        v = m.group(1).strip() if m else None
        return None if v in (None, "", "-") else v

    website = None
    if info:
        for a in info.find_all("a", href=True):
            if a["href"].startswith("http") and "sharesansar" not in a["href"]:
                website = a["href"]
                break
    return CompanyInfo(
        sharesansar_id=int(cid.get_text(strip=True)) if cid and cid.get_text(strip=True).isdigit() else None,
        symbol=symbol, email=field("Email", ("Address", "Website", "Contact")),
        website=website, address=field("Address", ("Website", "Contact", "Phone")))


class ShareSansarClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; NepalHydropowerDB/1.0)"})
        self._cache: dict[str, tuple[str, str]] = {}
        RAW.mkdir(parents=True, exist_ok=True)

    def _company_page(self, symbol: str) -> tuple[str, str]:
        r = self.s.get(f"{BASE}/company/{symbol.lower()}", timeout=config.REQUEST_TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        meta = soup.find("meta", attrs={"name": "_token"})
        if not meta:
            raise RuntimeError("CSRF token not found: page layout changed?")
        return r.text, meta["content"]

    def info(self, symbol: str) -> CompanyInfo:
        """Stage 1: company details (website / email / ShareSansar id). One request."""
        page, token = self._company_page(symbol)
        self._cache[symbol] = (page, token)
        return parse_company_info(page, symbol)

    def financials(self, symbol: str, info: CompanyInfo) -> dict:
        """Stage 2: latest quarterly financials + quarterly net-profit announcement history."""
        page, token = self._cache.pop(symbol, None) or self._company_page(symbol)
        soup = BeautifulSoup(page, "lxml")
        sector = soup.find(id="sector").get_text(strip=True) if soup.find(id="sector") else ""
        headers = {"X-CSRF-Token": token, "X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/company/{symbol.lower()}"}
        time.sleep(POLITE_DELAY)
        r = self.s.post(f"{BASE}/company-quarterly-report", headers=headers, timeout=config.REQUEST_TIMEOUT,
                        data={"company": info.sharesansar_id, "symbol": symbol, "sector": sector})
        (RAW / f"{symbol}_quarterly.html").write_text(r.text, encoding="utf-8")
        parsed = parse_quarterly_html(r.text) if r.ok else None
        return {"quarterly": parsed, "history": self._announcement_history(info.sharesansar_id, headers)}

    def _announcement_history(self, company_id: int | None, headers: dict) -> list[dict]:
        """Quarterly net-profit announcements ('...posted a net profit of Rs X million ... 3rd quarter ... 2082/83')."""
        if not company_id:
            return []
        rows: list[dict] = []
        start = 0
        while True:
            time.sleep(POLITE_DELAY)
            data = {"draw": 1, "start": start, "length": 50, "search[value]": "", "company": company_id, "category": 11}
            for i, c in enumerate(["published_date", "title"]):
                data.update({f"columns[{i}][data]": c, f"columns[{i}][searchable]": "true", f"columns[{i}][orderable]": "false"})
            r = self.s.post(f"{BASE}/company-announcement-category", data=data, headers=headers, timeout=config.REQUEST_TIMEOUT)
            try:
                j = r.json()
            except ValueError:
                break
            rows += j.get("data", [])
            start += 50
            if start >= j.get("recordsTotal", 0) or not j.get("data"):
                break
        return [h for h in (parse_profit_headline(re.sub(r"<[^>]+>", "", r["title"]), r.get("published_date"))
                            for r in rows) if h]


HEADLINE_RE = re.compile(
    r"net (?P<sign>profit|loss) of Rs\.?\s*(?P<amt>[\d,\.]+)\s*(?P<unit>million|billion|crore|arba|arab)?.*?"
    r"(?P<q>\d)(?:st|nd|rd|th) quarter.*?(?P<fy>\d{4})\s*/\s*(?P<fy2>\d{2,4})", re.I)
UNIT = {"million": 1e6, "billion": 1e9, "crore": 1e7, "arba": 1e9, "arab": 1e9, None: 1.0}


def parse_profit_headline(title: str, published: str | None = None) -> dict | None:
    """'...has posted a net profit of Rs 720.05 million and published its 4th quarter ... fiscal year 2082/83'."""
    m = HEADLINE_RE.search(title)
    if not m:
        return None
    amt = float(m.group("amt").replace(",", "")) * UNIT[(m.group("unit") or "").lower() or None]
    if m.group("sign").lower() == "loss":
        amt = -amt
    return {"fiscal_year": f"{m.group('fy')}/{m.group('fy2')[-2:]}", "quarter": int(m.group("q")),
            "net_profit_npr": amt, "published": published}


def store_info(company: Company, info: CompanyInfo) -> None:
    company.sharesansar_id = info.sharesansar_id or company.sharesansar_id
    company.email = info.email if info.email and "@" in info.email else company.email
    company.address = info.address or company.address
    site, src = (info.website, "sharesansar") if info.website else (website_from_email(info.email), "email-domain")
    if site and (not company.website or company.website_source != "manual"):
        company.website, company.website_source = site, src


def store_financials(session: Session, company: Company, result: dict) -> dict:
    """Write one company's fetched financials. Returns counts."""
    counts = {"quarterly": 0, "history": 0}

    def upsert(fy: str, q: int, **values) -> CompanyFinancial:
        row = session.scalar(select(CompanyFinancial).where(
            CompanyFinancial.company_id == company.company_id, CompanyFinancial.fiscal_year == fy,
            CompanyFinancial.quarter == q))
        if row is None:
            row = CompanyFinancial(company_id=company.company_id, fiscal_year=fy, quarter=q)
            session.add(row)
        for k, v in values.items():
            if v is not None:
                setattr(row, k, v)
        return row

    url = f"{BASE}/company/{company.stock_symbol.lower()}"
    for h in result["history"]:  # history first: the full quarterly report below overwrites with richer data
        upsert(h["fiscal_year"], h["quarter"], net_profit_npr=h["net_profit_npr"],
               source="ShareSansar announcement", source_url=url)
        counts["history"] += 1
    q = result["quarterly"]
    if q:
        raw = q.pop("raw")
        fy, qtr = q.pop("fiscal_year"), q.pop("quarter")
        upsert(fy, qtr, raw_json=json.dumps(raw, ensure_ascii=False), source="ShareSansar quarterly report",
               source_url=url, **q)
        counts["quarterly"] = 1
    return counts
