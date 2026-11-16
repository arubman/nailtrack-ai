"""Environment-backed settings.

Local development boots with AUTH_MODE=dev, a SQLite file, and filesystem
storage. Production refuses that combination: it requires Entra External ID,
PostgreSQL, and a private Azure Blob container.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")


class Settings(BaseSettings):
    """Runtime configuration. Construct via get_settings() so tests can clear the cache."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    database_url: str = "sqlite:///./nailtrack.db"
    auto_create_tables: bool | None = None
    sql_echo: bool = False

    auth_mode: Literal["dev", "entra"] = "dev"
    dev_user_subject: str = "local-dev-user"
    dev_user_email: str = "dev@localhost"
    dev_user_display_name: str = "Local Dev User"

    entra_tenant_id: str | None = None
    entra_tenant_subdomain: str | None = None
    entra_client_id: str | None = None
    entra_audience: str | None = None
    entra_issuer: str | None = None
    entra_jwks_url: str | None = None
    entra_required_scope: str | None = None
    entra_allowed_azp: str | None = None
    entra_jwks_cache_seconds: int = Field(default=3600, ge=60, le=86400)
    entra_clock_skew_seconds: int = Field(default=60, ge=0, le=300)

    storage_backend: Literal["auto", "local", "azure"] = "auto"
    azure_storage_connection_string: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AZURE_STORAGE_CONN_STRING",
            "AZURE_STORAGE_CONNECTION_STRING",
        ),
    )
    azure_storage_container: str = "nailtrack-private"
    sas_url_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    local_storage_path: str = "data/uploads"
    media_url_prefix: str = "/api/v1/media"
    max_image_edge_px: int = Field(default=1600, ge=256, le=4096)
    # Resolved relative to the backend directory when the path is not already a file.
    hand_model_path: str = "models/hand_landmarker.task"

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Server-side quality re-check of a submitted photo, and the same numbers
    # the client HUD should read from GET /api/v1/me rather than hardcode.
    blur_laplacian_min: float = Field(
        default=110.0,
        gt=0,
        validation_alias=AliasChoices("BLUR_LAPLACIAN_MIN", "NAILTRACK_BLUR_LAPLACIAN_MIN"),
    )
    glare_ratio_max: float = Field(
        default=0.08,
        gt=0,
        lt=1,
        validation_alias=AliasChoices("GLARE_RATIO_MAX", "NAILTRACK_GLARE_RATIO_MAX"),
    )
    tilt_deg_max: float = Field(
        default=10.0,
        gt=0,
        le=90,
        validation_alias=AliasChoices("TILT_DEG_MAX", "NAILTRACK_TILT_DEG_MAX"),
    )
    ridge_score_insight_threshold: float = Field(
        default=0.35,
        ge=0,
        le=1,
        validation_alias=AliasChoices(
            "RIDGE_SCORE_INSIGHT_THRESHOLD",
            "NAILTRACK_RIDGE_SCORE_INSIGHT_THRESHOLD",
        ),
        description=(
            "Wellness insight threshold for the 0..1 longitudinal Gabor ridge score. "
            "Normalization is documented in app.constants.GABOR_RIDGE_SCORE_NORMALIZATION."
        ),
    )
    gabor_energy_floor: float = Field(
        default=1e-4,
        gt=0,
        validation_alias=AliasChoices("GABOR_ENERGY_FLOOR", "NAILTRACK_GABOR_ENERGY_FLOOR"),
        description=(
            "Below this combined L2-normalized Gabor energy the ridge score is 0 "
            "(smooth nail). Phase 2 should confirm the default on real captures."
        ),
    )
    default_velocity_mm_day: float = Field(
        default=0.11,
        gt=0,
        validation_alias=AliasChoices(
            "DEFAULT_VELOCITY_MM_DAY",
            "NAILTRACK_DEFAULT_VELOCITY_MM_DAY",
        ),
    )
    population_velocity_mm_day: float = Field(
        default=0.115,
        gt=0,
        validation_alias=AliasChoices(
            "POPULATION_VELOCITY_MM_DAY",
            "NAILTRACK_POPULATION_VELOCITY_MM_DAY",
        ),
    )

    @field_validator(
        "entra_tenant_id",
        "entra_tenant_subdomain",
        "entra_client_id",
        "entra_audience",
        "entra_issuer",
        "entra_jwks_url",
        "entra_required_scope",
        "entra_allowed_azp",
        "azure_storage_connection_string",
        "dev_user_subject",
        "dev_user_email",
        "dev_user_display_name",
    )
    @classmethod
    def _blank_as_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value[len("postgres://") :]
        if value.startswith("postgresql+psycopg2://"):
            return "postgresql+psycopg://" + value[len("postgresql+psycopg2://") :]
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value[len("postgresql://") :]
        return value

    @field_validator("azure_storage_container")
    @classmethod
    def _container_name(cls, value: str) -> str:
        if _CONTAINER_RE.fullmatch(value) is None:
            raise ValueError(
                "AZURE_STORAGE_CONTAINER must be 3-63 lowercase letters, digits, or hyphens"
            )
        return value

    @field_validator("log_level")
    @classmethod
    def _log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        normalized = value.strip().upper()
        if normalized not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return normalized

    @property
    def resolved_hand_model_path(self) -> Path:
        configured = Path(self.hand_model_path)
        if configured.is_file():
            return configured
        packaged = Path(__file__).resolve().parents[1] / "models" / "hand_landmarker.task"
        if packaged.is_file():
            return packaged
        return configured

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def resolved_audiences(self) -> list[str]:
        raw = self.entra_audience or self.entra_client_id
        if not raw:
            return []
        return [part.strip() for part in raw.split(",") if part.strip()]

    @property
    def resolved_issuer(self) -> str | None:
        if self.entra_issuer:
            return self.entra_issuer
        if self.entra_tenant_subdomain and self.entra_tenant_id:
            return (
                f"https://{self.entra_tenant_subdomain}.ciamlogin.com/"
                f"{self.entra_tenant_id}/v2.0"
            )
        return None

    @property
    def resolved_jwks_url(self) -> str | None:
        if self.entra_jwks_url:
            return self.entra_jwks_url
        if self.entra_tenant_subdomain and self.entra_tenant_id:
            return (
                f"https://{self.entra_tenant_subdomain}.ciamlogin.com/"
                f"{self.entra_tenant_id}/discovery/v2.0/keys"
            )
        return None

    @property
    def resolved_storage_backend(self) -> Literal["local", "azure"]:
        if self.storage_backend == "local":
            return "local"
        if self.storage_backend == "azure":
            return "azure"
        if self.azure_storage_connection_string:
            return "azure"
        return "local"

    @property
    def create_tables_on_startup(self) -> bool:
        """SQLite dev/test databases create tables on boot. Postgres uses Alembic."""
        if self.auto_create_tables is None:
            return self.database_url.startswith("sqlite")
        return self.auto_create_tables

    @model_validator(mode="after")
    def _enforce_deployment_gates(self) -> Settings:
        problems: list[str] = []
        if self.environment == "production":
            if self.auth_mode != "entra":
                problems.append(
                    "AUTH_MODE=dev is forbidden when ENVIRONMENT=production; set AUTH_MODE=entra"
                )
            if self.database_url.startswith("sqlite"):
                problems.append("DATABASE_URL must be PostgreSQL when ENVIRONMENT=production")
            if self.resolved_storage_backend != "azure":
                problems.append(
                    "AZURE_STORAGE_CONN_STRING is required when ENVIRONMENT=production "
                    "(local disk is ephemeral and is not the private image store)"
                )
        if self.auth_mode == "dev":
            if not self.dev_user_subject:
                problems.append("DEV_USER_SUBJECT is required when AUTH_MODE=dev")
        if self.auth_mode == "entra":
            if not self.entra_tenant_id:
                problems.append("ENTRA_TENANT_ID is required when AUTH_MODE=entra")
            if not self.resolved_audiences:
                problems.append(
                    "ENTRA_CLIENT_ID or ENTRA_AUDIENCE is required when AUTH_MODE=entra"
                )
            if not self.resolved_issuer:
                problems.append(
                    "ENTRA_ISSUER or ENTRA_TENANT_SUBDOMAIN is required when AUTH_MODE=entra"
                )
            if not self.resolved_jwks_url:
                problems.append(
                    "ENTRA_JWKS_URL or ENTRA_TENANT_SUBDOMAIN is required when AUTH_MODE=entra"
                )
        if self.resolved_storage_backend == "azure" and not self.azure_storage_connection_string:
            problems.append("STORAGE_BACKEND=azure requires AZURE_STORAGE_CONN_STRING")
        elif self.azure_storage_connection_string and self.resolved_storage_backend == "azure":
            parts = {
                segment.split("=", 1)[0]
                for segment in self.azure_storage_connection_string.split(";")
                if "=" in segment
            }
            if "AccountName" not in parts or "AccountKey" not in parts:
                problems.append(
                    "AZURE_STORAGE_CONN_STRING must include AccountName and AccountKey "
                    "so the API can sign short-lived read SAS URLs"
                )
        if problems:
            raise ValueError("; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
