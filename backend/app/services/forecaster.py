"""Growth forecast: blended velocity, a pinned control mesh, and spot migration.

ΔL(t) = (0.7 * v_obs + 0.3 * v_population) * alpha_tx * t

alpha_tx is the product of velocity_modifier on treatments that are active
now and target this finger (or ALL). The modifier is the user's number.
The product is clamped to the same 0.05–5 range as a single modifier.

The mesh pins background, lunula, and the proximal 85% of the nail bed.
Free-margin vertices move along the finger axis by Δy. Their lateral
coordinate moves inward by 0.02 * Δy. Spots move by v_obs * t (the
observed pace) and drop out once that passes the forecast tip.
"""

from __future__ import annotations

from datetime import datetime

import cv2
import numpy as np

from app.config import Settings
from app.constants import (
    ALPHA_TX_MAX,
    ALPHA_TX_MIN,
    FORECAST_HORIZONS_DAYS,
    FORECAST_MESH_COLS,
    FORECAST_MESH_ROWS,
    FREE_MARGIN_SYNTHESIS_BELOW_MM,
    LATERAL_TAPER_PER_DELTA,
    MIN_VELOCITY_INTERVAL_DAYS,
    NAIL_BED_PIN_FRACTION,
    VELOCITY_EMA_NEW_WEIGHT,
    VELOCITY_OBSERVED_WEIGHT,
    VELOCITY_POPULATION_WEIGHT,
)
from app.database import utcnow
from app.models import Treatment
from app.schemas import MeshVertex, TimelineFeature
from app.services.geometry import (
    CLASS_FREE_MARGIN,
    CLASS_LUNULA,
    CLASS_NAIL_BED,
    NAIL_CLASSES,
    MeasurementError,
    axis_from_mask,
)


def blended_velocity(v_observed: float, v_population: float) -> float:
    return VELOCITY_OBSERVED_WEIGHT * v_observed + VELOCITY_POPULATION_WEIGHT * v_population


def effective_velocity(v_observed: float, v_population: float, alpha: float) -> float:
    return blended_velocity(v_observed, v_population) * alpha


def delta_length_mm(v_observed: float, v_population: float, alpha: float, t_days: float) -> float:
    return effective_velocity(v_observed, v_population, alpha) * t_days


def alpha_for_finger(treatments: list[Treatment], finger_code: str, moment: datetime | None = None) -> float:
    moment = moment or utcnow()
    product = 1.0
    matched = False
    for treatment in treatments:
        if not _treatment_active(treatment, moment):
            continue
        targets = list(treatment.target_fingers or [])
        if "ALL" not in targets and finger_code not in targets:
            continue
        product *= float(treatment.velocity_modifier)
        matched = True
    if not matched:
        return 1.0
    return float(np.clip(product, ALPHA_TX_MIN, ALPHA_TX_MAX))


def update_observed_velocity(
    previous_total_mm: float | None,
    new_total_mm: float | None,
    days: float,
    current_velocity: float,
) -> float | None:
    """EMA of growth. A shorter nail is a trim, not negative growth, and is skipped."""
    if previous_total_mm is None or new_total_mm is None:
        return None
    if days < MIN_VELOCITY_INTERVAL_DAYS:
        return None
    delta = new_total_mm - previous_total_mm
    if delta <= 0:
        return None
    rate = delta / days
    return VELOCITY_EMA_NEW_WEIGHT * rate + (1.0 - VELOCITY_EMA_NEW_WEIGHT) * current_velocity


def migrate_feature(
    dist_from_cuticle_mm: float,
    *,
    shift_mm: float,
    tip_mm: float,
) -> tuple[float, bool]:
    """Move a mark distally. Clip once it passes the tip used for this frame."""
    updated = dist_from_cuticle_mm + shift_mm
    return updated, updated > tip_mm + 1e-6


def days_until_clear(dist_from_cuticle_mm: float, total_length_mm: float, velocity_mm_day: float) -> float | None:
    if velocity_mm_day <= 1e-6:
        return None
    remaining = total_length_mm - dist_from_cuticle_mm
    if remaining <= 0:
        return 0.0
    return remaining / velocity_mm_day


def build_control_mesh(
    mask: np.ndarray,
    *,
    delta_px: float,
    cols: int = FORECAST_MESH_COLS,
    rows: int = FORECAST_MESH_ROWS,
) -> list[MeshVertex]:
    height, width = mask.shape[:2]
    try:
        centroid, direction = axis_from_mask(mask)
    except MeasurementError:
        return _flat_mesh(width, height, cols, rows)
    normal = np.array([-direction[1], direction[0]], dtype=np.float64)
    nail = np.isin(mask, NAIL_CLASSES)
    ys, xs = np.where(nail)
    points = np.stack([xs, ys], axis=1).astype(np.float64)
    projection = (points - centroid) @ direction
    labels = mask[ys, xs]
    bed = labels == CLASS_NAIL_BED
    if np.any(bed):
        pin_limit = float(np.quantile(projection[bed], NAIL_BED_PIN_FRACTION))
    else:
        pin_limit = float(np.quantile(projection, NAIL_BED_PIN_FRACTION))
    tip = float(projection.max())
    span = max(tip - pin_limit, 1.0)
    vertices: list[MeshVertex] = []
    for row in range(rows):
        y_coord = (row + 0.5) * height / rows
        for col in range(cols):
            x_coord = (col + 0.5) * width / cols
            point = np.array([x_coord, y_coord], dtype=np.float64)
            sample = mask[min(height - 1, int(y_coord)), min(width - 1, int(x_coord))]
            proj = float((point - centroid) @ direction)
            lateral = float((point - centroid) @ normal)
            pinned = _is_pinned(int(sample), proj, pin_limit)
            if pinned or delta_px == 0:
                vertices.append(MeshVertex(x=x_coord, y=y_coord, dx=0.0, dy=0.0, pinned=True))
                continue
            fraction = float(np.clip((proj - pin_limit) / span, 0.0, 1.0))
            longitudinal = direction * (fraction * delta_px)
            inward = -np.sign(lateral) * normal * (fraction * LATERAL_TAPER_PER_DELTA * abs(delta_px))
            if lateral == 0:
                inward = np.zeros(2)
            shift = longitudinal + inward
            vertices.append(
                MeshVertex(
                    x=x_coord,
                    y=y_coord,
                    dx=float(shift[0]),
                    dy=float(shift[1]),
                    pinned=False,
                )
            )
    return vertices


def warp_forecast(
    image_bgr: np.ndarray,
    mask: np.ndarray,
    mesh: list[MeshVertex],
    *,
    delta_px: float,
    synthesize: bool,
    ridge_score: float,
) -> np.ndarray:
    height, width = image_bgr.shape[:2]
    displacement = _dense_displacement(mesh, width, height)
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    map_x = grid_x - displacement[:, :, 0]
    map_y = grid_y - displacement[:, :, 1]
    warped = cv2.remap(
        image_bgr,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )
    if synthesize and delta_px > 1.0:
        warped = _synthesize_keratin(warped, image_bgr, mask, delta_px, ridge_score)
    return warped


def forecast_horizons() -> tuple[int, ...]:
    return FORECAST_HORIZONS_DAYS


def _treatment_active(treatment: Treatment, moment: datetime) -> bool:
    if treatment.started_at > moment:
        return False
    if treatment.ended_at is not None and treatment.ended_at < moment:
        return False
    return True


def _is_pinned(sample: int, projection: float, pin_limit: float) -> bool:
    if sample == 0 or sample == CLASS_LUNULA:
        return True
    if sample == CLASS_NAIL_BED and projection <= pin_limit:
        return True
    if sample not in NAIL_CLASSES and sample != CLASS_FREE_MARGIN:
        return True
    return False


def _flat_mesh(width: int, height: int, cols: int, rows: int) -> list[MeshVertex]:
    vertices = []
    for row in range(rows):
        y_coord = (row + 0.5) * height / rows
        for col in range(cols):
            x_coord = (col + 0.5) * width / cols
            vertices.append(MeshVertex(x=x_coord, y=y_coord, dx=0.0, dy=0.0, pinned=True))
    return vertices


def _dense_displacement(mesh: list[MeshVertex], width: int, height: int) -> np.ndarray:
    if not mesh:
        return np.zeros((height, width, 2), np.float32)
    cols = FORECAST_MESH_COLS
    rows = FORECAST_MESH_ROWS
    if len(mesh) != cols * rows:
        cols = int(round(np.sqrt(len(mesh) * width / max(height, 1)))) or 1
        rows = max(len(mesh) // cols, 1)
    field = np.zeros((rows, cols, 2), np.float32)
    for index, vertex in enumerate(mesh[: rows * cols]):
        field[index // cols, index % cols] = (vertex.dx, vertex.dy)
    return cv2.resize(field, (width, height), interpolation=cv2.INTER_LINEAR)


def _synthesize_keratin(
    warped_bgr: np.ndarray,
    source_bgr: np.ndarray,
    mask: np.ndarray,
    delta_px: float,
    ridge_score: float,
) -> np.ndarray:
    """Tile the distal nail edge into the grown band and add faint longitudinal noise."""
    try:
        centroid, direction = axis_from_mask(mask)
    except MeasurementError:
        return warped_bgr
    height, width = mask.shape[:2]
    ys, xs = np.where(np.isin(mask, NAIL_CLASSES))
    points = np.stack([xs, ys], axis=1).astype(np.float64)
    projection = (points - centroid) @ direction
    tip = float(projection.max())
    normal = np.array([-direction[1], direction[0]], dtype=np.float64)
    strip = (projection > tip - 6.0)
    if int(strip.sum()) < 8:
        return warped_bgr
    strip_colors = source_bgr[ys[strip], xs[strip]].astype(np.float32)
    strip_lateral = (points[strip] - centroid) @ normal
    output = warped_bgr.copy()
    yy, xx = np.mgrid[0:height, 0:width]
    grid = np.stack([xx, yy], axis=-1).astype(np.float64)
    grid_proj = (grid - centroid) @ direction
    grid_lat = (grid - centroid) @ normal
    grown = (grid_proj > tip) & (grid_proj < tip + delta_px + 1.0)
    if not np.any(grown):
        return warped_bgr
    order = np.argsort(strip_lateral)
    colors = strip_colors[order]
    laterals = strip_lateral[order]
    grown_lat = grid_lat[grown]
    indexes = np.searchsorted(laterals, grown_lat).clip(0, len(colors) - 1)
    sampled = colors[indexes]
    noise_amp = 18.0 * float(np.clip(ridge_score, 0.0, 1.0))
    if noise_amp > 0:
        phase = np.sin(grown_lat / 3.0)
        sampled = sampled + (phase[:, None] * noise_amp)
    output[grown] = np.clip(sampled, 0, 255).astype(np.uint8)
    return output


def preview_deltas(v_observed: float, settings: Settings, alpha: float) -> list[dict]:
    rows = []
    for day in FORECAST_HORIZONS_DAYS:
        rows.append(
            {
                "t_days": day,
                "delta_mm": delta_length_mm(v_observed, settings.population_velocity_mm_day, alpha, day),
            }
        )
    return rows


def write_forecast_cache(db, user, settings: Settings, *, commit: bool = True) -> None:
    """Recompute per-finger forecast numbers after a capture or a treatment change."""
    from sqlalchemy import select

    from app.models import FingerBaseline

    baselines = list(db.scalars(select(FingerBaseline).where(FingerBaseline.user_id == user.id)).all())
    treatments = list(db.scalars(select(Treatment).where(Treatment.user_id == user.id)).all())
    moment = utcnow()
    for baseline in baselines:
        alpha = alpha_for_finger(treatments, baseline.finger_code.value, moment)
        velocity = float(baseline.observed_velocity_mm_day)
        baseline.forecast_cache_json = {
            "generated_at": moment.isoformat(),
            "alpha_tx": alpha,
            "v_observed_mm_day": velocity,
            "v_effective_mm_day": effective_velocity(velocity, settings.population_velocity_mm_day, alpha),
            "horizons": preview_deltas(velocity, settings, alpha),
        }
    if commit:
        db.commit()
