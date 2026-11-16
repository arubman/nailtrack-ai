"""Liveness for Azure Container Apps and App Service. Unauthenticated on purpose.

Container Apps probes GET /health. The route checks the database with SELECT 1
and does not require an Entra token.

Platform probes do not send an Entra token. Every /api/v1 route still resolves
the caller through get_current_user. This route and the documentation pages
are the exceptions.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.config import get_settings
from app.database import get_db
from app.schemas import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Database health check failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from None
    settings = get_settings()
    return HealthResponse(
        status="ok",
        service="nailtrack-ai",
        version=__version__,
        environment=settings.environment,
        auth_mode=settings.auth_mode,
        database="ok",
    )
