"""GET /api/v1/fingers/{finger_code}/timeline — history, today, and forecasts."""

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.deps import CurrentStorage, CurrentUser, DbSession
from app.models import FingerCode
from app.schemas import TimelineRead
from app.services.timeline import build_finger_timeline

router = APIRouter(prefix="/fingers", tags=["forecast"])


@router.get("/{finger_code}/timeline", response_model=TimelineRead)
def finger_timeline(
    finger_code: FingerCode,
    user: CurrentUser,
    db: DbSession,
    storage: CurrentStorage,
) -> TimelineRead:
    timeline = build_finger_timeline(db, storage, user, finger_code, get_settings())
    if timeline is None:
        raise HTTPException(status_code=404, detail="This finger has not been calibrated")
    return timeline
