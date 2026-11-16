"""Treatment log scoped to the signed-in user.

POST stores the row the user sent. It does not recompute forecasts; that
side effect arrives with the Phase 3 forecaster. velocity_modifier is stored
as the user set it.
"""

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.database import utcnow
from app.deps import CurrentUser, DbSession
from app.models import FingerCode, Treatment, TreatmentCategory
from app.schemas import TreatmentCreate, TreatmentListResponse, TreatmentRead, TreatmentUpdate

router = APIRouter(prefix="/treatments", tags=["treatments"])


@router.post("", response_model=TreatmentRead, status_code=status.HTTP_201_CREATED)
def create_treatment(payload: TreatmentCreate, user: CurrentUser, db: DbSession) -> Treatment:
    row = Treatment(
        user_id=user.id,
        started_at=payload.started_at,
        ended_at=payload.ended_at,
        category=payload.category,
        target_fingers_json=payload.target_fingers,
        velocity_modifier=payload.velocity_modifier,
        notes=payload.notes,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("", response_model=TreatmentListResponse)
def list_treatments(
    user: CurrentUser,
    db: DbSession,
    category: TreatmentCategory | None = None,
    finger_code: FingerCode | None = None,
    active_only: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> TreatmentListResponse:
    statement = select(Treatment).where(Treatment.user_id == user.id)
    if category is not None:
        statement = statement.where(Treatment.category == category)
    if active_only:
        now = utcnow()
        statement = statement.where((Treatment.ended_at.is_(None)) | (Treatment.ended_at >= now))
    statement = statement.order_by(Treatment.started_at.desc(), Treatment.created_at.desc())
    rows = list(db.scalars(statement).all())
    if finger_code is not None:
        code = finger_code.value
        rows = [row for row in rows if code in row.target_fingers or "ALL" in row.target_fingers]
    total = len(rows)
    page = rows[offset : offset + limit]
    return TreatmentListResponse(
        items=[TreatmentRead.model_validate(row) for row in page],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{treatment_id}", response_model=TreatmentRead)
def read_treatment(treatment_id: str, user: CurrentUser, db: DbSession) -> Treatment:
    return _owned_treatment(db, user.id, treatment_id)


@router.patch("/{treatment_id}", response_model=TreatmentRead)
def update_treatment(
    treatment_id: str,
    payload: TreatmentUpdate,
    user: CurrentUser,
    db: DbSession,
) -> Treatment:
    row = _owned_treatment(db, user.id, treatment_id)
    data = payload.model_dump(exclude_unset=True)
    if "target_fingers" in data:
        row.target_fingers_json = data.pop("target_fingers")
    started = data["started_at"] if "started_at" in data else row.started_at
    ended = data["ended_at"] if "ended_at" in data else row.ended_at
    if ended is not None and started is not None and ended < started:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ended_at must be on or after started_at",
        )
    for field, value in data.items():
        setattr(row, field, value)
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{treatment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_treatment(treatment_id: str, user: CurrentUser, db: DbSession) -> None:
    row = _owned_treatment(db, user.id, treatment_id)
    db.delete(row)
    db.commit()


def _owned_treatment(db: DbSession, user_id: object, treatment_id: str) -> Treatment:
    try:
        parsed = uuid.UUID(treatment_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Treatment not found") from exc
    row = db.get(Treatment, parsed)
    # Missing and owned-by-someone-else look the same so ids are not an oracle.
    if row is None or row.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Treatment not found")
    return row
