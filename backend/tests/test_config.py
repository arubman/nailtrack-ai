from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings, clear_settings_cache
from app.constants import FORECAST_HORIZONS_DAYS, GABOR_RIDGE_SCORE_NORMALIZATION


def _settings(monkeypatch, **env: str) -> Settings:
    for key in (
        "ENVIRONMENT",
        "AUTH_MODE",
        "DATABASE_URL",
        "STORAGE_BACKEND",
        "AZURE_STORAGE_CONN_STRING",
        "AZURE_STORAGE_CONNECTION_STRING",
        "BLUR_LAPLACIAN_MIN",
        "GLARE_RATIO_MAX",
        "TILT_DEG_MAX",
        "RIDGE_SCORE_INSIGHT_THRESHOLD",
        "GABOR_ENERGY_FLOOR",
        "DEFAULT_VELOCITY_MM_DAY",
        "POPULATION_VELOCITY_MM_DAY",
        "ENTRA_TENANT_ID",
        "ENTRA_TENANT_SUBDOMAIN",
        "ENTRA_CLIENT_ID",
        "ENTRA_AUDIENCE",
        "ENTRA_ISSUER",
        "ENTRA_JWKS_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    clear_settings_cache()
    return Settings(_env_file=None)


def test_threshold_defaults(monkeypatch):
    settings = _settings(monkeypatch)
    assert settings.environment == "development"
    assert settings.auth_mode == "dev"
    assert settings.blur_laplacian_min == 110.0
    assert settings.glare_ratio_max == pytest.approx(0.08)
    assert settings.tilt_deg_max == 10.0
    assert settings.ridge_score_insight_threshold == pytest.approx(0.35)
    assert settings.gabor_energy_floor == pytest.approx(1e-4)
    assert settings.default_velocity_mm_day == pytest.approx(0.11)
    assert settings.population_velocity_mm_day == pytest.approx(0.115)
    assert settings.create_tables_on_startup is True
    assert FORECAST_HORIZONS_DAYS == (7, 14, 30, 60, 90)
    assert 15 not in FORECAST_HORIZONS_DAYS
    assert "0..1" in GABOR_RIDGE_SCORE_NORMALIZATION
    assert "periungual" not in GABOR_RIDGE_SCORE_NORMALIZATION.lower()


def test_env_overrides_thresholds(monkeypatch):
    settings = _settings(
        monkeypatch,
        BLUR_LAPLACIAN_MIN="80",
        GLARE_RATIO_MAX="0.05",
        TILT_DEG_MAX="8",
        RIDGE_SCORE_INSIGHT_THRESHOLD="0.5",
        DEFAULT_VELOCITY_MM_DAY="0.2",
        POPULATION_VELOCITY_MM_DAY="0.13",
    )
    assert settings.blur_laplacian_min == 80
    assert settings.glare_ratio_max == pytest.approx(0.05)
    assert settings.tilt_deg_max == 8
    assert settings.ridge_score_insight_threshold == pytest.approx(0.5)
    assert settings.default_velocity_mm_day == pytest.approx(0.2)
    assert settings.population_velocity_mm_day == pytest.approx(0.13)


def test_postgres_url_uses_psycopg(monkeypatch):
    settings = _settings(
        monkeypatch,
        DATABASE_URL="postgresql://user:pass@db.example:5432/nailtrack",
        ENVIRONMENT="development",
    )
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.create_tables_on_startup is False


def test_dev_auth_forbidden_in_production(monkeypatch):
    with pytest.raises(ValidationError) as caught:
        _settings(monkeypatch, ENVIRONMENT="production", AUTH_MODE="dev")
    assert "AUTH_MODE=dev is forbidden" in str(caught.value)


def test_production_requires_postgres_and_blob(monkeypatch):
    with pytest.raises(ValidationError) as caught:
        _settings(
            monkeypatch,
            ENVIRONMENT="production",
            AUTH_MODE="entra",
            ENTRA_TENANT_ID="tenant",
            ENTRA_TENANT_SUBDOMAIN="nailtrack",
            ENTRA_CLIENT_ID="client",
            DATABASE_URL="sqlite:///./nailtrack.db",
        )
    text = str(caught.value)
    assert "PostgreSQL" in text
    assert "AZURE_STORAGE_CONN_STRING" in text


def test_entra_issuer_defaults_to_external_id_host(monkeypatch):
    settings = _settings(
        monkeypatch,
        AUTH_MODE="entra",
        ENTRA_TENANT_ID="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        ENTRA_TENANT_SUBDOMAIN="nailtrack",
        ENTRA_CLIENT_ID="client-id",
        ENTRA_AUDIENCE="client-id,api://client-id",
    )
    assert settings.resolved_issuer == (
        "https://nailtrack.ciamlogin.com/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee/v2.0"
    )
    assert settings.resolved_jwks_url.endswith("/discovery/v2.0/keys")
    assert settings.resolved_audiences == ["client-id", "api://client-id"]


def test_entra_mode_requires_tenant_configuration(monkeypatch):
    with pytest.raises(ValidationError) as caught:
        _settings(monkeypatch, AUTH_MODE="entra")
    assert "ENTRA_TENANT_ID" in str(caught.value)
