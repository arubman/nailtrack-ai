"""POST /api/v1/calibrate — ID-1 (or manual) card, white reference, nail width."""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.deps import CurrentStorage, CurrentUser, DbSession
from app.images import image_link
from app.models import FingerCode, ScaleReferenceType
from app.schemas import CalibrationRead
from app.services.hand_recognizer import HandModelUnavailable, recognize_finger
from app.services.imaging import ImageDecodeError, decode_bgr
from app.services.pipeline import PipelineRejection, calibrate_finger

router = APIRouter(prefix="/calibrate", tags=["calibration"])


@router.post("", response_model=CalibrationRead)
def calibrate(
    user: CurrentUser,
    db: DbSession,
    storage: CurrentStorage,
    image: UploadFile = File(...),
    finger_code: FingerCode = Form(...),
    reference_type: ScaleReferenceType = Form(...),
    reference_width_mm: float | None = Form(default=None),
):
    settings = get_settings()
    raster = _read_upload(image, settings.max_image_edge_px)
    try:
        observation = recognize_finger(raster, finger_code.value, settings.resolved_hand_model_path)
    except HandModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        stored = calibrate_finger(
            db,
            storage,
            user,
            raster,
            finger_code,
            observation,
            reference_type=reference_type,
            reference_width_mm=reference_width_mm,
            settings=settings,
        )
    except PipelineRejection as rejection:
        return JSONResponse(status_code=rejection.status_code, content=rejection.body)
    link = image_link(storage, stored.baseline.baseline_image_key)
    if link is None:
        raise HTTPException(status_code=500, detail="Baseline image was not stored")
    return CalibrationRead(
        finger_code=finger_code,
        baseline_id=stored.baseline.id,
        calibrated_width_mm=stored.baseline.calibrated_width_mm,
        scale_reference_type=stored.baseline.scale_reference_type,
        scale_reference_width_mm=stored.baseline.scale_reference_width_mm,
        mm_per_px=stored.mm_per_px,
        reference_white=stored.reference_white,
        anchor_keypoints=stored.anchors,
        quality=stored.quality,
        baseline_image=link,
    )


def _read_upload(upload: UploadFile, max_edge: int):
    try:
        return decode_bgr(upload.file.read(), max_edge=max_edge)
    except ImageDecodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
