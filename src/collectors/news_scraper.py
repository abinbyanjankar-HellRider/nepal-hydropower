"""Collect hydropower news from RSS feeds and attach it to the projects and NEPSE-listed companies it mentions."""
from __future__ import annotations

import logging
import re
from datetime import date
from email.utils import parsedate_to_datetime

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import Company, Project, ProjectUpdate, UpdateType
from ..processors.cleaners import company_key, name_tokens

log = logging.getLogger(__name__)

FEEDS = {
    "Online Khabar": "https://english.onlinekhabar.com/feed",
    "The Rising Nepal": "https://risingnepaldaily.com/rss",
    "Kathmandu Post": "https://kathmandupost.com/rss",
}
RELEVANT = re.compile(r"hydro|megawatt|\bMW\b|power project|electricity|\bNEA\b|transmission line|power plant", re.I)
MIN_FLAT_LEN = 6  # names shorter than this ('Mai', 'Tila') are too ambiguous to match in free text
MIN_SYMBOL_LEN = 3  # stock symbols shorter than this match too many unrelated words


def fetch_items(feeds: dict[str, str] | None = None) -> list[dict]:
    items: list[dict] = []
    for source, url in (feeds or FEEDS).items():
        try:
            r = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=config.REQUEST_TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as exc:
            log.warning("feed %s failed: %s", source, exc)
            continue
        for it in BeautifulSoup(r.content, "xml").find_all("item"):
            title = it.title.get_text(strip=True) if it.title else ""
            desc = BeautifulSoup(it.description.get_text(), "lxml").get_text(" ", strip=True) if it.description else ""
            link = it.link.get_text(strip=True) if it.link else None
            try:
                published = parsedate_to_datetime(it.pubDate.get_text(strip=True)).date() if it.pubDate else date.today()
            except (TypeError, ValueError):
                published = date.today()
            if link and RELEVANT.search(f"{title} {desc}"):
                items.append({"title": title, "summary": desc[:1000], "url": link, "date": published, "source": source})
    return items


def _contains(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def attach_news(session: Session, items: list[dict]) -> dict:
    projects = []
    for p in session.scalars(select(Project)):
        toks = name_tokens(p.project_name_en)
        if toks and len("".join(toks)) >= MIN_FLAT_LEN:
            projects.append((p, toks))
    # Company matching is scoped to NEPSE-listed companies: that's what a "company news" section means here,
    # and it keeps the candidate list short (fewer generic names to false-match in free text).
    companies = []
    for c in session.scalars(select(Company).where(Company.nepse_listed.is_(True))):
        toks = company_key(c.company_name).split()
        if toks and len("".join(toks)) >= MIN_FLAT_LEN:
            companies.append((c, toks))
    existing_p = {(u.project_id, u.source_url) for u in session.scalars(select(ProjectUpdate)) if u.project_id}
    existing_c = {(u.company_id, u.source_url) for u in session.scalars(select(ProjectUpdate)) if u.company_id}

    added = matched_items = 0
    for item in items:
        raw_text = f"{item['title']} {item['summary']}"
        text_tokens = name_tokens(raw_text)
        hits = [(p, toks) for p, toks in projects if _contains(text_tokens, toks)]
        # Keep the most specific names only: an article on 'Upper Trishuli-1' is not about plain 'Trishuli'.
        hits = [(p, t) for p, t in hits
                if not any(len(o) > len(t) and _contains(o, t) for _, o in hits)]
        project_companies = set()
        for p, _ in hits:
            project_companies.update(cid for cid in (p.developer_company_id, p.owner_company_id) if cid)
            if (p.project_id, item["url"]) not in existing_p:
                session.add(ProjectUpdate(
                    project_id=p.project_id, update_date=item["date"], update_type=UpdateType.NEWS,
                    title=item["title"][:300], description=item["summary"], source_url=item["url"],
                    source_name=item["source"]))
                existing_p.add((p.project_id, item["url"]))
                added += 1

        # Companies named directly in the text, by name or by stock symbol (e.g. "... Api Power (API) said ...").
        company_text_tokens = company_key(raw_text).split()
        name_hits = {c for c, toks in companies if _contains(company_text_tokens, toks)}
        symbol_hits = {c for c, _ in companies if c.stock_symbol and len(c.stock_symbol) >= MIN_SYMBOL_LEN
                       and re.search(rf"\b{re.escape(c.stock_symbol)}\b", raw_text)}
        # Skip a company already covered by one of its own projects matched above, so the same article isn't
        # stored twice for the same company (once via the project, once via the direct name/symbol match).
        company_hits = {c for c in (name_hits | symbol_hits) if c.company_id not in project_companies}
        for c in company_hits:
            if (c.company_id, item["url"]) not in existing_c:
                session.add(ProjectUpdate(
                    company_id=c.company_id, update_date=item["date"], update_type=UpdateType.NEWS,
                    title=item["title"][:300], description=item["summary"], source_url=item["url"],
                    source_name=item["source"]))
                existing_c.add((c.company_id, item["url"]))
                added += 1
        matched_items += bool(hits or company_hits)
    return {"items": len(items), "matched_items": matched_items, "updates_added": added}
