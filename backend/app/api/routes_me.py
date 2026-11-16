"""Authenticated session bootstrap.

The client HUD reads quality thresholds from here instead of hardcoding them.
Forecast horizons are the single set {+7, +14, +30, +60, +90}.
"""

from fastapi import APIRouter

from app.config import get_settings
from app.constants import (
    FORECAST_HORIZONS_DAYS,
    NOT_MEDICAL_ADVICE,
    TIMELINE_SLIDER_MAX_DAYS,
    TIMELINE_SLIDER_MIN_DAYS,
    TIMELINE_SLIDER_STEP_DAYS,
)
from app.deps import CurrentUser
from app.schemas import (
    QualityThresholds,
    SessionRead,
    StoragePublicInfo,
    TimelineSlider,
    UserRead,
)

router = APIRouter(prefix="/me", tags=["session"])


@router.get("", response_model=SessionRead)
def read_session(user: CurrentUser) -> SessionRead:
    settings = get_settings()
    backend = settings.resolved_storage_backend
    if backend == "azure":
        storage = StoragePublicInfo(
            backend="azure",
            read_access="sas",
            sas_ttl_seconds=settings.sas_url_ttl_seconds,
        )
    else:
        storage = StoragePublicInfo(
            backend="local",
            read_access="authenticated_route",
            sas_ttl_seconds=None,
        )
    return SessionRead(
        user=UserRead.model_validate(user),
        auth_mode=settings.auth_mode,
        disclaimer=NOT_MEDICAL_ADVICE,
        forecast_horizons_days=list(FORECAST_HORIZONS_DAYS),
        timeline_slider=TimelineSlider(
            min_days=TIMELINE_SLIDER_MIN_DAYS,
            max_days=TIMELINE_SLIDER_MAX_DAYS,
            step_days=TIMELINE_SLIDER_STEP_DAYS,
        ),
        quality_thresholds=QualityThresholds(
            blur_laplacian_min=settings.blur_laplacian_min,
            glare_ratio_max=settings.glare_ratio_max,
            tilt_deg_max=settings.tilt_deg_max,
        ),
        ridge_score_insight_threshold=settings.ridge_score_insight_threshold,
        gabor_energy_floor=settings.gabor_energy_floor,
        default_velocity_mm_day=settings.default_velocity_mm_day,
        population_velocity_mm_day=settings.population_velocity_mm_day,
        storage=storage,
    )
