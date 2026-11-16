"""Test defaults: dev auth, a throwaway SQLite file, local storage."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

os.environ["ENVIRONMENT"] = "test"
os.environ["AUTH_MODE"] = "dev"

from app.config import clear_settings_cache  # noqa: E402
from app.database import dispose_engine, get_engine  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'nailtrack.db'}")
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("AUTO_CREATE_TABLES", "true")
    clear_settings_cache()
    dispose_engine()
    from app.main import create_app

    application = create_app()
    with TestClient(application) as test_client:
        yield test_client
    dispose_engine()
    clear_settings_cache()


@pytest.fixture
def db(client):
    session = Session(bind=get_engine(), expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_MODE", "dev")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'models.db'}")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path / "uploads"))
    clear_settings_cache()
    dispose_engine()
    from app.models import Base

    Base.metadata.create_all(bind=get_engine())
    db_session = Session(bind=get_engine(), expire_on_commit=False)
    try:
        yield db_session
    finally:
        db_session.close()
        dispose_engine()
        clear_settings_cache()
