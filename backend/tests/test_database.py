from app.config import clear_settings_cache
from app.database import dispose_engine, get_engine


def test_engine_creates_the_sqlite_parent_directory(tmp_path, monkeypatch):
    database = tmp_path / "nested" / "nailtrack.db"
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    clear_settings_cache()
    dispose_engine()
    try:
        get_engine()
        assert database.parent.is_dir()
        assert not database.exists() or database.is_file()
    finally:
        dispose_engine()
        clear_settings_cache()
