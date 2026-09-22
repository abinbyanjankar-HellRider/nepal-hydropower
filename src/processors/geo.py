"""Geographic zones for projects.

Ecological belt (Mountain / Hill / Terai) follows the government's district-level classification (the CBS list:
16 Mountain, 39 Hill and 20 Terai districts, updated for districts split in 2015). A district is placed wholly in one belt
even when it spans several, so borderline districts (for example Gorkha, Jumla, Nawalpur, Udayapur) are approximate.
"""
from __future__ import annotations

from ..database import DISTRICTS_BY_PROVINCE

BELTS = ("Mountain", "Hill", "Terai")

MOUNTAIN = {
    "Taplejung", "Sankhuwasabha", "Solukhumbu", "Dolakha", "Sindhupalchok", "Rasuwa", "Gorkha", "Manang", "Mustang", "Dolpa",
    "Mugu", "Humla", "Jumla", "Bajura", "Bajhang", "Darchula",
}
TERAI = {
    "Jhapa", "Morang", "Sunsari", "Saptari", "Siraha", "Dhanusha", "Mahottari", "Sarlahi", "Rautahat", "Bara", "Parsa", "Chitwan",
    "Nawalpur", "Parasi", "Rupandehi", "Kapilvastu", "Dang", "Banke", "Bardiya", "Kailali", "Kanchanpur",
}
ALL_DISTRICTS = {d for ds in DISTRICTS_BY_PROVINCE.values() for d in ds}


def ecological_belt(district: str | None) -> str | None:
    """'Mountain', 'Hill' or 'Terai' for a known district name; None when the district is missing or unknown."""
    if not district or district not in ALL_DISTRICTS:
        return None
    return "Mountain" if district in MOUNTAIN else "Terai" if district in TERAI else "Hill"
