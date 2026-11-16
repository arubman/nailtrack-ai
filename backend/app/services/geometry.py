"""Shared finger geometry. Pixel coordinates are (x, y) with y downward."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

CLASS_BACKGROUND = 0
CLASS_LUNULA = 1
CLASS_NAIL_BED = 2
CLASS_FREE_MARGIN = 3
NAIL_CLASSES = (CLASS_LUNULA, CLASS_NAIL_BED, CLASS_FREE_MARGIN)

# MediaPipe hand topology. The thumb has no DIP; the IP joint is the stand-in.
FINGER_TIP_AND_DIP: dict[str, tuple[int, int]] = {
    "THUMB": (4, 3),
    "INDEX": (8, 7),
    "MIDDLE": (12, 11),
    "RING": (16, 15),
    "PINKY": (20, 19),
}


class MeasurementError(Exception):
    """The mask does not contain a nail the ray caster can measure."""


@dataclass
class FingerPose:
    """One finger, in pixels, plus optional 3D joints for the tilt check.

    `dip_xyz` / `tip_xyz` use MediaPipe's coordinates: x, y in the image plane
    and z toward the camera. They are not pixels.
    """

    dip_xy: tuple[float, float]
    tip_xy: tuple[float, float]
    dip_xyz: tuple[float, float, float] | None = None
    tip_xyz: tuple[float, float, float] | None = None
    handedness: str | None = None
    handedness_score: float | None = None

    def axis(self) -> tuple[np.ndarray, np.ndarray, float]:
        dip = np.array(self.dip_xy, dtype=np.float64)
        tip = np.array(self.tip_xy, dtype=np.float64)
        vector = tip - dip
        length = float(np.linalg.norm(vector))
        if length < 1e-3:
            raise MeasurementError("finger axis is degenerate")
        return dip, vector / length, length


def finger_name(finger_code: str) -> str:
    return finger_code.split("_", 1)[1]


def pitch_degrees(dip_xyz: tuple[float, float, float], tip_xyz: tuple[float, float, float]) -> float:
    """Spec tilt: pitch = asin(|dz| / |tip - dip|) in degrees."""
    vector = np.array(tip_xyz, dtype=np.float64) - np.array(dip_xyz, dtype=np.float64)
    norm = float(np.linalg.norm(vector))
    if norm < 1e-8:
        return 90.0
    return float(np.degrees(np.arcsin(float(np.clip(abs(vector[2]) / norm, 0.0, 1.0)))))


def nail_pixels(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.where(np.isin(mask, NAIL_CLASSES))
    return ys, xs


def axis_from_mask(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Centroid and a unit axis pointing from the cuticle toward the free edge.

    The direction is the nail's principal axis. Lunula (or nail bed) is treated
    as proximal and the free margin as distal so the sign is anatomical rather
    than an arbitrary SVD sign.
    """
    ys, xs = nail_pixels(mask)
    if xs.size < 10:
        raise MeasurementError("nail mask is empty")
    points = np.stack([xs, ys], axis=1).astype(np.float64)
    centroid = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - centroid, full_matrices=False)
    direction = vt[0].astype(np.float64)
    projection = (points - centroid) @ direction
    labels = mask[ys, xs]

    def mean_of(class_id: int) -> float | None:
        selected = labels == class_id
        if not np.any(selected):
            return None
        return float(projection[selected].mean())

    proximal = mean_of(CLASS_LUNULA)
    if proximal is None:
        proximal = mean_of(CLASS_NAIL_BED)
    distal = mean_of(CLASS_FREE_MARGIN)
    if proximal is not None and distal is not None and proximal > distal:
        direction = -direction
    return centroid, direction


def finger_band(pose: FingerPose, height: int, width: int) -> np.ndarray:
    """Polygon covering the fingertip, from just proximal of the DIP to past the tip."""
    dip, axis, length = pose.axis()
    normal = np.array([-axis[1], axis[0]], dtype=np.float64)
    # The plate sits distal to the DIP. Keep a little skin proximal of the DIP
    # for the cuticle, but not the whole finger, or proximal skin becomes lunula.
    half = 0.22 * length
    proximal = dip - axis * (0.08 * length)
    distal = dip + axis * (length * 1.12)
    polygon = np.array(
        [
            proximal + normal * half,
            proximal - normal * half,
            distal - normal * half,
            distal + normal * half,
        ],
        dtype=np.float32,
    )
    band = np.zeros((height, width), np.uint8)
    cv2.fillConvexPoly(band, np.round(polygon).astype(np.int32), 255)
    return band


def width_and_folds(
    mask: np.ndarray, centroid: np.ndarray, direction: np.ndarray
) -> tuple[float, tuple[float, float], tuple[float, float]]:
    """Widest nail cross-section perpendicular to the axis, ignoring the tapered tip.

    Returns width in pixels and the left/right boundary points at that station.
    """
    ys, xs = nail_pixels(mask)
    points = np.stack([xs, ys], axis=1).astype(np.float64)
    relative = points - centroid
    projection = relative @ direction
    normal = np.array([-direction[1], direction[0]], dtype=np.float64)
    lateral = relative @ normal
    # Skip the proximal fringe (a pale lunula cluster can pick up adjacent skin)
    # and the distal 15%, where the free margin tapers.
    proximal = float(np.percentile(projection, 25))
    distal_limit = float(np.percentile(projection, 85))
    keep = (projection >= proximal) & (projection <= distal_limit)
    if int(keep.sum()) < 8:
        keep = np.ones(projection.shape[0], dtype=bool)
    bins = np.linspace(float(projection[keep].min()), float(projection[keep].max()), 9)
    best_width = -1.0
    best_left = points[keep][np.argmin(lateral[keep])]
    best_right = points[keep][np.argmax(lateral[keep])]
    for start, stop in zip(bins[:-1], bins[1:]):
        station = keep & (projection >= start) & (projection <= stop)
        if int(station.sum()) < 4:
            continue
        left_index = np.argmin(lateral[station])
        right_index = np.argmax(lateral[station])
        width = float(lateral[station][right_index] - lateral[station][left_index])
        if width > best_width:
            best_width = width
            best_left = points[station][left_index]
            best_right = points[station][right_index]
    if best_width <= 0:
        raise MeasurementError("nail width is zero")
    return best_width, (float(best_left[0]), float(best_left[1])), (
        float(best_right[0]),
        float(best_right[1]),
    )


def dip_crease_points(
    centroid: np.ndarray,
    direction: np.ndarray,
    left: tuple[float, float],
    right: tuple[float, float],
    mask: np.ndarray,
) -> list[tuple[float, float]]:
    """A short skin line just proximal of the cuticle, spanning the fold width."""
    ys, xs = nail_pixels(mask)
    points = np.stack([xs, ys], axis=1).astype(np.float64)
    projection = (points - centroid) @ direction
    cuticle = centroid + direction * float(projection.min())
    nail_length = float(projection.max() - projection.min())
    station = cuticle - direction * (0.12 * max(nail_length, 1.0))
    left_arr = np.array(left, dtype=np.float64)
    right_arr = np.array(right, dtype=np.float64)
    samples = []
    for weight in np.linspace(0.0, 1.0, 5):
        point = station + (right_arr - left_arr) * (weight - 0.5)
        samples.append((float(point[0]), float(point[1])))
    return samples
