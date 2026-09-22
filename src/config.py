"""Central configuration for the Nepal Hydropower database."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
EXPORT_DIR = BASE_DIR / "exports" / "generated"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'projects.db').as_posix()}")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"

FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

SCRAPING_ENABLED = os.getenv("SCRAPING_ENABLED", "True").lower() == "true"
REQUEST_TIMEOUT = 20
USER_AGENT = "NepalHydropowerDB/1.0 (research project)"

# Validation thresholds
MAX_PROJECT_CAPACITY_MW = 5000.0
MIN_PROJECT_CAPACITY_MW = 0.01
MAX_CAPACITY_FACTOR_PCT = 100.0
NEPAL_LAT_RANGE = (26.3, 30.5)
NEPAL_LON_RANGE = (80.0, 88.3)

# Nepal's grid: 1 NPR = ? USD is volatile; used only as a fallback for display.
DEFAULT_NPR_PER_USD = 133.0

for _d in (DATA_DIR, RAW_DIR, PROCESSED_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Revenue-estimate ASSUMPTIONS. The two tariffs are NEA's standard run-of-river PPA rates (NPR 8.40 dry / 4.80 wet per kWh,
#     confirmed in news reports, Sept 2026); project-specific PPAs differ (Upper Tamakoshi: wet 3.63 / dry 6.96). The dry-season
#     energy share and capacity factor remain assumptions.
# --- Revenue-estimate ASSUMPTIONS (not sourced data) ---------------------------------------------
# Used only by `analyze revenue`, whose output is always labelled an estimate. Replace with the
# current NEA PPA rate schedule for your analysis; every value can also be overridden on the CLI.
ASSUMED_WET_TARIFF_NPR_PER_KWH = 4.80
ASSUMED_DRY_TARIFF_NPR_PER_KWH = 8.40
ASSUMED_DRY_SEASON_ENERGY_SHARE = 0.30  # fraction of a run-of-river plant's annual energy in the dry season
ASSUMED_CAPACITY_FACTOR = 0.55
