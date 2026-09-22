"""Database connection, initialisation and reference-data loading."""
from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from . import config
from .models import Base, Company, CompanyType, District, River

# 77 districts grouped by province (Nepal's 2015 constitution).
DISTRICTS_BY_PROVINCE = {
    "Koshi": ["Taplejung", "Panchthar", "Ilam", "Jhapa", "Morang", "Sunsari", "Dhankuta", "Terhathum",
              "Sankhuwasabha", "Bhojpur", "Solukhumbu", "Okhaldhunga", "Khotang", "Udayapur"],
    "Madhesh": ["Saptari", "Siraha", "Dhanusha", "Mahottari", "Sarlahi", "Rautahat", "Bara", "Parsa"],
    "Bagmati": ["Sindhuli", "Ramechhap", "Dolakha", "Sindhupalchok", "Kavrepalanchok", "Lalitpur",
                "Bhaktapur", "Kathmandu", "Nuwakot", "Rasuwa", "Dhading", "Makwanpur", "Chitwan"],
    "Gandaki": ["Gorkha", "Lamjung", "Tanahun", "Syangja", "Kaski", "Manang", "Mustang", "Myagdi",
                "Parbat", "Baglung", "Nawalpur"],
    "Lumbini": ["Parasi", "Rupandehi", "Kapilvastu", "Palpa", "Arghakhanchi", "Gulmi", "Pyuthan",
                "Rolpa", "Rukum East", "Dang", "Banke", "Bardiya"],
    "Karnali": ["Rukum West", "Salyan", "Surkhet", "Dailekh", "Jajarkot", "Dolpa", "Jumla", "Kalikot",
                "Mugu", "Humla"],
    "Sudurpashchim": ["Bajura", "Bajhang", "Achham", "Doti", "Kailali", "Kanchanpur", "Dadeldhura",
                      "Baitadi", "Darchula"],
}

RIVERS_BY_BASIN = {
    "Koshi": ["Tamor", "Arun", "Dudh Koshi", "Likhu", "Sun Koshi", "Tamakoshi", "Indrawati",
              "Bhote Koshi", "Hewa", "Khimti", "Kabeli", "Mai", "Balephi"],
    "Bagmati": ["Bagmati", "Kulekhani"],
    "Gandaki": ["Trishuli", "Budhi Gandaki", "Kali Gandaki", "Seti", "Madi", "Modi", "Marsyangdi",
                "Daraudi", "Andhi Khola"],
    "Karnali": ["Karnali", "Bheri", "Tila", "Seti (West)"],
    "Mahakali": ["Mahakali", "Chamelia"],
    "Mechi": ["Mechi"],
}

# Well-known companies. NEPSE listing (symbol, shares) is NOT hard-coded here: it comes from the
# NEPSE sync (`main.py sync --source nepse`), which avoids typing symbols from memory.
REFERENCE_COMPANIES = [
    ("Nepal Electricity Authority", CompanyType.NEA),
    ("Government of Nepal", CompanyType.GOVERNMENT),
    ("Himal Power Limited", CompanyType.IPP),
    ("Bhote Koshi Power Company", CompanyType.IPP),
    ("Kabeli Energy Limited", CompanyType.IPP),
    ("SJVN Arun-3 Power Development Company", CompanyType.IPP),
    ("Tanahu Hydropower Limited", CompanyType.GOVERNMENT),
]


class DatabaseManager:
    def __init__(self, url: str | None = None):
        self.url = url or config.DATABASE_URL
        self.engine = create_engine(self.url, echo=config.DEBUG, future=True)
        if self.url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _fk_on(dbapi_conn, _):  # enforce FK constraints in SQLite
                dbapi_conn.execute("PRAGMA foreign_keys=ON")
        self._Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def init_db(self, drop: bool = False) -> None:
        if drop:
            Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)
        self._add_missing_columns()
        self._relax_project_update_nullability()

    def _add_missing_columns(self) -> None:
        """Lightweight migration: ADD COLUMN for model columns an older database file doesn't have yet.

        create_all() only creates missing *tables*. This keeps existing data when a column is added to a model
        (nullable columns only; anything else needs Alembic).
        """
        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if not insp.has_table(table.name):
                    continue
                have = {c["name"] for c in insp.get_columns(table.name)}
                for col in table.columns:
                    if col.name not in have and col.nullable and not col.primary_key:
                        ddl = col.type.compile(dialect=self.engine.dialect)
                        conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}')

    def _relax_project_update_nullability(self) -> None:
        """One-time migration: project_updates.project_id used to be required so a company-only news item
        (no specific project named) couldn't be stored. SQLite can't ALTER a column's NOT NULL, so an older
        database file is migrated by rebuilding the table; a fresh database already has the nullable column.

        Each step is written to be safely re-runnable: pysqlite commits DDL statements as it goes (there is no
        real multi-statement transaction to roll back), so a process killed mid-migration can leave the rename
        done but the copy not — the next run must pick up from 'project_updates_old exists' rather than assume
        all-or-nothing.
        """
        insp = inspect(self.engine)
        if insp.has_table("project_updates") and not insp.has_table("project_updates_old"):
            col = next((c for c in insp.get_columns("project_updates") if c["name"] == "project_id"), None)
            if col is None or col["nullable"]:
                return  # already migrated (or a fresh database, created nullable from the start)
            with self.engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE project_updates RENAME TO project_updates_old")

        if not inspect(self.engine).has_table("project_updates_old"):
            return
        with self.engine.begin() as conn:
            # Renaming a SQLite table does not rename its indexes, which would collide with the new table's.
            for (idx_name,) in conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='project_updates_old' "
                    "AND sql IS NOT NULL"):
                conn.exec_driver_sql(f'DROP INDEX "{idx_name}"')
            Base.metadata.tables["project_updates"].create(conn, checkfirst=True)
            conn.exec_driver_sql(
                "INSERT INTO project_updates (update_id, project_id, update_date, update_type, title, "
                "description, source_url, source_name) SELECT update_id, project_id, update_date, update_type, "
                "title, description, source_url, source_name FROM project_updates_old")
            conn.exec_driver_sql("DROP TABLE project_updates_old")

    def get_session(self) -> Session:
        return self._Session()

    @contextmanager
    def session_scope(self):
        session = self.get_session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_db_stats(self) -> dict[str, int]:
        stats: dict[str, int] = {}
        with self.session_scope() as s:
            for table in inspect(self.engine).get_table_names():
                stats[table] = s.execute(select(func.count()).select_from(Base.metadata.tables[table])).scalar_one()
        return stats

    def init_reference_data(self) -> dict[str, int]:
        """Idempotently load districts, rivers and reference companies."""
        added = {"districts": 0, "rivers": 0, "companies": 0}
        with self.session_scope() as s:
            existing_d = {d.district_name for d in s.scalars(select(District))}
            for province, names in DISTRICTS_BY_PROVINCE.items():
                for name in names:
                    if name not in existing_d:
                        s.add(District(district_name=name, province=province))
                        added["districts"] += 1
            existing_r = {r.river_name for r in s.scalars(select(River))}
            for basin, names in RIVERS_BY_BASIN.items():
                for name in names:
                    if name not in existing_r:
                        s.add(River(river_name=name, basin=basin))
                        existing_r.add(name)
                        added["rivers"] += 1
            existing_c = {c.company_name for c in s.scalars(select(Company))}
            for name, ctype in REFERENCE_COMPANIES:
                if name not in existing_c:
                    s.add(Company(company_name=name, company_type=ctype))
                    added["companies"] += 1
        return added


db_manager = DatabaseManager()


def get_session() -> Session:
    return db_manager.get_session()
