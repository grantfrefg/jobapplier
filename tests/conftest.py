import pytest

from applypilot import config, database


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Never touch the real ~/.applypilot database or ~/ApplyPilot uploads from a test."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "APP_DIR", tmp_path)  # e.g. discover_failed.json
    monkeypatch.setattr(config, "COVER_LETTER_DIR", tmp_path / "cover_letters")  # recent letter openings
    monkeypatch.delenv("TAILOR_STYLE", raising=False)
    yield
    database.close_connection(tmp_path / "test.db")
