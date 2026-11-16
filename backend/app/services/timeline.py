"""Assemble the finger timeline: history, today, and forecast horizons."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.constants import FORECAST_HORIZONS_DAYS, FREE_MARGIN_SYNTHESIS_BELOW_MM, NOT_MEDICAL_ADVICE
from app.images import image_link
from app.models import FingerBaseline, FingerCode, NailCapture, Treatment, User
from app.schemas import TimelineFeature, TimelineFrame, TimelineRead
from app.services.forecaster import (
    alpha_for_finger,
    build_control_mesh,
    days_until_clear,
    delta_length_mm,
    effective_velocity,
    migrate_feature,
    warp_forecast,
    write_forecast_cache,
)
from app.services.imaging import decode_bgr, decode_mask_png, encode_jpeg
from app.services.insights import build_insights
from app.storage import Storage, StorageNotFound, blob_key_for_user


def build_finger_timeline(
    db: Session,
    storage: Storage,
    user: User,
    finger_code: FingerCode,
    settings: Settings,
) -> TimelineRead | None:
    baseline = db.scalar(
        select(FingerBaseline).where(
            FingerBaseline.user_id == user.id,
            FingerBaseline.finger_code == finger_code,
        )
    )
    if baseline is None:
        return None
    captures = list(
        db.scalars(
            select(NailCapture)
            .where(NailCapture.user_id == user.id, NailCapture.baseline_id == baseline.id)
            .order_by(NailCapture.captured_at.asc())
        ).all()
    )
    treatments = list(db.scalars(select(Treatment).where(Treatment.user_id == user.id)).all())
    alpha = alpha_for_finger(treatments, finger_code.value)
    velocity = float(baseline.observed_velocity_mm_day)
    population = settings.population_velocity_mm_day
    v_eff = effective_velocity(velocity, population, alpha)
    latest = captures[-1] if captures else None
    origin = latest.captured_at if latest is not None else None
    lengths = [(row.nail_bed_length_mm, row.total_length_mm) for row in captures]
    features = list(latest.tracked_features_json or []) if latest is not None else []
    ridge = latest.ridge_gabor_score if latest is not None else None
    total = latest.total_length_mm if latest is not None else None
    insights = build_insights(
        lengths=lengths,
        ridge_score=ridge,
        ridge_threshold=settings.ridge_score_insight_threshold,
        features=features,
        total_length_mm=total,
        velocity_mm_day=velocity,
    )
    frames: list[TimelineFrame] = []
    for capture in captures:
        t_days = 0.0 if origin is None else (capture.captured_at - origin).total_seconds() / 86400.0
        kind = "today" if capture.id == (latest.id if latest else None) else "history"
        frames.append(
            TimelineFrame(
                t_days=t_days,
                kind=kind,
                capture_id=capture.id,
                image=image_link(storage, capture.aligned_image_key or capture.raw_image_key),
                mask=image_link(storage, _mask_key(capture)),
                nail_bed_length_mm=capture.nail_bed_length_mm,
                free_margin_length_mm=capture.free_margin_length_mm,
                total_length_mm=capture.total_length_mm,
                features=_features_at(
                    list(capture.tracked_features_json or []),
                    shift_mm=0.0,
                    clip_tip_mm=capture.total_length_mm,
                    clear_length_mm=capture.total_length_mm,
                    velocity=velocity,
                ),
            )
        )
    if latest is not None and latest.aligned_image_key and latest.total_length_mm is not None:
        image, mask = _load_aligned(storage, latest)
        mm_per_px = _mm_per_px(baseline)
        for day in FORECAST_HORIZONS_DAYS:
            delta_mm = delta_length_mm(velocity, population, alpha, day)
            delta_px = delta_mm / mm_per_px if mm_per_px else 0.0
            mesh = build_control_mesh(mask, delta_px=delta_px) if mask is not None else None
            spot_shift = velocity * day
            tip = latest.total_length_mm + delta_mm
            synthesize = (latest.free_margin_length_mm or 0.0) < FREE_MARGIN_SYNTHESIS_BELOW_MM
            forecast_image = None
            if image is not None and mask is not None and mesh is not None:
                warped = warp_forecast(
                    image,
                    mask,
                    mesh,
                    delta_px=delta_px,
                    synthesize=synthesize,
                    ridge_score=float(latest.ridge_gabor_score or 0.0),
                )
                key = blob_key_for_user(user.id, "forecasts", finger_code.value, f"plus{day}.jpg")
                storage.put_bytes(key, encode_jpeg(warped), "image/jpeg")
                forecast_image = image_link(storage, key)
            frames.append(
                TimelineFrame(
                    t_days=float(day),
                    kind="forecast",
                    capture_id=latest.id,
                    image=forecast_image,
                    mask=image_link(storage, _mask_key(latest)),
                    delta_length_mm=delta_mm,
                    nail_bed_length_mm=latest.nail_bed_length_mm,
                    free_margin_length_mm=(latest.free_margin_length_mm or 0.0) + delta_mm,
                    total_length_mm=latest.total_length_mm + delta_mm,
                    mesh=mesh,
                    features=_features_at(
                        features,
                        shift_mm=spot_shift,
                        clip_tip_mm=tip,
                        clear_length_mm=latest.total_length_mm,
                        velocity=velocity,
                    ),
                    synthesized_free_margin=synthesize,
                )
            )
    write_forecast_cache(db, user, settings, commit=True)
    return TimelineRead(
        finger_code=finger_code,
        disclaimer=NOT_MEDICAL_ADVICE,
        insights=insights.insights,
        v_observed_mm_day=velocity,
        v_effective_mm_day=v_eff,
        alpha_tx=alpha,
        frames=frames,
    )


def _features_at(
    features: list[dict],
    *,
    shift_mm: float,
    clip_tip_mm: float | None,
    clear_length_mm: float | None,
    velocity: float,
) -> list[TimelineFeature]:
    rows: list[TimelineFeature] = []
    for feature in features:
        distance = float(feature["dist_from_cuticle_mm"])
        updated, clipped = migrate_feature(
            distance,
            shift_mm=shift_mm,
            tip_mm=clip_tip_mm if clip_tip_mm is not None else 1e9,
        )
        rows.append(
            TimelineFeature(
                id=str(feature["id"]),
                type=feature["type"],
                rel_x=float(feature["rel_x"]),
                dist_from_cuticle_mm=updated,
                clipped=clipped,
                days_until_clear=(
                    days_until_clear(distance, clear_length_mm, velocity) if clear_length_mm is not None else None
                ),
            )
        )
    return rows


def _mask_key(capture: NailCapture) -> str | None:
    if capture.mask_source and capture.mask_source.value == "user_corrected" and capture.corrected_mask_key:
        return capture.corrected_mask_key
    return capture.segmentation_mask_key


def _mm_per_px(baseline: FingerBaseline) -> float:
    anchors = baseline.anchor_keypoints_json or {}
    left = anchors.get("left_fold")
    right = anchors.get("right_fold")
    if not left or not right:
        return 0.0
    import numpy as np

    width_px = float(np.linalg.norm(np.array(left, dtype=float) - np.array(right, dtype=float)))
    if width_px < 1e-3:
        return 0.0
    return float(baseline.calibrated_width_mm) / width_px


def _load_aligned(storage: Storage, capture: NailCapture) -> tuple:
    try:
        image = decode_bgr(storage.get_bytes(capture.aligned_image_key), max_edge=10_000)
    except (StorageNotFound, ValueError):
        return None, None
    mask = None
    key = capture.corrected_mask_key if capture.mask_source and capture.mask_source.value == "user_corrected" else capture.segmentation_mask_key
    if key:
        try:
            mask = decode_mask_png(storage.get_bytes(key))
        except (StorageNotFound, ValueError):
            mask = None
    if mask is not None and mask.shape[:2] != image.shape[:2]:
        mask = None
    return image, mask


