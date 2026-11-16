"""Conservative classical detection of leukonychia spots and cracks.

A mark is kept only when it is small, inside the nail plate, and clearly
lighter (spot) or darker and elongated (crack) than the plate around it.
Glare-sized white regions and plate-wide shading are ignored.
"""

from __future__ import annotations

import cv2
import numpy as np

from app.services.geometry import NAIL_CLASSES, axis_from_mask


def detect_tracked_features(image_bgr: np.ndarray, mask: np.ndarray, mm_per_px: float) -> list[dict]:
    plate = np.isin(mask, NAIL_CLASSES).astype(np.uint8)
    if int(plate.sum()) < 80 or mm_per_px <= 0:
        return []
    try:
        centroid, direction = axis_from_mask(mask)
    except Exception:
        return []
    normal = np.array([-direction[1], direction[0]], dtype=np.float64)
    ys, xs = np.where(plate > 0)
    points = np.stack([xs, ys], axis=1).astype(np.float64)
    projection = (points - centroid) @ direction
    lateral = (points - centroid) @ normal
    cuticle = float(projection.min())
    lat_min = float(lateral.min())
    lat_span = max(float(lateral.max() - lat_min), 1.0)
    features: list[dict] = []
    features.extend(
        _spots(image_bgr, plate, centroid, direction, normal, cuticle, lat_min, lat_span, mm_per_px)
    )
    features.extend(
        _cracks(image_bgr, plate, centroid, direction, normal, cuticle, lat_min, lat_span, mm_per_px)
    )
    features.sort(key=lambda item: (item["type"], item["dist_from_cuticle_mm"], item["rel_x"]))
    counts = {"leukonychia": 0, "crack": 0}
    for feature in features:
        counts[feature["type"]] += 1
        prefix = "spot" if feature["type"] == "leukonychia" else "crack"
        feature["id"] = f"{prefix}_{counts[feature['type']]}"
    return features


def _spots(image, plate, centroid, direction, normal, cuticle, lat_min, lat_span, mm_per_px) -> list[dict]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2]
    saturation = hsv[:, :, 1]
    local = cv2.GaussianBlur(value, (0, 0), 4)
    bright = ((value.astype(np.int16) - local.astype(np.int16)) > 16) & (saturation < 60) & (plate > 0)
    # Drop anything that touches a large fraction of the plate; that is glare or a pale nail, not a spot.
    bright = bright.astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(bright, connectivity=8)
    found = []
    plate_area = int(plate.sum())
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < 12 or area > 450 or area > 0.04 * plate_area:
            continue
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        if min(width, height) < 3:
            continue
        circularity = area / max(width * height, 1)
        if circularity < 0.35:
            continue
        cx, cy = centroids[label]
        found.append(
            _record("leukonychia", cx, cy, centroid, direction, normal, cuticle, lat_min, lat_span, mm_per_px)
        )
    return found


def _cracks(image, plate, centroid, direction, normal, cuticle, lat_min, lat_span, mm_per_px) -> list[dict]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    dark = (blackhat > 18) & (plate > 0)
    dark = dark.astype(np.uint8)
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(dark, connectivity=8)
    found = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        width = int(stats[label, cv2.CC_STAT_WIDTH])
        height = int(stats[label, cv2.CC_STAT_HEIGHT])
        long_side = max(width, height)
        short_side = max(min(width, height), 1)
        if area < 20 or long_side < 14 or long_side / short_side < 3.0:
            continue
        cx, cy = centroids[label]
        found.append(_record("crack", cx, cy, centroid, direction, normal, cuticle, lat_min, lat_span, mm_per_px))
    return found


def _record(kind, cx, cy, centroid, direction, normal, cuticle, lat_min, lat_span, mm_per_px) -> dict:
    point = np.array([cx, cy], dtype=np.float64)
    relative = point - centroid
    projection = float(relative @ direction)
    lateral = float(relative @ normal)
    rel_x = float(np.clip((lateral - lat_min) / lat_span, 0.0, 1.0))
    distance_mm = max(0.0, (projection - cuticle) * mm_per_px)
    return {
        "id": kind,
        "type": kind,
        "rel_x": rel_x,
        "dist_from_cuticle_mm": distance_mm,
    }
