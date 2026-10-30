import pytest

from applypilot import config, database


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Never touch the real ~/.applypilot database or ~/ApplyPilot uploads from a test."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "APP_DIR", tmp_path)  # e.g. discover_failed.json
    monkeypatch.setattr(config, "COVER_LETTER_DIR", tmp_path / "cover_letters")  # recent letter openings
    for key in ("TAILOR_STYLE", "RESUME_LAYOUT", "RESUME_ACCENT", "RESUME_TAGLINE", "RESUME_TECH_LINE",
                "SHOW_WORK_RIGHTS"):
        monkeypatch.setenv(key, "")  # set (not deleted), so values a test's API call writes are undone
    yield
    database.close_connection(tmp_path / "test.db")
