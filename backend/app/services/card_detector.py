"""Find a flat rectangular calibration card and sample its white area.

ID-1 cards are 85.60 mm by 53.98 mm. The long edge sets millimetres per pixel.
The white reference is the low-saturation, high-value region of the card, not
the skin around the nail.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.constants import ID1_CARD_HEIGHT_MM, ID1_CARD_WIDTH_MM
from app.schemas import ReferenceWhite, reference_white_from_patch
from app.services.colorimetry import bgr_mean_to_linear_rgb

ID1_ASPECT = ID1_CARD_WIDTH_MM / ID1_CARD_HEIGHT_MM


@dataclass
class DetectedCard:
    corners: np.ndarray
    long_edge_px: float
    mm_per_px: float
    reference_white: ReferenceWhite


def detect_card(
    image_bgr: np.ndarray,
    *,
    reference_type: str,
    long_edge_mm: float,
) -> DetectedCard | None:
    aspect = ID1_ASPECT if reference_type == "ID1_CARD" else None
    quad = _best_quad(image_bgr, aspect)
    if quad is None:
        return None
    long_edge_px = _mean_long_edge(quad)
    if long_edge_px < 30:
        return None
    warped = _warp_card(image_bgr, quad)
    white_pixels = _white_pixels(warped)
    if white_pixels.shape[0] < 20:
        return None
    try:
        mean_rgb = bgr_mean_to_linear_rgb(white_pixels)
        source = "id1_card_white" if reference_type == "ID1_CARD" else "manual_white"
        reference = reference_white_from_patch(mean_rgb=mean_rgb, source=source)
    except ValueError:
        return None
    return DetectedCard(
        corners=quad,
        long_edge_px=long_edge_px,
        mm_per_px=float(long_edge_mm) / long_edge_px,
        reference_white=reference,
    )


def _best_quad(image_bgr: np.ndarray, aspect: float | None) -> np.ndarray | None:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    candidates: list[np.ndarray] = []
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 140)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    candidates.extend(_quads_from_mask(edges))
    _threshold, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    candidates.extend(_quads_from_mask(bright))
    best: np.ndarray | None = None
    best_area = 0.0
    for quad in candidates:
        long_edge, short_edge = _edges(quad)
        if short_edge < 20:
            continue
        ratio = long_edge / short_edge
        if aspect is not None and abs(ratio - aspect) / aspect > 0.12:
            continue
        if aspect is None and not 1.15 <= ratio <= 3.2:
            continue
        area = long_edge * short_edge
        if area > best_area:
            best_area = area
            best = quad
    return best


def _quads_from_mask(mask: np.ndarray) -> list[np.ndarray]:
    contours, _hierarchy = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    quads: list[np.ndarray] = []
    for contour in contours:
        if cv2.contourArea(contour) < 400:
            continue
        rectangle = cv2.minAreaRect(contour)
        _center, size, _angle = rectangle
        if min(size) < 20:
            continue
        quads.append(_order_corners(cv2.boxPoints(rectangle)))
    return quads


def _order_corners(points: np.ndarray) -> np.ndarray:
    pts = points.reshape(4, 2).astype(np.float64)
    total = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)
    ordered = np.array(
        [
            pts[np.argmin(total)],
            pts[np.argmin(diff)],
            pts[np.argmax(total)],
            pts[np.argmax(diff)],
        ],
        dtype=np.float64,
    )
    return ordered


def _edges(quad: np.ndarray) -> tuple[float, float]:
    top = float(np.linalg.norm(quad[1] - quad[0]))
    right = float(np.linalg.norm(quad[2] - quad[1]))
    return max(top, right), min(top, right)


def _mean_long_edge(quad: np.ndarray) -> float:
    lengths = [
        float(np.linalg.norm(quad[(index + 1) % 4] - quad[index]))
        for index in range(4)
    ]
    lengths.sort()
    return float(sum(lengths[2:]) / 2.0)


def _warp_card(image_bgr: np.ndarray, quad: np.ndarray) -> np.ndarray:
    top = float(np.linalg.norm(quad[1] - quad[0]))
    side = float(np.linalg.norm(quad[3] - quad[0]))
    if top >= side:
        source = quad
        out_w = max(int(round(top)), 40)
        out_h = max(int(round(side)), 20)
    else:
        source = np.array([quad[3], quad[0], quad[1], quad[2]], dtype=np.float64)
        out_w = max(int(round(side)), 40)
        out_h = max(int(round(top)), 20)
    destination = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float64,
    )
    matrix = cv2.getPerspectiveTransform(source.astype(np.float32), destination.astype(np.float32))
    return cv2.warpPerspective(image_bgr, matrix, (out_w, out_h))


def _white_pixels(card_bgr: np.ndarray) -> np.ndarray:
    height, width = card_bgr.shape[:2]
    margin_y = max(int(height * 0.08), 1)
    margin_x = max(int(width * 0.08), 1)
    crop = card_bgr[margin_y : height - margin_y, margin_x : width - margin_x]
    if crop.size == 0:
        crop = card_bgr
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    white = (hsv[:, :, 1] < 40) & (hsv[:, :, 2] > 180)
    if int(white.sum()) < 20:
        white = hsv[:, :, 2] > 200
    return crop[white]
