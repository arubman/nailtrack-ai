"""Forecast math, mesh pinning, spot migration, and treatment refresh."""

from __future__ import annotations

import uuid
from datetime import timedelta

import cv2
import numpy as np
import pytest

from app.config import Settings, clear_settings_cache, get_settings
from app.constants import NOT_MEDICAL_ADVICE
from app.database import utcnow
from app.models import FingerBaseline, FingerCode, MaskSource, NailCapture, ScaleReferenceType, User
from app.services.feature_detector import detect_tracked_features
from app.services.forecaster import (
    alpha_for_finger,
    build_control_mesh,
    delta_length_mm,
    effective_velocity,
    migrate_feature,
    update_observed_velocity,
    warp_forecast,
)
from app.services.geometry import axis_from_mask
from app.services.imaging import encode_jpeg, encode_mask_png
from app.services.insights import build_insights
from app.storage import blob_key_for_user


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        auth_mode="dev",
        database_url="sqlite://",
        storage_backend="local",
        population_velocity_mm_day=0.115,
        ridge_score_insight_threshold=0.35,
    )


def test_delta_matches_the_blended_formula():
    delta = delta_length_mm(0.11, 0.115, 1.0, 10)
    assert delta == pytest.approx((0.7 * 0.11 + 0.3 * 0.115) * 10)
    assert effective_velocity(0.11, 0.115, 2.0) == pytest.approx(delta / 10 * 2)


def test_alpha_uses_active_treatments_for_that_finger():
    from app.models import Treatment, TreatmentCategory

    now = utcnow()
    biotin = Treatment(
        user_id=uuid.uuid4(),
        started_at=now - timedelta(days=3),
        category=TreatmentCategory.SUPPLEMENT_BIOTIN,
        target_fingers_json=["L_INDEX"],
        velocity_modifier=1.2,
    )
    oil = Treatment(
        user_id=biotin.user_id,
        started_at=now - timedelta(days=1),
        category=TreatmentCategory.TOPICAL_OIL,
        target_fingers_json=["ALL"],
        velocity_modifier=1.1,
    )
    ended = Treatment(
        user_id=biotin.user_id,
        started_at=now - timedelta(days=10),
        ended_at=now - timedelta(days=2),
        category=TreatmentCategory.GROOMING_TRIM,
        target_fingers_json=["L_INDEX"],
        velocity_modifier=2.0,
    )
    other = Treatment(
        user_id=biotin.user_id,
        started_at=now - timedelta(days=1),
        category=TreatmentCategory.TRAUMA_NOTE,
        target_fingers_json=["R_THUMB"],
        velocity_modifier=3.0,
    )
    assert alpha_for_finger([biotin, oil, ended, other], "L_INDEX", now) == pytest.approx(1.32)
    assert alpha_for_finger([other], "L_INDEX", now) == pytest.approx(1.0)


def test_velocity_ema_ignores_trims_and_short_gaps():
    assert update_observed_velocity(10.0, 10.4, 4.0, 0.11) == pytest.approx(0.3 * 0.1 + 0.7 * 0.11)
    assert update_observed_velocity(10.0, 9.0, 5.0, 0.11) is None
    assert update_observed_velocity(10.0, 10.4, 0.2, 0.11) is None


def test_mesh_pins_proximal_nail_and_tapers_the_free_margin():
    mask = np.zeros((240, 160), np.uint8)
    mask[40:80, 60:100] = 1
    mask[80:180, 60:100] = 2
    mask[180:220, 60:100] = 3
    delta_px = 40.0
    mesh = build_control_mesh(mask, delta_px=delta_px)
    centroid, direction = axis_from_mask(mask)
    pinned = [vertex for vertex in mesh if vertex.pinned]
    moving = [vertex for vertex in mesh if not vertex.pinned]
    assert pinned
    assert all(vertex.dx == 0 and vertex.dy == 0 for vertex in pinned)
    # Lunula samples must be pinned.
    lunula_vertices = [
        vertex
        for vertex in mesh
        if mask[min(239, int(vertex.y)), min(159, int(vertex.x))] == 1
    ]
    assert lunula_vertices
    assert all(vertex.pinned for vertex in lunula_vertices)
    distal = max(moving, key=lambda vertex: vertex.y)
    shift = np.array([distal.dx, distal.dy])
    # Grid centers do not sit on the exact tip pixel, so the peak shift is a bit under Δy.
    assert float(shift @ direction) > 0.75 * delta_px
    distal_side = min((vertex for vertex in moving if vertex.y > 190), key=lambda vertex: vertex.x)
    inward = np.array([distal_side.dx, distal_side.dy])
    normal = np.array([-direction[1], direction[0]])
    lateral = float((np.array([distal_side.x, distal_side.y]) - centroid) @ normal)
    inward_component = float(inward @ normal)
    # Negative product means the lateral part of the shift points toward the axis.
    assert lateral * inward_component < 0
    assert 0 < abs(inward_component) <= 0.02 * delta_px + 0.2


def test_spot_migration_clips_past_the_forecast_tip():
    # v_obs 0.30 mm/day, blended with 0.10 is 0.24, so the mark outruns the tip.
    v_obs = 0.30
    v_pop = 0.10
    alpha = 1.0
    total = 10.0
    distance = 9.5
    day = 30
    shift = v_obs * day
    tip = total + delta_length_mm(v_obs, v_pop, alpha, day)
    updated, clipped = migrate_feature(distance, shift_mm=shift, tip_mm=tip)
    assert updated == pytest.approx(distance + shift)
    assert clipped
    short, still_there = migrate_feature(distance, shift_mm=v_obs * 7, tip_mm=total + delta_length_mm(v_obs, v_pop, alpha, 7))
    assert short > distance
    assert not still_there


def test_feature_detector_finds_a_spot_and_a_crack_only():
    image = np.full((180, 120, 3), (150, 120, 190), np.uint8)
    mask = np.zeros((180, 120), np.uint8)
    mask[30:160, 40:80] = 2
    cv2.circle(image, (60, 70), 6, (245, 245, 245), thickness=-1)
    cv2.line(image, (48, 120), (72, 120), (20, 20, 20), thickness=2)
    features = detect_tracked_features(image, mask, mm_per_px=0.1)
    kinds = {feature["type"] for feature in features}
    assert "leukonychia" in kinds
    assert "crack" in kinds
    assert len(features) <= 4
    spot = next(feature for feature in features if feature["type"] == "leukonychia")
    assert 0.0 <= spot["rel_x"] <= 1.0
    assert spot["dist_from_cuticle_mm"] > 0


def test_keratin_synthesis_fills_past_a_short_free_margin():
    image = np.full((160, 100, 3), (20, 80, 20), np.uint8)
    mask = np.zeros((160, 100), np.uint8)
    mask[30:120, 35:65] = 2
    mask[120:126, 35:65] = 3
    image[120:126, 35:65] = (40, 40, 210)
    mesh = build_control_mesh(mask, delta_px=24.0)
    warped = warp_forecast(
        image,
        mask,
        mesh,
        delta_px=24.0,
        synthesize=True,
        ridge_score=0.8,
    )
    beyond = warped[127:145, 40:60]
    assert beyond.shape[0] > 0
    assert float(beyond[:, :, 2].mean()) > float(image[127:145, 40:60][:, :, 2].mean()) + 20


def test_insights_include_disclaimer_and_avoid_diagnosis_language():
    insights = build_insights(
        lengths=[(8.0, 12.0), (8.6, 12.4)],
        ridge_score=0.5,
        ridge_threshold=0.35,
        features=[{"id": "spot_1", "type": "leukonychia", "rel_x": 0.4, "dist_from_cuticle_mm": 4.0}],
        total_length_mm=12.4,
        velocity_mm_day=0.1,
    )
    assert insights.disclaimer == NOT_MEDICAL_ADVICE
    codes = {item.code for item in insights.insights}
    assert "matrix_cycle" in codes
    assert "bed_length_increase" in codes
    assert "longitudinal_ridges" in codes
    assert any(code.startswith("feature_clear_") for code in codes)
    blob = " ".join(item.message for item in insights.insights).lower()
    assert "you have" not in blob
    assert "onychomycosis" not in blob
    assert all(item.disclaimer == NOT_MEDICAL_ADVICE for item in insights.insights)
    quiet = build_insights(
        lengths=[(8.0, 12.0)],
        ridge_score=0.1,
        ridge_threshold=0.35,
        features=[],
        total_length_mm=12.0,
        velocity_mm_day=0.1,
    )
    assert [item.code for item in quiet.insights] == ["matrix_cycle"]


def test_timeline_and_treatment_refresh(client, db, monkeypatch):
    monkeypatch.setenv("POPULATION_VELOCITY_MM_DAY", "0.115")
    clear_settings_cache()
    settings = get_settings()
    me = uuid.UUID(client.get("/api/v1/me").json()["user"]["id"])
    user = db.get(User, me)
    image = np.full((200, 120, 3), (160, 120, 180), np.uint8)
    mask = np.zeros((200, 120), np.uint8)
    mask[40:150, 40:80] = 2
    mask[150:190, 40:80] = 3
    storage = client.app.state.storage
    image_key = blob_key_for_user(me, "captures", "today", "aligned.jpg")
    mask_key = blob_key_for_user(me, "captures", "today", "mask.png")
    storage.put_bytes(image_key, encode_jpeg(image), "image/jpeg")
    storage.put_bytes(mask_key, encode_mask_png(mask), "image/png")
    baseline = FingerBaseline(
        user_id=me,
        finger_code=FingerCode.L_INDEX,
        calibrated_width_mm=4.0,
        scale_reference_type=ScaleReferenceType.ID1_CARD,
        scale_reference_width_mm=85.6,
        anchor_keypoints_json={"left_fold": [40, 100], "right_fold": [80, 100], "dip_crease": [[40, 30], [80, 30]]},
        reference_white_json={
            "space": "linear_srgb",
            "mean_rgb": [1, 1, 1],
            "von_kries_gains": [1, 1, 1],
            "target": "D65",
            "source": "id1_card_white",
        },
        observed_velocity_mm_day=0.2,
        baseline_image_key=image_key,
    )
    db.add(baseline)
    db.flush()
    older = NailCapture(
        user_id=me,
        baseline_id=baseline.id,
        finger_code=FingerCode.L_INDEX,
        captured_at=utcnow() - timedelta(days=14),
        aligned_image_key=image_key,
        segmentation_mask_key=mask_key,
        mask_source=MaskSource.AUTO_CLASSICAL,
        nail_bed_length_mm=8.0,
        free_margin_length_mm=2.0,
        total_length_mm=10.0,
        ridge_gabor_score=0.5,
        tracked_features_json=[{"id": "spot_1", "type": "leukonychia", "rel_x": 0.5, "dist_from_cuticle_mm": 6.0}],
    )
    today = NailCapture(
        user_id=me,
        baseline_id=baseline.id,
        finger_code=FingerCode.L_INDEX,
        captured_at=utcnow(),
        aligned_image_key=image_key,
        segmentation_mask_key=mask_key,
        mask_source=MaskSource.AUTO_CLASSICAL,
        nail_bed_length_mm=8.5,
        free_margin_length_mm=2.2,
        total_length_mm=10.7,
        ridge_gabor_score=0.5,
        tracked_features_json=[{"id": "spot_1", "type": "leukonychia", "rel_x": 0.5, "dist_from_cuticle_mm": 6.0}],
    )
    db.add_all([older, today])
    db.commit()

    first = client.get("/api/v1/fingers/L_INDEX/timeline")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["disclaimer"] == NOT_MEDICAL_ADVICE
    assert body["alpha_tx"] == pytest.approx(1.0)
    kinds = [frame["kind"] for frame in body["frames"]]
    assert kinds[0] == "history"
    assert "today" in kinds
    forecast = [frame for frame in body["frames"] if frame["kind"] == "forecast"]
    assert [frame["t_days"] for frame in forecast] == [7, 14, 30, 60, 90]
    assert forecast[0]["mesh"]
    assert any(vertex["pinned"] and vertex["dx"] == 0 and vertex["dy"] == 0 for vertex in forecast[0]["mesh"])
    spot = forecast[2]["features"][0]
    assert spot["dist_from_cuticle_mm"] == pytest.approx(6.0 + 0.2 * 30)
    assert any(item["code"] == "longitudinal_ridges" for item in body["insights"])
    assert all("not medical advice" in item["disclaimer"].lower() for item in body["insights"])

    created = client.post(
        "/api/v1/treatments",
        json={
            "started_at": (utcnow() - timedelta(days=1)).isoformat(),
            "category": "TOPICAL_OIL",
            "target_fingers": ["L_INDEX"],
            "velocity_modifier": 2.0,
        },
    )
    assert created.status_code == 201
    db.refresh(baseline)
    assert baseline.forecast_cache_json["alpha_tx"] == pytest.approx(2.0)
    second = client.get("/api/v1/fingers/L_INDEX/timeline")
    assert second.status_code == 200
    grown = next(frame for frame in second.json()["frames"] if frame["kind"] == "forecast" and frame["t_days"] == 30)
    assert grown["delta_length_mm"] == pytest.approx(body["frames"][-3]["delta_length_mm"] * 2, rel=0.02)

    client.delete(f"/api/v1/treatments/{created.json()['id']}")
    db.expire_all()
    refreshed = db.get(FingerBaseline, baseline.id)
    assert refreshed.forecast_cache_json["alpha_tx"] == pytest.approx(1.0)
    del user
    del settings
