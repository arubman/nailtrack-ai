"""Calibrate a finger and measure a later capture.

Routes stay thin: they decode the upload, ask MediaPipe for the hand, and
call these functions. Quality failures return a 422 body. force_save overrides
blur, glare, tilt, and a missing hand. A missing nail or calibration card
still blocks the save.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.constants import ID1_CARD_WIDTH_MM, QUALITY_REASON_MESSAGES
from app.database import utcnow
from app.models import FingerBaseline, FingerCode, MaskSource, NailCapture, ScaleReferenceType, User
from app.schemas import (
    AnchorKeypoints,
    CielabColor,
    HomographyRecord,
    QualityMetrics,
    ReferenceWhite,
)
from app.services.biometric_metrics import measure_nail
from app.services.aruco_card import detect_aruco_card
from app.services.card_detector import detect_card
from app.services.feature_detector import detect_tracked_features
from app.services.forecaster import update_observed_velocity, write_forecast_cache
from app.services.geometry import (
    FingerPose,
    MeasurementError,
    dip_crease_points,
    width_and_folds,
)
from app.services.hand_recognizer import HandObservation
from app.services.homography_align import (
    AlignmentError,
    align_to_baseline,
    build_invariant_mask,
    warp_bgr,
    warp_mask,
)
from app.services.imaging import decode_mask_png, encode_jpeg, encode_mask_png
from app.services.nail_segmenter import segment_nail
from app.services.quality_gate import blocks_save, evaluate_quality
from app.storage import Storage, blob_key_for_user

MASK_VALUES = {0, 1, 2, 3}


class PipelineRejection(Exception):
    def __init__(self, body: dict, status_code: int = 422):
        self.body = body
        self.status_code = status_code
        super().__init__(str(body.get("reasons")))


@dataclass
class StoredCalibration:
    baseline: FingerBaseline
    mm_per_px: float
    reference_white: ReferenceWhite
    anchors: AnchorKeypoints
    quality: QualityMetrics


@dataclass
class StoredCapture:
    capture: NailCapture
    quality: QualityMetrics
    homography: HomographyRecord | None
    cielab: CielabColor | None


def calibrate_finger(
    db: Session,
    storage: Storage,
    user: User,
    image_bgr: np.ndarray,
    finger_code: FingerCode,
    observation: HandObservation,
    *,
    reference_type: ScaleReferenceType,
    reference_width_mm: float | None,
    settings: Settings,
) -> StoredCalibration:
    pose = observation.pose
    segment_pose = pose if pose is not None else _frame_axis_pose(image_bgr)
    mask = segment_nail(image_bgr, segment_pose)
    quality = evaluate_quality(image_bgr, mask, pose, settings)
    long_edge_mm = ID1_CARD_WIDTH_MM if reference_type is ScaleReferenceType.ID1_CARD else reference_width_mm
    if long_edge_mm is None or long_edge_mm <= 0:
        raise PipelineRejection(
            _failure(quality.metrics, ["RETAKE_NO_CARD"], ["A manual reference needs its long-edge width in millimetres."]),
        )
    card = None
    if reference_type is ScaleReferenceType.ID1_CARD:
        card = detect_aruco_card(image_bgr)
    if card is None:
        card = detect_card(
            image_bgr,
            reference_type=reference_type.value,
            long_edge_mm=float(long_edge_mm),
        )
    extra: list[str] = []
    if card is None:
        extra.append("RETAKE_NO_CARD")
    reasons = list(quality.metrics.reasons) + extra
    if blocks_save(reasons, force_save=False) or extra:
        metrics = quality.metrics.model_copy(update={"passed": False, "reasons": reasons})
        raise PipelineRejection(_failure(metrics, reasons))
    assert card is not None and pose is not None
    try:
        width_px, left, right, anchors = _anchors_from_mask(mask)
    except MeasurementError as exc:
        metrics = quality.metrics.model_copy(update={"passed": False, "reasons": [*reasons, "RETAKE_NO_NAIL"]})
        raise PipelineRejection(_failure(metrics, list(metrics.reasons))) from exc
    calibrated_width_mm = float(width_px * card.mm_per_px)
    if calibrated_width_mm <= 0:
        raise PipelineRejection(_failure(quality.metrics, ["RETAKE_NO_NAIL"]))
    reference = card.reference_white
    baseline = db.scalar(
        select(FingerBaseline).where(
            FingerBaseline.user_id == user.id,
            FingerBaseline.finger_code == finger_code,
        )
    )
    if baseline is None:
        baseline = FingerBaseline(
            user_id=user.id,
            finger_code=finger_code,
            calibrated_width_mm=calibrated_width_mm,
        )
        db.add(baseline)
        db.flush()
    image_key = blob_key_for_user(user.id, "baselines", finger_code.value, "day0.jpg")
    mask_key = blob_key_for_user(user.id, "baselines", finger_code.value, "day0_mask.png")
    storage.put_bytes(image_key, encode_jpeg(image_bgr), "image/jpeg")
    storage.put_bytes(mask_key, encode_mask_png(mask), "image/png")
    baseline.calibrated_width_mm = calibrated_width_mm
    baseline.scale_reference_type = reference_type
    baseline.scale_reference_width_mm = float(long_edge_mm)
    baseline.baseline_image_key = image_key
    baseline.baseline_mask_key = mask_key
    baseline.anchor_keypoints_json = anchors.model_dump(mode="json")
    baseline.reference_white_json = reference.model_dump(mode="json")
    baseline.updated_at = utcnow()
    db.commit()
    db.refresh(baseline)
    return StoredCalibration(
        baseline=baseline,
        mm_per_px=card.mm_per_px,
        reference_white=reference,
        anchors=anchors,
        quality=quality.metrics,
    )


def analyze_capture(
    db: Session,
    storage: Storage,
    user: User,
    image_bgr: np.ndarray,
    finger_code: FingerCode,
    observation: HandObservation,
    *,
    force_save: bool,
    settings: Settings,
) -> StoredCapture:
    baseline = db.scalar(
        select(FingerBaseline).where(
            FingerBaseline.user_id == user.id,
            FingerBaseline.finger_code == finger_code,
        )
    )
    if baseline is None or not baseline.baseline_image_key or not baseline.reference_white_json:
        raise PipelineRejection(
            {"detail": "Calibrate this finger before analyzing a capture."},
            status_code=409,
        )
    pose = observation.pose
    # A nail close-up often has no detectable palm. Segment from a vertical
    # axis through the frame so force_save can still measure the plate.
    segment_pose = pose if pose is not None else _frame_axis_pose(image_bgr)
    mask = segment_nail(image_bgr, segment_pose)
    quality = evaluate_quality(image_bgr, mask, pose, settings)
    if blocks_save(list(quality.metrics.reasons), force_save=force_save):
        raise PipelineRejection(_failure(quality.metrics, list(quality.metrics.reasons)))
    baseline_bgr, baseline_mask = _load_baseline_rasters(storage, baseline, settings)
    try:
        anchors = AnchorKeypoints.model_validate(baseline.anchor_keypoints_json)
        reference = ReferenceWhite.model_validate(baseline.reference_white_json)
    except ValueError as exc:
        raise PipelineRejection(
            {"detail": "The stored calibration is incomplete. Calibrate this finger again."},
            status_code=409,
        ) from exc
    try:
        alignment, aligned_bgr, aligned_mask = _align_and_warp(
            baseline_bgr, baseline_mask, image_bgr, mask, anchors, pose
        )
        mm_per_px = _mm_per_px(baseline.calibrated_width_mm, anchors)
        measurement = measure_nail(
            aligned_bgr,
            aligned_mask,
            mm_per_px=mm_per_px,
            von_kries_gains=reference.von_kries_gains,
            gabor_energy_floor=settings.gabor_energy_floor,
        )
    except (AlignmentError, MeasurementError) as exc:
        metrics = quality.metrics.model_copy(update={"passed": False})
        raise PipelineRejection(_failure(metrics, ["ALIGNMENT_FAILED"])) from exc
    capture = NailCapture(
        user_id=user.id,
        baseline_id=baseline.id,
        finger_code=finger_code,
        captured_at=utcnow(),
    )
    db.add(capture)
    db.flush()
    raw_key = blob_key_for_user(user.id, "captures", str(capture.id), "raw.jpg")
    aligned_key = blob_key_for_user(user.id, "captures", str(capture.id), "aligned.jpg")
    mask_key = blob_key_for_user(user.id, "captures", str(capture.id), "mask.png")
    storage.put_bytes(raw_key, encode_jpeg(image_bgr), "image/jpeg")
    storage.put_bytes(aligned_key, encode_jpeg(aligned_bgr), "image/jpeg")
    storage.put_bytes(mask_key, encode_mask_png(aligned_mask), "image/png")
    homography = HomographyRecord(
        matrix=alignment.matrix.tolist(),
        ransac_inlier_rms=alignment.ransac_inlier_rms,
        method=alignment.method,  # type: ignore[arg-type]
        alignment_quality=alignment.alignment_quality,
        inlier_ratio=alignment.inlier_ratio,
        ecc_correlation=alignment.ecc_correlation,
    )
    cielab = CielabColor(L=measurement.cielab[0], a=measurement.cielab[1], b=measurement.cielab[2])
    capture.raw_image_key = raw_key
    capture.aligned_image_key = aligned_key
    capture.segmentation_mask_key = mask_key
    capture.mask_source = MaskSource.AUTO_CLASSICAL
    capture.mask_updated_at = utcnow()
    capture.homography_matrix_json = homography.model_dump(mode="json")
    capture.quality_metrics_json = quality.metrics.model_dump(mode="json")
    capture.nail_bed_length_mm = measurement.nail_bed_length_mm
    capture.free_margin_length_mm = measurement.free_margin_length_mm
    capture.total_length_mm = measurement.total_length_mm
    capture.lunula_ratio = measurement.lunula_ratio
    capture.ridge_gabor_score = measurement.ridge_gabor_score
    capture.transverse_gabor_score = measurement.transverse_gabor_score
    capture.cielab_color_json = cielab.model_dump(mode="json")
    capture.tracked_features_json = detect_tracked_features(aligned_bgr, aligned_mask, mm_per_px)
    _update_velocity_from_previous(db, baseline, capture, measurement.total_length_mm)
    write_forecast_cache(db, user, settings, commit=False)
    db.commit()
    db.refresh(capture)
    return StoredCapture(capture=capture, quality=quality.metrics, homography=homography, cielab=cielab)


def apply_corrected_mask(
    db: Session,
    storage: Storage,
    user: User,
    capture: NailCapture,
    mask_png: bytes,
    settings: Settings,
) -> StoredCapture:
    baseline = db.get(FingerBaseline, capture.baseline_id)
    if baseline is None or baseline.user_id != user.id or not baseline.reference_white_json:
        raise PipelineRejection({"detail": "Capture not found"}, status_code=404)
    try:
        corrected = decode_mask_png(mask_png)
    except ValueError as exc:
        raise PipelineRejection({"detail": str(exc)}) from exc
    reference_key = capture.aligned_image_key or capture.raw_image_key
    if reference_key is None:
        raise PipelineRejection({"detail": "Capture has no image to correct"})
    reference_image = _read_bgr(storage, reference_key)
    if corrected.shape[:2] != reference_image.shape[:2]:
        raise PipelineRejection(
            {
                "detail": (
                    "Corrected mask must match the aligned image size "
                    f"{reference_image.shape[1]}x{reference_image.shape[0]}."
                )
            }
        )
    values = set(np.unique(corrected).tolist())
    if not values or not values <= MASK_VALUES:
        raise PipelineRejection({"detail": "Mask values must be 0 background, 1 lunula, 2 nail bed, 3 free margin."})
    if int(np.isin(corrected, (1, 2, 3)).sum()) < 20:
        raise PipelineRejection({"detail": "Corrected mask does not contain a nail plate."})
    anchors = AnchorKeypoints.model_validate(baseline.anchor_keypoints_json)
    reference_white = ReferenceWhite.model_validate(baseline.reference_white_json)
    try:
        measurement = measure_nail(
            reference_image,
            corrected,
            mm_per_px=_mm_per_px(baseline.calibrated_width_mm, anchors),
            von_kries_gains=reference_white.von_kries_gains,
            gabor_energy_floor=settings.gabor_energy_floor,
        )
    except MeasurementError as exc:
        raise PipelineRejection({"detail": "Corrected mask could not be measured."}) from exc
    mask_key = blob_key_for_user(user.id, "captures", str(capture.id), "mask_corrected.png")
    storage.put_bytes(mask_key, encode_mask_png(corrected), "image/png")
    capture.corrected_mask_key = mask_key
    capture.mask_source = MaskSource.USER_CORRECTED
    capture.mask_updated_at = utcnow()
    capture.nail_bed_length_mm = measurement.nail_bed_length_mm
    capture.free_margin_length_mm = measurement.free_margin_length_mm
    capture.total_length_mm = measurement.total_length_mm
    capture.lunula_ratio = measurement.lunula_ratio
    capture.ridge_gabor_score = measurement.ridge_gabor_score
    capture.transverse_gabor_score = measurement.transverse_gabor_score
    cielab = CielabColor(L=measurement.cielab[0], a=measurement.cielab[1], b=measurement.cielab[2])
    capture.cielab_color_json = cielab.model_dump(mode="json")
    capture.tracked_features_json = detect_tracked_features(
        reference_image,
        corrected,
        _mm_per_px(baseline.calibrated_width_mm, anchors),
    )
    write_forecast_cache(db, user, settings, commit=False)
    db.commit()
    db.refresh(capture)
    quality = QualityMetrics.model_validate(capture.quality_metrics_json) if capture.quality_metrics_json else QualityMetrics(
        laplacian_var=0.0, glare_ratio=0.0, passed=True, reasons=[]
    )
    homography = (
        HomographyRecord.model_validate(capture.homography_matrix_json)
        if capture.homography_matrix_json
        else None
    )
    return StoredCapture(capture=capture, quality=quality, homography=homography, cielab=cielab)


def _update_velocity_from_previous(db: Session, baseline: FingerBaseline, capture: NailCapture, new_total_mm: float) -> None:
    previous = db.scalar(
        select(NailCapture)
        .where(
            NailCapture.baseline_id == baseline.id,
            NailCapture.id != capture.id,
            NailCapture.total_length_mm.is_not(None),
        )
        .order_by(NailCapture.captured_at.desc())
    )
    if previous is None or previous.captured_at is None:
        return
    days = (capture.captured_at - previous.captured_at).total_seconds() / 86400.0
    updated = update_observed_velocity(
        previous.total_length_mm,
        new_total_mm,
        days,
        float(baseline.observed_velocity_mm_day),
    )
    if updated is not None:
        baseline.observed_velocity_mm_day = updated


def _frame_axis_pose(image_bgr: np.ndarray) -> FingerPose:
    """A stand-in axis when MediaPipe cannot see a palm in a close-up."""
    height, width = image_bgr.shape[:2]
    x = width / 2.0
    return FingerPose(
        dip_xy=(x, height * 0.28),
        tip_xy=(x, height * 0.82),
        dip_xyz=(0.0, 0.0, 0.0),
        tip_xyz=(0.0, 1.0, 0.0),
    )


def _anchors_from_mask(mask: np.ndarray) -> tuple[float, tuple[float, float], tuple[float, float], AnchorKeypoints]:
    from app.services.geometry import axis_from_mask

    centroid, direction = axis_from_mask(mask)
    width_px, left, right = width_and_folds(mask, centroid, direction)
    crease = dip_crease_points(centroid, direction, left, right, mask)
    anchors = AnchorKeypoints(left_fold=left, right_fold=right, dip_crease=crease)
    return width_px, left, right, anchors


def _align_and_warp(baseline_bgr, baseline_mask, capture_bgr, capture_mask, anchors: AnchorKeypoints, pose: FingerPose | None):
    baseline_pose = _pose_from_anchors(anchors)
    baseline_invariant = build_invariant_mask(baseline_mask, baseline_pose)
    capture_invariant = build_invariant_mask(capture_mask, pose)
    capture_folds = _folds_from_mask(capture_mask)
    alignment = align_to_baseline(
        baseline_bgr,
        capture_bgr,
        baseline_invariant=baseline_invariant,
        capture_invariant=capture_invariant,
        baseline_folds=(anchors.left_fold, anchors.right_fold),
        capture_folds=capture_folds,
    )
    size = (baseline_bgr.shape[1], baseline_bgr.shape[0])
    aligned = warp_bgr(capture_bgr, alignment.matrix, size)
    aligned_mask = warp_mask(capture_mask, alignment.matrix, size)
    return alignment, aligned, aligned_mask


def _folds_from_mask(mask: np.ndarray) -> tuple[tuple[float, float], tuple[float, float]] | None:
    try:
        _width, left, right, _anchors = _anchors_from_mask(mask)
    except MeasurementError:
        return None
    return left, right


def _pose_from_anchors(anchors: AnchorKeypoints) -> FingerPose:
    crease = np.array(anchors.dip_crease, dtype=np.float64).mean(axis=0)
    fold_mid = (np.array(anchors.left_fold) + np.array(anchors.right_fold)) / 2.0
    direction = fold_mid - crease
    norm = float(np.linalg.norm(direction))
    if norm < 1e-3:
        direction = np.array([0.0, 1.0])
        norm = 1.0
    tip = fold_mid + direction / norm * max(norm, 40.0)
    return FingerPose(dip_xy=(float(crease[0]), float(crease[1])), tip_xy=(float(tip[0]), float(tip[1])))


def _mm_per_px(calibrated_width_mm: float, anchors: AnchorKeypoints) -> float:
    width_px = float(np.linalg.norm(np.array(anchors.left_fold) - np.array(anchors.right_fold)))
    if width_px < 1e-3:
        raise MeasurementError("baseline fold width is zero")
    return float(calibrated_width_mm) / width_px


def _load_baseline_rasters(storage: Storage, baseline: FingerBaseline, settings: Settings):
    from app.services.imaging import decode_bgr
    from app.storage import StorageNotFound

    del settings
    try:
        payload = storage.get_bytes(baseline.baseline_image_key or "")
    except StorageNotFound as exc:
        raise PipelineRejection(
            {"detail": "The Day-0 image is missing. Calibrate this finger again."},
            status_code=409,
        ) from exc
    # Stored rasters are already sized. Do not rescale them or the anchors drift.
    image = decode_bgr(payload, max_edge=10_000)
    if baseline.baseline_mask_key:
        mask = decode_mask_png(storage.get_bytes(baseline.baseline_mask_key))
        if mask.shape[:2] != image.shape[:2]:
            mask = segment_nail(image, _pose_from_anchors(AnchorKeypoints.model_validate(baseline.anchor_keypoints_json)))
    else:
        mask = segment_nail(image, _pose_from_anchors(AnchorKeypoints.model_validate(baseline.anchor_keypoints_json)))
    return image, mask


def _read_bgr(storage: Storage, key: str):
    from app.services.imaging import decode_bgr

    return decode_bgr(storage.get_bytes(key), max_edge=10_000)


def _failure(metrics: QualityMetrics, reasons: list[str], messages: list[str] | None = None) -> dict:
    unique: list[str] = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    resolved_messages = messages if messages is not None else [
        QUALITY_REASON_MESSAGES.get(reason, reason) for reason in unique
    ]
    payload = metrics.model_copy(update={"passed": False, "reasons": [reason for reason in unique if reason in QUALITY_REASON_MESSAGES and reason != "ALIGNMENT_FAILED"]})
    # Keep alignment failures in the top-level reasons even though they are not a retake code.
    stored_reasons = [reason for reason in unique if reason != "ALIGNMENT_FAILED"]
    if stored_reasons:
        payload = metrics.model_copy(update={"passed": False, "reasons": stored_reasons})
    return {
        "passed": False,
        "reasons": unique,
        "messages": resolved_messages,
        "metrics": payload.model_dump(mode="json"),
    }


def owned_capture(db: Session, user_id: uuid.UUID, capture_id: uuid.UUID) -> NailCapture:
    row = db.get(NailCapture, capture_id)
    if row is None or row.user_id != user_id:
        raise PipelineRejection({"detail": "Capture not found"}, status_code=404)
    return row
