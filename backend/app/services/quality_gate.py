"""Blur, glare, and tilt checks on a submitted photo.

Blur and glare run on the nail plate. Tilt uses the 3D DIP and tip from
MediaPipe. A missing hand is reported, not treated as a flat pose. The client
may store that photo with force_save. A missing nail is still a hard failure.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.config import Settings
from app.constants import (
    GLARE_SATURATION_MAX,
    GLARE_VALUE_MIN,
    QUALITY_REASON_MESSAGES,
    QUALITY_REASONS_FORCE_SAVE_CAN_OVERRIDE,
)
from app.schemas import QualityMetrics
from app.services.geometry import NAIL_CLASSES, FingerPose, pitch_degrees


@dataclass
class QualityReport:
    metrics: QualityMetrics
    messages: list[str]

    @property
    def passed(self) -> bool:
        return self.metrics.passed


def evaluate_quality(
    image_bgr: np.ndarray,
    nail_mask: np.ndarray | None,
    pose: FingerPose | None,
    settings: Settings,
) -> QualityReport:
    reasons: list[str] = []
    tilt: float | None = None
    handedness = None
    hand_detected = pose is not None
    if pose is None or pose.dip_xyz is None or pose.tip_xyz is None:
        reasons.append("RETAKE_NO_HAND")
    else:
        handedness = pose.handedness
        tilt = pitch_degrees(pose.dip_xyz, pose.tip_xyz)
        if tilt > settings.tilt_deg_max:
            reasons.append("RETAKE_TILT")

    plate = _plate_mask(nail_mask, image_bgr.shape[:2])
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    laplacian = _laplacian_variance(gray, plate)
    glare = _glare_ratio(image_bgr, plate)
    if plate is None or int(plate.sum()) < 40:
        reasons.append("RETAKE_NO_NAIL")
    else:
        if laplacian < settings.blur_laplacian_min:
            reasons.append("RETAKE_BLURRY")
        if glare > settings.glare_ratio_max:
            reasons.append("RETAKE_GLARE")

    metrics = QualityMetrics(
        laplacian_var=laplacian,
        glare_ratio=glare,
        tilt_deg=tilt,
        passed=not reasons,
        reasons=reasons,
        hand_detected=hand_detected,
        handedness=handedness,
    )
    return QualityReport(metrics=metrics, messages=[QUALITY_REASON_MESSAGES[code] for code in reasons])


def blocks_save(reasons: list[str], *, force_save: bool) -> bool:
    if not reasons:
        return False
    if any(reason not in QUALITY_REASONS_FORCE_SAVE_CAN_OVERRIDE for reason in reasons):
        return True
    return not force_save


def _plate_mask(nail_mask: np.ndarray | None, shape: tuple[int, int]) -> np.ndarray | None:
    if nail_mask is None or nail_mask.shape[:2] != shape:
        return None
    plate = np.isin(nail_mask, NAIL_CLASSES)
    if int(plate.sum()) < 40:
        return None
    return plate


def _laplacian_variance(gray: np.ndarray, plate: np.ndarray | None) -> float:
    response = cv2.Laplacian(gray, cv2.CV_64F)
    if plate is None:
        return float(response.var())
    return float(response[plate].var())


def _glare_ratio(image_bgr: np.ndarray, plate: np.ndarray | None) -> float:
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    glare = (hsv[:, :, 2] > GLARE_VALUE_MIN) & (hsv[:, :, 1] < GLARE_SATURATION_MAX)
    if plate is None:
        return float(glare.mean())
    return float(glare[plate].mean())
