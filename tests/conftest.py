import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import DatabaseManager  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    """A fresh, isolated SQLite database with reference data loaded."""
    manager = DatabaseManager(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    manager.init_db()
    manager.init_reference_data()
    return manager
