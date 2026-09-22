"""Normalisation helpers for scraped project data."""
from __future__ import annotations

import difflib
import re
from datetime import date

import pandas as pd

from ..database import DISTRICTS_BY_PROVINCE

DISTRICT_TO_PROVINCE = {d: p for p, ds in DISTRICTS_BY_PROVINCE.items() for d in ds}
_DISTRICT_LOOKUP = {d.lower(): d for d in DISTRICT_TO_PROVINCE}
# Spellings seen in DoED / Wikipedia that differ from the canonical district names.
_DISTRICT_ALIASES = {
    "makawanpur": "Makwanpur", "sindhupalchowk": "Sindhupalchok", "sindhupalchowk": "Sindhupalchok",
    "kavre": "Kavrepalanchok", "kabhrepalanchok": "Kavrepalanchok", "kavrepalanchowk": "Kavrepalanchok",
    "dhanusa": "Dhanusha", "tehrathum": "Terhathum", "terathum": "Terhathum", "chitawan": "Chitwan",
    "tanahu": "Tanahun", "tanahun": "Tanahun", "nawalparasi": "Nawalpur", "nawalparasi east": "Nawalpur",
    "nawalparasi west": "Parasi", "rukum": "Rukum West", "rukkum": "Rukum West", "arghakhanchi": "Arghakhanchi",
    "sankhuwasava": "Sankhuwasabha", "sankhuwasabha": "Sankhuwasabha", "solukhumbu": "Solukhumbu",
    "khotang": "Khotang", "udaypur": "Udayapur", "okhaldunga": "Okhaldhunga", "bajhang": "Bajhang",
    "dolpa": "Dolpa", "dolpo": "Dolpa", "dadeldhura": "Dadeldhura", "kapilbastu": "Kapilvastu",
    "sindhuli": "Sindhuli", "sindhupalchok": "Sindhupalchok", "myagdi": "Myagdi", "rasuwa": "Rasuwa",
    "dailekh": "Dailekh", "kaski": "Kaski", "taplejung": "Taplejung", "manang": "Manang",
}
_COMPANY_NOISE = re.compile(
    r"\b(pvt|private|ltd|limited|co|company|corp|corporation|the|p|l)\b|[^a-z0-9 ]", re.I)
_NAME_NOISE = re.compile(
    r"\b(hydropower|hydroelectric|hydro|power|electric|project|station|plant|hep|hpp|shp|hps|hpc|"
    r"small|mini|micro|ltd|limited|pvt|the|of|cascade|prop|pror|ror|phase)\b|[^a-z0-9 ]", re.I)

NEA_NAME = "Nepal Electricity Authority"


def strip_refs(text) -> str | None:
    """Remove Wikipedia footnote markers like [12] and collapse whitespace."""
    if text is None or (not isinstance(text, str) and pd.isna(text)):
        return None
    text = re.sub(r"\[[^\]]*\]", "", str(text))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}


def _fold(s: str) -> str:
    """Fold Nepali transliteration variants: sh/s, dh/d, kh/k, chh/ch and doubled letters."""
    s = re.sub(r"([bcdgjkpstz])h+", r"\1", s)
    return re.sub(r"(.)\1+", r"\1", s)


# Positional words that make two otherwise-identical names different projects (compared after folding).
QUALIFIERS = {_fold(q) for q in ("upper", "lower", "middle", "super", "sub", "mathillo", "tallo")}


def name_tokens(name: str | None) -> list[str]:
    """Tokens of a project name folded so that spelling variants compare equal
    (Trisuli/Trishuli, Budi/Budhi, Rasuwagadi/Rasuwagadhi, Tattopani/Tatopani)."""
    if not name:
        return []
    s = strip_refs(name).lower().replace("-", " ").replace("_", " ")
    s = re.sub(r"\b(i{1,3}|iv|v)\b", lambda m: _ROMAN[m.group()], s)
    s = _NAME_NOISE.sub(" ", s)
    s = re.sub(r"\bmadhya\b", "middle", s)  # Nepali for Middle: Madhya Marsyangdi == Middle Marsyangdi
    s = re.sub(r"\b([a-z]{3,})khola\b", r"\1", s)  # 'Setikhola' == 'Seti Khola'
    s = re.sub(r"\bkhola\b", " ", s)  # 'Khola' (river) is noise: 'Kabeli' == 'Kabeli Khola'
    return _fold(s).split()


def normalize_project_name(name: str | None) -> str:
    """Order-insensitive key used to match the same project across sources."""
    return " ".join(sorted(name_tokens(name)))


def discriminators(tokens) -> frozenset[str]:
    """Tokens that make two otherwise-similar names different projects (Upper/Lower, 1/2/3, A/B)."""
    return frozenset(t for t in tokens if t in QUALIFIERS or t.isdigit() or len(t) <= 2)


def normalize_company_name(name: str | None) -> str | None:
    """Return a display name; NEA aliases collapse to the canonical NEA name."""
    name = strip_refs(name)
    if not name:
        return None
    name = re.sub(r"\([^)]*\)", "", name).strip(" ,;")  # drop '(CHPCL)' style suffixes
    if re.fullmatch(r"(nea|nepal electricity authority|nepal electric authority)", name, re.I):
        return NEA_NAME
    first = re.split(r"\s+(?:and|&)\s+|;", name, maxsplit=1)[0].strip(" ,")
    return first or None


def company_key(name: str | None) -> str:
    if not name:
        return ""
    return " ".join(_COMPANY_NOISE.sub(" ", name.lower()).split())


def parse_capacity(value) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    m = re.search(r"\d+(?:[.,]\d+)?", str(value).replace(",", ""))
    return float(m.group()) if m else None


def parse_year(value) -> int | None:
    m = re.search(r"\b(19[0-9]{2}|20[0-9]{2})\b", str(value)) if value is not None else None
    return int(m.group()) if m else None


def bs_to_ad(text) -> date | None:
    """Convert a Bikram Sambat 'YYYY-MM-DD' string (DoED format) to a Gregorian date."""
    if text is None or (not isinstance(text, str) and pd.isna(text)):
        return None
    m = re.fullmatch(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*", str(text))
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    if y == 0 or mo == 0 or d == 0:
        return None
    import nepali_datetime
    try:
        return nepali_datetime.date(y, mo, d).to_datetime_date()
    except (ValueError, OverflowError):
        return None


def dms_to_decimal(text) -> float | None:
    """'27o 28' 28\"' -> 27.4744. Returns None for missing / 00o 00' 00\" placeholders."""
    if text is None or (not isinstance(text, str) and pd.isna(text)):
        return None
    m = re.match(r"\s*(\d+)\D+(\d+)\D+(\d+(?:\.\d+)?)", str(text))
    if not m:
        return None
    deg, minutes, seconds = float(m.group(1)), float(m.group(2)), float(m.group(3))
    if deg == 0:
        return None
    return round(deg + minutes / 60 + seconds / 3600, 5)


def midpoint(a: float | None, b: float | None) -> float | None:
    vals = [v for v in (a, b) if v is not None]
    return round(sum(vals) / len(vals), 5) if vals else None


def resolve_district(*texts) -> str | None:
    """Find a canonical district name inside free text such as 'Sahure,Hawa (Dolakha)'."""
    for text in texts:
        text = strip_refs(text)
        if not text:
            continue
        # Prefer names in parentheses: 'Narchyang (Myagdi)', last one wins only if first misses.
        candidates = re.findall(r"\(([^)]+)\)", text) + [text]
        for cand in candidates:
            for token in re.split(r"[,/;]|\band\b", cand):
                token = re.sub(r"\bdistrict\b", "", token, flags=re.I).strip().lower()
                if not token:
                    continue
                if token in _DISTRICT_LOOKUP:
                    return _DISTRICT_LOOKUP[token]
                if token in _DISTRICT_ALIASES:
                    return _DISTRICT_ALIASES[token]
                close = difflib.get_close_matches(token, _DISTRICT_LOOKUP.keys(), n=1, cutoff=0.86)
                if close:
                    return _DISTRICT_LOOKUP[close[0]]
    return None


def province_of(district: str | None) -> str | None:
    return DISTRICT_TO_PROVINCE.get(district) if district else None
