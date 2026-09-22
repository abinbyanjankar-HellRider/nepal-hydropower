"""Pull candidate facts (project cost, PPA rate, tax holiday) out of report PDFs, with page + exact snippet.

These are regex candidates on unstructured text, so every fact is stored unverified with the sentence it came from.
Treat them as leads to confirm, not as audited numbers. Confirmed values can be loaded via `import-csv --kind facts`.
"""
from __future__ import annotations

import re
from pathlib import Path

UNIT_NPR = {"billion": 1e9, "arba": 1e9, "arab": 1e9, "million": 1e6, "crore": 1e7, "lakh": 1e5}
MONEY = r"(?:Rs\.?|NPR|NRs\.?)\s*"
NUM = r"([0-9][0-9,]*(?:\.[0-9]+)?)"

# 'tax holiday' style statements
TAX_RE = re.compile(
    r"(tax holiday|(?:exempt(?:ed|ion)?|exemption|rebate|concession)[^.\n]{0,60}income tax|income tax[^.\n]{0,60}"
    r"(?:exempt(?:ed|ion)?|exemption|rebate|concession)|(?:100|50)\s?%[^.\n]{0,50}(?:tax|rebate|exempt))", re.I)
# 'PPA rate Rs 8.40 per unit' either order
PPA_A = re.compile(r"(?:PPA|power purchase|tariff|energy rate|purchase rate)[^.\n]{0,140}?" + MONEY + NUM + r"\s*(?:/|per)\s*(?:unit|kwh|kw\.?h)", re.I)
PPA_B = re.compile(MONEY + NUM + r"\s*(?:/|per)\s*(?:unit|kwh|kw\.?h)[^.\n]{0,100}?(?:PPA|tariff)", re.I)
# 'total project cost of Rs 5.2 billion'
COST_RE = re.compile(r"(?:total|estimated|revised|approved)?\s*(?:project|construction)\s+cost[^.\n]{0,90}?" + MONEY + NUM + r"\s*(billion|arba|arab|million|crore|lakh)", re.I)


def _snippet(text: str, m: re.Match, width: int = 110) -> str:
    return re.sub(r"\s+", " ", text[max(0, m.start() - width): m.end() + width]).strip()[:500]


def extract_from_text(text: str, page: int) -> list[dict]:
    facts: list[dict] = []
    for m in PPA_A.finditer(text):
        v = float(m.group(1).replace(",", ""))
        if 2.0 <= v <= 20.0:  # plausible NPR/kWh only
            facts.append({"fact_type": "ppa_rate_npr_kwh", "value_num": v, "unit": "NPR/kWh", "page": page, "snippet": _snippet(text, m)})
    for m in PPA_B.finditer(text):
        v = float(m.group(1).replace(",", ""))
        if 2.0 <= v <= 20.0:
            facts.append({"fact_type": "ppa_rate_npr_kwh", "value_num": v, "unit": "NPR/kWh", "page": page, "snippet": _snippet(text, m)})
    for m in COST_RE.finditer(text):
        v = float(m.group(1).replace(",", "")) * UNIT_NPR[m.group(2).lower()]
        if 1e8 <= v <= 5e11:  # NPR 100 million .. 500 billion
            facts.append({"fact_type": "project_cost_npr", "value_num": v, "unit": "NPR", "page": page, "snippet": _snippet(text, m)})
    for m in TAX_RE.finditer(text):
        facts.append({"fact_type": "tax_holiday_mention", "value_text": re.sub(r"\s+", " ", m.group(0))[:190], "page": page,
                      "snippet": _snippet(text, m)})
    return facts


def extract_from_pdf(path: Path, max_pages: int = 220) -> list[dict]:
    """Read a PDF page by page (pypdf) and collect fact candidates. Scanned/image-only pages yield no text."""
    from pypdf import PdfReader
    facts: list[dict] = []
    reader = PdfReader(str(path))
    for i, page in enumerate(reader.pages[:max_pages], 1):
        try:
            text = page.extract_text() or ""
        except Exception:  # a single bad page should not sink the whole report
            continue
        if text:
            facts += extract_from_text(text, i)
    return facts


def dedupe(facts: list[dict], per_type: int = 6) -> list[dict]:
    """Keep the first few distinct facts of each type (annual reports repeat the same sentence many times)."""
    out, seen, count = [], set(), {}
    for f in facts:
        key = (f["fact_type"], f.get("value_num"), f.get("value_text"))
        if key in seen or count.get(f["fact_type"], 0) >= per_type:
            continue
        seen.add(key)
        count[f["fact_type"]] = count.get(f["fact_type"], 0) + 1
        out.append(f)
    return out
