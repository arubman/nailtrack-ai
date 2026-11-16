"""FastAPI entrypoint.

Calibration and capture analysis run the classical vision pipeline.
Forecasts and the timeline are still later phases.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes_calibrate import router as calibrate_router
from app.api.routes_captures import router as captures_router
from app.api.routes_fingers import router as fingers_router
from app.api.routes_health import router as health_router
from app.api.routes_me import router as me_router
from app.api.routes_media import router as media_router
from app.api.routes_treatments import router as treatments_router
from app.config import get_settings
from app.constants import NOT_MEDICAL_ADVICE
from app.database import Base, get_engine
from app.storage import build_storage

logger = logging.getLogger(__name__)

DESCRIPTION = f"""
NailTrack records fingernail photos, measurements, and the regimens you choose
to log, then shows how a nail has changed and how it may grow.

{NOT_MEDICAL_ADVICE}

Accounts, calibration, capture measurement, the treatment log, and private
image reads are served here. Forecasts are a later phase.
""".strip()


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    logging.getLogger("app").setLevel(level)


@asynccontextmanager
async def lifespan(application: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_level)
    # Register models on Base.metadata before create_all.
    import app.models  # noqa: F401

    if settings.create_tables_on_startup:
        Base.metadata.create_all(bind=get_engine())
        logger.info("Database tables are ready")
    storage = build_storage(settings)
    storage.ensure_ready()
    application.state.storage = storage
    if settings.auth_mode == "dev":
        message = (
            "AUTH_MODE=dev: every request runs as the single local dev user and "
            "Entra tokens are not validated. This mode cannot start when ENVIRONMENT=production."
        )
        if settings.environment == "test":
            logger.info(message)
        else:
            logger.warning(message)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    _configure_logging(settings.log_level)
    application = FastAPI(
        title="NailTrack AI",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )
    application.include_router(health_router)
    application.include_router(me_router, prefix="/api/v1")
    application.include_router(fingers_router, prefix="/api/v1")
    application.include_router(calibrate_router, prefix="/api/v1")
    application.include_router(captures_router, prefix="/api/v1")
    application.include_router(treatments_router, prefix="/api/v1")
    application.include_router(media_router, prefix="/api/v1")

    @application.get("/", include_in_schema=False)
    def root() -> dict[str, str]:
        return {"service": "nailtrack-ai", "docs": "/docs", "health": "/health"}

    return application


app = create_app()
