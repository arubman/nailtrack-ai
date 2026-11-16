"""List the ten fingers and any Day-0 baseline that belongs to the caller.

Creating a baseline is the calibrate flow, which needs the vision pipeline and
lands in Phase 2. This router only reads.
"""

from fastapi import APIRouter
from sqlalchemy import select

from app.deps import CurrentStorage, CurrentUser, DbSession
from app.models import FingerBaseline, FingerCode
from app.schemas import FingerSummary, ImageLink
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
        baseline_image=_image_link(storage, baseline.baseline_image_key if baseline else None),
    )


def _image_link(storage: Storage, key: str | None) -> ImageLink | None:
    if not key:
        return None
    if storage.kind == "azure":
        ttl = getattr(storage, "ttl_seconds", None)
        return ImageLink(url=storage.read_url(key), access="sas", expires_in_seconds=ttl)
    return ImageLink(
        url=storage.read_url(key),
        access="authenticated_route",
        expires_in_seconds=None,
    )
