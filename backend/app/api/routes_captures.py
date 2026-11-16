"""Capture analysis and user-corrected masks."""

import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.deps import CurrentStorage, CurrentUser, DbSession
from app.images import image_link
from app.models import FingerCode
from app.schemas import CaptureMeasurementRead
from app.services.hand_recognizer import HandModelUnavailable, recognize_finger
from app.services.imaging import ImageDecodeError, decode_bgr
from app.services.pipeline import (
    PipelineRejection,
    StoredCapture,
    analyze_capture,
    apply_corrected_mask,
    owned_capture,
)
from app.storage import Storage

router = APIRouter(prefix="/captures", tags=["captures"])


@router.post("/analyze", response_model=CaptureMeasurementRead)
def analyze(
    user: CurrentUser,
    db: DbSession,
    storage: CurrentStorage,
    image: UploadFile = File(...),
    finger_code: FingerCode = Form(...),
    force_save: bool = Form(default=False),
):
    settings = get_settings()
    try:
        raster = decode_bgr(image.file.read(), max_edge=settings.max_image_edge_px)
    except ImageDecodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        observation = recognize_finger(raster, finger_code.value, settings.resolved_hand_model_path)
    except HandModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        stored = analyze_capture(
            db,
            storage,
            user,
            raster,
            finger_code,
            observation,
            force_save=force_save,
            settings=settings,
        )
    except PipelineRejection as rejection:
        return JSONResponse(status_code=rejection.status_code, content=rejection.body)
    return _capture_body(stored, storage)


@router.post("/{capture_id}/mask", response_model=CaptureMeasurementRead)
def submit_corrected_mask(
    capture_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    storage: CurrentStorage,
    mask: UploadFile = File(...),
):
    settings = get_settings()
    try:
        row = owned_capture(db, user.id, capture_id)
        stored = apply_corrected_mask(db, storage, user, row, mask.file.read(), settings)
    except PipelineRejection as rejection:
        return JSONResponse(status_code=rejection.status_code, content=rejection.body)
    return _capture_body(stored, storage)


def _capture_body(stored: StoredCapture, storage: Storage) -> CaptureMeasurementRead:
    capture = stored.capture
    return CaptureMeasurementRead(
        id=capture.id,
        finger_code=capture.finger_code,
        baseline_id=capture.baseline_id,
        captured_at=capture.captured_at,
        quality=stored.quality,
        nail_bed_length_mm=capture.nail_bed_length_mm,
        free_margin_length_mm=capture.free_margin_length_mm,
        total_length_mm=capture.total_length_mm,
        lunula_ratio=capture.lunula_ratio,
        ridge_gabor_score=capture.ridge_gabor_score,
        transverse_gabor_score=capture.transverse_gabor_score,
        cielab=stored.cielab,
        homography=stored.homography,
        mask_source=capture.mask_source,
        raw_image=image_link(storage, capture.raw_image_key),
        aligned_image=image_link(storage, capture.aligned_image_key),
        segmentation_mask=image_link(storage, capture.segmentation_mask_key),
        corrected_mask=image_link(storage, capture.corrected_mask_key),
    )
