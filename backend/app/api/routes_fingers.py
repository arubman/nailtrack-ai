"""List the ten fingers and any Day-0 baseline that belongs to the caller.

Baselines are created by POST /api/v1/calibrate. This router only reads them.
"""

from fastapi import APIRouter
from sqlalchemy import select

from app.deps import CurrentStorage, CurrentUser, DbSession
from app.images import image_link
from app.models import FingerBaseline, FingerCode
from app.schemas import FingerSummary
from app.storage import Storage

router = APIRouter(prefix="/fingers", tags=["fingers"])


@router.get("", response_model=list[FingerSummary])
def list_fingers(user: CurrentUser, db: DbSession, storage: CurrentStorage) -> list[FingerSummary]:
    rows = db.scalars(select(FingerBaseline).where(FingerBaseline.user_id == user.id)).all()
    by_code = {row.finger_code: row for row in rows}
    return [_summary(code, by_code.get(code), storage) for code in FingerCode]


@router.get("/{finger_code}", response_model=FingerSummary)
def read_finger(
    finger_code: FingerCode,
    user: CurrentUser,
    db: DbSession,
    storage: CurrentStorage,
) -> FingerSummary:
    row = db.scalar(
        select(FingerBaseline).where(
            FingerBaseline.user_id == user.id,
            FingerBaseline.finger_code == finger_code,
        )
    )
    return _summary(finger_code, row, storage)


def _summary(
    finger_code: FingerCode,
    baseline: FingerBaseline | None,
    storage: Storage,
) -> FingerSummary:
    return FingerSummary(
        finger_code=finger_code,
        baseline_id=baseline.id if baseline else None,
        calibrated_width_mm=baseline.calibrated_width_mm if baseline else None,
        observed_velocity_mm_day=baseline.observed_velocity_mm_day if baseline else None,
        has_reference_white=bool(baseline and baseline.reference_white_json),
        scale_reference_type=baseline.scale_reference_type if baseline else None,
        scale_reference_width_mm=baseline.scale_reference_width_mm if baseline else None,
        baseline_image=image_link(storage, baseline.baseline_image_key if baseline else None),
    )
