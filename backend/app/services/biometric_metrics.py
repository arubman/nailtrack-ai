"""Ray-cast lengths, Gabor scores, and Von Kries CIELAB on an aligned nail mask.

Lengths follow the spec: 11 rays parallel to the nail axis, within ±15% of
the midline. L_bed runs from the cuticle (first lunula or nail-bed pixel) to
the onychocorneal band. L_free runs from that band to the distal tip.

The ridge score is the longitudinal share of L2-normalized Gabor energy, after
the nail is rotated so the finger axis is image +y. theta=0 is longitudinal in
that frame. Below the energy floor both scores are 0.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.constants import (
    GABOR_GAMMA,
    GABOR_KSIZE,
    GABOR_LAMBD,
    GABOR_PSI,
    GABOR_SIGMA,
    GABOR_THETA_LONGITUDINAL,
    GABOR_THETA_TRANSVERSE,
)
from app.services.colorimetry import mean_lab
from app.services.geometry import (
    CLASS_FREE_MARGIN,
    CLASS_LUNULA,
    CLASS_NAIL_BED,
    NAIL_CLASSES,
    MeasurementError,
    axis_from_mask,
)


@dataclass
class NailMeasurement:
    nail_bed_length_mm: float
    free_margin_length_mm: float
    total_length_mm: float
    lunula_ratio: float
    ridge_gabor_score: float
    transverse_gabor_score: float
    cielab: tuple[float, float, float]
    centroid_xy: tuple[float, float]
    axis_xy: tuple[float, float]


def measure_nail(
    image_bgr: np.ndarray,
    mask: np.ndarray,
    *,
    mm_per_px: float,
    von_kries_gains: tuple[float, float, float],
    gabor_energy_floor: float,
) -> NailMeasurement:
    if mm_per_px <= 0:
        raise MeasurementError("millimetres per pixel must be positive")
    centroid, direction = axis_from_mask(mask)
    bed_mm, free_mm = _ray_lengths_mm(mask, centroid, direction, mm_per_px)
    ridge, transverse = gabor_scores(
        image_bgr, mask, centroid, direction, energy_floor=gabor_energy_floor
    )
    bed_pixels = mask == CLASS_NAIL_BED
    if int(bed_pixels.sum()) < 10:
        bed_pixels = np.isin(mask, (CLASS_LUNULA, CLASS_NAIL_BED))
    lightness, a_star, b_star = mean_lab(image_bgr, bed_pixels, von_kries_gains)
    nail_area = int(np.isin(mask, NAIL_CLASSES).sum())
    lunula_ratio = float((mask == CLASS_LUNULA).sum() / nail_area) if nail_area else 0.0
    return NailMeasurement(
        nail_bed_length_mm=bed_mm,
        free_margin_length_mm=free_mm,
        total_length_mm=bed_mm + free_mm,
        lunula_ratio=lunula_ratio,
        ridge_gabor_score=ridge,
        transverse_gabor_score=transverse,
        cielab=(lightness, a_star, b_star),
        centroid_xy=(float(centroid[0]), float(centroid[1])),
        axis_xy=(float(direction[0]), float(direction[1])),
    )


def gabor_scores(
    image_bgr: np.ndarray,
    mask: np.ndarray,
    centroid: np.ndarray,
    direction: np.ndarray,
    *,
    energy_floor: float,
) -> tuple[float, float]:
    angle = float(np.degrees(np.arctan2(float(direction[0]), float(direction[1]))))
    rotation = cv2.getRotationMatrix2D((float(centroid[0]), float(centroid[1])), angle, 1.0)
    height, width = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    rotated_gray = cv2.warpAffine(gray, rotation, (width, height), flags=cv2.INTER_LINEAR)
    rotated_mask = cv2.warpAffine(mask, rotation, (width, height), flags=cv2.INTER_NEAREST)
    plate = np.isin(rotated_mask, NAIL_CLASSES)
    if int(plate.sum()) < 20:
        return 0.0, 0.0
    roi = rotated_gray.astype(np.float32) / 255.0
    longitudinal = cv2.filter2D(roi, cv2.CV_32F, _unit_gabor(GABOR_THETA_LONGITUDINAL))
    transverse = cv2.filter2D(roi, cv2.CV_32F, _unit_gabor(GABOR_THETA_TRANSVERSE))
    energy_long = float(np.mean(longitudinal[plate] ** 2))
    energy_trans = float(np.mean(transverse[plate] ** 2))
    if energy_long + energy_trans < energy_floor:
        return 0.0, 0.0
    total = energy_long + energy_trans
    return (
        float(np.clip(energy_long / total, 0.0, 1.0)),
        float(np.clip(energy_trans / total, 0.0, 1.0)),
    )


def _unit_gabor(theta: float) -> np.ndarray:
    kernel = cv2.getGaborKernel(
        (GABOR_KSIZE, GABOR_KSIZE),
        GABOR_SIGMA,
        theta,
        GABOR_LAMBD,
        GABOR_GAMMA,
        GABOR_PSI,
        ktype=cv2.CV_32F,
    )
    norm = float(np.linalg.norm(kernel))
    if norm < 1e-8:
        return kernel
    return kernel / norm


def _ray_lengths_mm(
    mask: np.ndarray, centroid: np.ndarray, direction: np.ndarray, mm_per_px: float
) -> tuple[float, float]:
    ys, xs = np.where(np.isin(mask, NAIL_CLASSES))
    points = np.stack([xs, ys], axis=1).astype(np.float64)
    relative = points - centroid
    projection = relative @ direction
    normal = np.array([-direction[1], direction[0]], dtype=np.float64)
    lateral = relative @ normal
    full_width = float(np.percentile(lateral, 98) - np.percentile(lateral, 2))
    offsets = np.linspace(-0.15 * full_width, 0.15 * full_width, 11)
    t_start = float(projection.min()) - 3.0
    t_stop = float(projection.max()) + 3.0
    samples = np.arange(t_start, t_stop, 1.0)
    bed_px: list[float] = []
    free_px: list[float] = []
    height, width = mask.shape[:2]
    for offset in offsets:
        classes = np.empty(samples.shape[0], np.uint8)
        for index, distance in enumerate(samples):
            point = centroid + direction * distance + normal * offset
            x_coord = int(round(point[0]))
            y_coord = int(round(point[1]))
            if x_coord < 0 or y_coord < 0 or x_coord >= width or y_coord >= height:
                classes[index] = 0
            else:
                classes[index] = mask[y_coord, x_coord]
        cuticle = _boundary(samples, classes, lambda value: value in (CLASS_LUNULA, CLASS_NAIL_BED))
        if cuticle is None:
            continue
        tip = _last_boundary(samples, classes, lambda value: value in NAIL_CLASSES)
        if tip is None or tip <= cuticle:
            continue
        onychocorneal = _boundary(
            samples,
            classes,
            lambda value: value == CLASS_FREE_MARGIN,
            start_at=cuticle,
        )
        if onychocorneal is None or onychocorneal > tip:
            onychocorneal = tip
        bed_px.append(onychocorneal - cuticle)
        free_px.append(max(0.0, tip - onychocorneal))
    if not bed_px:
        raise MeasurementError("rays did not cross the nail plate")
    return float(np.median(bed_px)) * mm_per_px, float(np.median(free_px)) * mm_per_px


def _boundary(samples: np.ndarray, classes: np.ndarray, predicate, start_at: float | None = None) -> float | None:
    begin = 0
    if start_at is not None:
        begin = int(np.searchsorted(samples, start_at))
    for index in range(begin, len(samples)):
        if predicate(int(classes[index])):
            if index == 0:
                return float(samples[index])
            return float((samples[index - 1] + samples[index]) * 0.5)
    return None


def _last_boundary(samples: np.ndarray, classes: np.ndarray, predicate) -> float | None:
    last = None
    for index, distance in enumerate(samples):
        if predicate(int(classes[index])):
            last = index
    if last is None:
        return None
    if last + 1 < len(samples):
        return float((samples[last] + samples[last + 1]) * 0.5)
    return float(samples[last])
