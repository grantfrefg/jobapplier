import pytest

from applypilot import database


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Never touch the real ~/.applypilot database from a test."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    yield
    database.close_connection(tmp_path / "test.db")
