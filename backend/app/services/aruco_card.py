"""Printed NailTrack calibration card: ArUco DICT_4X4_50, ids 0–3.

Geometry matches the owner's card. Origin is the bottom-left of the outer
edge, y up, millimetres. Marker ids run clockwise from the top-left.
detectMarkers returns each marker's corners as top-left, top-right,
bottom-right, bottom-left in the marker's own orientation, which is how
they are printed.

White and 18% gray patches are sampled from their designed rectangles.
If the four markers are not all found, the caller falls back to the
generic quadrilateral detector.
"""

from __future__ import annotations

import cv2
import numpy as np

from app.constants import ID1_CARD_HEIGHT_MM, ID1_CARD_WIDTH_MM
from app.reference_white import von_kries_gains_neutral_gray
from app.schemas import reference_white_from_patch
from app.services.card_detector import DetectedCard
from app.services.colorimetry import bgr_mean_to_linear_rgb

MARKER_SIZE_MM = 8.0
# Bottom-left corner of each marker in the card's y-up frame.
_MARKER_ORIGIN_MM: dict[int, tuple[float, float]] = {
    0: (4.5, 41.48),
    1: (73.1, 41.48),
    2: (73.1, 4.5),
    3: (4.5, 4.5),
}
_WHITE_PATCH_MM = (16.0, 18.4, 32.0, 16.5)
_GRAY_PATCH_MM = (51.0, 18.4, 16.5, 16.5)
_PATCH_INSET_MM = 0.6


def detect_aruco_card(image_bgr: np.ndarray) -> DetectedCard | None:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
    corners, ids, _rejected = detector.detectMarkers(gray)
    if ids is None or len(ids) < 4:
        return None
    by_id: dict[int, np.ndarray] = {}
    for marker_corners, marker_id in zip(corners, ids.reshape(-1)):
        by_id[int(marker_id)] = marker_corners.reshape(4, 2).astype(np.float64)
    if any(marker_id not in by_id for marker_id in _MARKER_ORIGIN_MM):
        return None
    image_points = []
    card_points = []
    for marker_id, origin in _MARKER_ORIGIN_MM.items():
        image_points.append(by_id[marker_id])
        card_points.append(_marker_corners_mm(*origin))
    image_stack = np.concatenate(image_points, axis=0)
    card_stack = np.concatenate(card_points, axis=0)
    homography, _mask = cv2.findHomography(image_stack, card_stack, 0)
    if homography is None:
        return None
    mm_per_px = _scale_mm_per_px(by_id, homography)
    if mm_per_px is None or mm_per_px <= 0:
        return None
    try:
        white_rgb = _sample_patch(image_bgr, homography, _WHITE_PATCH_MM)
        gray_rgb = _sample_patch(image_bgr, homography, _GRAY_PATCH_MM)
        reference = reference_white_from_patch(mean_rgb=white_rgb, source="id1_card_white")
        reference = reference.model_copy(
            update={
                "gray_mean_rgb": gray_rgb,
                "gray_von_kries_gains": von_kries_gains_neutral_gray(gray_rgb),
                "calibration_method": "aruco",
            }
        )
    except ValueError:
        return None
    outer = np.array(
        [[0.0, ID1_CARD_HEIGHT_MM], [ID1_CARD_WIDTH_MM, ID1_CARD_HEIGHT_MM], [ID1_CARD_WIDTH_MM, 0.0], [0.0, 0.0]],
        dtype=np.float64,
    )
    return DetectedCard(
        corners=_mm_to_image(homography, outer),
        long_edge_px=ID1_CARD_WIDTH_MM / mm_per_px,
        mm_per_px=mm_per_px,
        reference_white=reference,
        method="aruco",
    )


def _marker_corners_mm(x_mm: float, y_mm: float) -> np.ndarray:
    """TL, TR, BR, BL in y-up millimetres. y_mm is the marker's bottom edge."""
    size = MARKER_SIZE_MM
    return np.array(
        [
            [x_mm, y_mm + size],
            [x_mm + size, y_mm + size],
            [x_mm + size, y_mm],
            [x_mm, y_mm],
        ],
        dtype=np.float64,
    )


def _scale_mm_per_px(by_id: dict[int, np.ndarray], homography: np.ndarray) -> float | None:
    ratios: list[float] = []
    pairs = ((0, 1), (3, 2), (0, 3), (1, 2))
    for left_id, right_id in pairs:
        image_distance = float(np.linalg.norm(by_id[left_id].mean(axis=0) - by_id[right_id].mean(axis=0)))
        if image_distance < 5:
            continue
        card_left = _marker_corners_mm(*_MARKER_ORIGIN_MM[left_id]).mean(axis=0)
        card_right = _marker_corners_mm(*_MARKER_ORIGIN_MM[right_id]).mean(axis=0)
        card_distance = float(np.linalg.norm(card_left - card_right))
        ratios.append(card_distance / image_distance)
    del homography
    if not ratios:
        return None
    return float(np.mean(ratios))


def _sample_patch(
    image_bgr: np.ndarray,
    homography: np.ndarray,
    patch: tuple[float, float, float, float],
) -> tuple[float, float, float]:
    x_mm, y_mm, width_mm, height_mm = patch
    inset = _PATCH_INSET_MM
    corners_mm = np.array(
        [
            [x_mm + inset, y_mm + height_mm - inset],
            [x_mm + width_mm - inset, y_mm + height_mm - inset],
            [x_mm + width_mm - inset, y_mm + inset],
            [x_mm + inset, y_mm + inset],
        ],
        dtype=np.float64,
    )
    pixels = _mm_to_image(homography, corners_mm)
    out_w = max(int(round((width_mm - 2 * inset) / 0.1)), 8)
    out_h = max(int(round((height_mm - 2 * inset) / 0.1)), 8)
    destination = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32,
    )
    warp = cv2.getPerspectiveTransform(pixels.astype(np.float32), destination)
    patch_bgr = cv2.warpPerspective(image_bgr, warp, (out_w, out_h))
    return bgr_mean_to_linear_rgb(patch_bgr.reshape(-1, 3))


def _mm_to_image(homography: np.ndarray, points_mm: np.ndarray) -> np.ndarray:
    inverse = np.linalg.inv(homography)
    shaped = points_mm.reshape(-1, 1, 2).astype(np.float64)
    return cv2.perspectiveTransform(shaped, inverse).reshape(-1, 2)
