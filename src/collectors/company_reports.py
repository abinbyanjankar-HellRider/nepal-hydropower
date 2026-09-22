"""Find and download annual / quarterly report PDFs from listed companies' own websites.

Neither ShareSansar nor Merolagani hosts report files (ShareSansar shows structured quarterly figures, Merolagani
shows filings through an image viewer), so PDFs have to come from each company's website. Coverage depends on the
company publishing them; everything found or failed is recorded in `company_reports` with a status and note.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import Company, CompanyReport

log = logging.getLogger(__name__)
REPORT_DIR = config.DATA_DIR / "reports"
MAX_PDF_BYTES = 60 * 1024 * 1024
DELAY = 1.0  # seconds between requests to the same site
HUB_HINT = re.compile(r"report|download|publication|investor|financial|annual|shareholder|disclosure", re.I)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NepalHydropowerDB/1.0; research)"}


def bs_fiscal_year(text: str) -> str | None:
    """Find a fiscal year like 2080/81, 2080-81, FY 2079-80 (BS) or 2023-24 (AD, converted to BS)."""
    for m in re.finditer(r"(?<!\d)(20\d{2})\s*[/\-_]\s*(\d{2,4})(?!\d)", text):
        y = int(m.group(1))
        if 2060 <= y <= 2099:  # already Bikram Sambat
            return f"{y}/{str(m.group(2))[-2:]}"
        if 2010 <= y <= 2031:  # Gregorian fiscal year: FY 2023-24 == BS 2080/81
            return f"{y + 57}/{(y + 58) % 100:02d}"
    return None


def classify_link(text: str, href: str) -> tuple[str, str | None, int | None] | None:
    """Return (kind, fiscal_year, quarter) for a PDF link that looks like an annual or quarterly report."""
    blob = f"{text} {urlparse(href).path.rsplit('/', 1)[-1]}".lower().replace("_", " ")
    if re.search(r"annual|agm|\bar\b|financial statement|audited", blob) and not re.search(r"quarter|\bq[1-4]\b", blob):
        return "annual", bs_fiscal_year(blob), None
    m = re.search(r"(?:\bq([1-4])\b|(1st|2nd|3rd|4th|first|second|third|fourth)\s+quarter|quarter(?:ly)?\s*([1-4])?)", blob)
    if m:
        words = {"1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3, "4th": 4, "fourth": 4}
        q = m.group(1) or m.group(3) or words.get(m.group(2) or "")
        return "quarterly", bs_fiscal_year(blob), int(q) if q else None
    return None


def _get(session_: requests.Session, url: str, **kw) -> requests.Response | None:
    for candidate in (url, url.replace("https://", "http://"), url.replace("https://", "https://www.")):
        try:
            r = session_.get(candidate, timeout=config.REQUEST_TIMEOUT, headers=HEADERS, **kw)
            if r.ok:
                return r
        except requests.RequestException:
            continue
    return None


def discover(session: Session, company: Company, max_hub_pages: int = 6) -> dict:
    """Crawl a company site (home + likely report pages) for annual/quarterly PDF links; record them."""
    result = {"pdf_links": 0, "new": 0, "note": None}
    if not company.website:
        result["note"] = "no website known"
        return result
    http = requests.Session()
    home = _get(http, company.website)
    if home is None:
        result["note"] = "website unreachable"
        return result
    host = urlparse(home.url).netloc.removeprefix("www.")
    pages, seen_pages, found = [home], {home.url}, {}
    queue = []
    for r in pages:
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            url = urljoin(r.url, a["href"]).split("#")[0]
            text = a.get_text(" ", strip=True)
            if urlparse(url).netloc.removeprefix("www.") != host:
                continue
            if url.lower().split("?")[0].endswith(".pdf"):
                found[url] = text
            elif HUB_HINT.search(f"{text} {url}") and url not in seen_pages and len(queue) < max_hub_pages:
                seen_pages.add(url)
                queue.append(url)
    for url in queue:
        time.sleep(DELAY)
        r = _get(http, url)
        if r is None or "html" not in r.headers.get("content-type", ""):
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            link = urljoin(r.url, a["href"]).split("#")[0]
            if link.lower().split("?")[0].endswith(".pdf") and urlparse(link).netloc.removeprefix("www.") == host:
                found[link] = a.get_text(" ", strip=True)
    existing = {x.source_url for x in session.scalars(select(CompanyReport).where(CompanyReport.company_id == company.company_id))}
    for url, text in found.items():
        cls = classify_link(text, url)
        if not cls:
            continue
        result["pdf_links"] += 1
        if url in existing:
            continue
        kind, fy, q = cls
        session.add(CompanyReport(company_id=company.company_id, kind=kind, fiscal_year=fy, quarter=q,
                                  title=(text or url.rsplit("/", 1)[-1])[:300], source_site=host, source_url=url,
                                  status="found"))
        result["new"] += 1
    return result


def download(session: Session, report: CompanyReport, symbol: str) -> str:
    """Download one PDF (size-capped, verified as a PDF) into data/reports/<symbol>/<kind>/."""
    dest_dir = REPORT_DIR / symbol / report.kind
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(requests.utils.requote_uri(report.source_url), stream=True, timeout=60, headers=HEADERS) as r:  # encodes spaces
            r.raise_for_status()
            if int(r.headers.get("content-length", 0)) > MAX_PDF_BYTES:
                raise ValueError("file larger than 60 MB")
            data = bytearray()
            for chunk in r.iter_content(1 << 16):
                data += chunk
                if len(data) > MAX_PDF_BYTES:
                    raise ValueError("file larger than 60 MB")
        if not data.startswith(b"%PDF"):
            raise ValueError("not a PDF (server returned something else)")
    except (requests.RequestException, ValueError) as exc:
        report.status, report.note = "failed", str(exc)[:290]
        return "failed"
    digest = hashlib.sha256(data).hexdigest()
    name = f"{report.fiscal_year or 'unknown'}{'_q' + str(report.quarter) if report.quarter else ''}_{digest[:8]}.pdf".replace("/", "-")
    path = dest_dir / name
    path.write_bytes(data)
    report.local_path, report.sha256, report.size_bytes = str(path.relative_to(config.BASE_DIR)), digest, len(data)
    report.status, report.note = "downloaded", None
    return "downloaded"


# ------------------------------------------------------------------ website guessing
GENERIC = {"limited", "ltd", "pvt", "private", "public", "company", "co", "the", "and"}
POWER_WORDS = {"hydropower", "hydro", "power", "energy", "electric", "urja", "bidyut", "jalbidyut", "jalvidhyut", "jalvidyut"}


def name_tokens_for_domain(name: str) -> tuple[list[str], list[str]]:
    """(all distinctive tokens, distinctive tokens minus power words) from a company name."""
    words = [w for w in re.sub(r"[^a-z0-9 ]", " ", name.lower()).split() if w not in GENERIC]
    core = [w for w in words if w not in POWER_WORDS] or words
    return words, core


SUFFIXES = (".com.np", ".com", ".org.np", ".org", ".net.np", ".np")


def candidate_domains(name: str, symbol: str | None = None) -> list[str]:
    """Likely domains from the company name (and NEPSE symbol), most probable first."""
    words, core = name_tokens_for_domain(name)
    joined_core, joined_all = "".join(core), "".join(words)
    bases: list[str] = []
    for b in (joined_core, joined_all, joined_core + "hydro", joined_core + "hydropower", joined_core + "power",
              joined_core + "energy", joined_core + "urja", joined_core + "hp", (symbol or "").lower(),
              (symbol or "").lower() + "hydro", "-".join(core) if len(core) > 1 else ""):
        if len(b) >= 3 and b not in bases:
            bases.append(b)
    return [f"https://{b}{suf}" for b in bases for suf in SUFFIXES]


PARKED = ("submit offer", "domain is for sale", "buy this domain", "this domain may be for sale", "domain parking", "parked")


def _verify_site(url: str, name: str, min_frac: float = 1.0) -> str | None:
    """Accept a guessed URL only if it is a real page that is plausibly the company's own site.

    Distinctive name tokens (all of them by default, `min_frac` of them for search hits) must appear in the page, plus a power word. A name with a single distinctive token
    (e.g. 'Api', 'Sayapatri') is too easy to match by accident, so that token must also be in the page <title>, and
    thin or parked-domain pages are refused.
    """
    try:
        r = requests.get(url, timeout=6, headers=HEADERS, allow_redirects=True)
    except requests.RequestException:
        return None
    if not r.ok or "html" not in r.headers.get("content-type", ""):
        return None
    _, core = name_tokens_for_domain(name)
    soup = BeautifulSoup(r.text[:200_000], "lxml")
    norm = lambda t: re.sub(r"[‘’'`]", "", t.lower())  # People's == Peoples
    text = norm(soup.get_text(" ", strip=True))
    title = norm(soup.title.get_text(" ", strip=True)) if soup.title else ""
    if len(text) < 300 or any(m in text for m in PARKED):
        return None
    present = sum(t in text for t in core) / len(core)
    if present < min_frac or not any(w in text for w in POWER_WORDS | {"megawatt", "mw", "kwh"}):
        return None
    if len(core) == 1:
        # A single distinctive word ('Api', 'Sayapatri') is easy to hit by accident: also need the word 'hydro' and either
        # the word in the title (ignoring spaces: 'Green Life' ~ 'greenlife') or repeated in the page.
        token = core[0]
        in_title = token in title.replace(" ", "") or token in title
        if "hydro" not in text or not (in_title or text.count(token) >= 2):
            return None
    return f"{urlparse(r.url).scheme}://{urlparse(r.url).netloc}"


def guess_website(name: str, symbol: str | None = None) -> str | None:
    for url in candidate_domains(name, symbol):
        if (site := _verify_site(url, name)):
            return site
    return None


# ------------------------------------------------------------------ web search
AGGREGATORS = ("merolagani", "sharesansar", "facebook", "linkedin", "wikipedia", "nepalstock", "doed.gov", "nea.org", "twitter",
               "youtube", "sebon", "cdsc", "hamroshare", "instagram", "nepsealpha", "bizmandu", "kathmandupost", "onlinekhabar",
               "risingnepal", "myrepublica", "nepalipaisa", "siprabi", "google", "bing.", "scribd", "yumpu", "issuu", "slideshare",
               "arthasansar", "worldbank", "adb.org", "ifc.org", "duckduckgo", "yellowpages", "crunchbase", "zaubacorp", "opencorporates",
               "sharehub", "nepalenergyforum", "reddit", "medium.com", "pinterest", "tiktok", "amazon", "alibaba", "indeed", "glassdoor")


def search_candidates(name: str, limit: int = 6, pause: float = 1.5) -> list[str]:
    """Web-search a company and return non-aggregator hostnames of the top results (DuckDuckGo HTML)."""
    from urllib.parse import unquote
    time.sleep(pause)
    try:
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": f"{name} Nepal official website"},
                          headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"},
                          timeout=20)
    except requests.RequestException:
        return []
    hosts: list[str] = []
    for a in BeautifulSoup(r.text, "lxml").select("a.result__a"):
        m = re.search(r"uddg=([^&]+)", a.get("href", ""))
        host = urlparse(unquote(m.group(1)) if m else a.get("href", "")).netloc.lower()
        if host and host not in hosts and not any(x in host for x in AGGREGATORS):
            hosts.append(host)
    return hosts[:limit]


def find_website_by_search(name: str) -> str | None:
    for host in search_candidates(name):
        if (site := _verify_site(f"https://{host}", name, min_frac=0.6)):
            return site
    return None
