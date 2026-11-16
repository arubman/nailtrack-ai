from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from app.config import clear_settings_cache
from app.database import utcnow
from app.models import (
    Base,
    FingerBaseline,
    FingerCode,
    MaskSource,
    NailCapture,
    ScaleReferenceType,
    Treatment,
    TreatmentCategory,
    User,
)
from app.schemas import (
    AnchorKeypoints,
    CielabColor,
    HomographyRecord,
    QualityMetrics,
    TrackedFeature,
    WellnessInsight,
    reference_white_from_patch,
)


def _user(subject: str) -> User:
    return User(
        id=uuid.uuid4(),
        external_subject=subject,
        email=f"{subject}@example.com",
        display_name=subject,
    )


def _baseline(user: User, finger: FingerCode = FingerCode.L_INDEX) -> FingerBaseline:
    white = reference_white_from_patch(mean_rgb=(0.82, 0.8, 0.78), source="id1_card_white")
    anchors = AnchorKeypoints(
        left_fold=(10.0, 40.0),
        right_fold=(80.0, 42.0),
        dip_crease=[(20.0, 90.0), (70.0, 92.0)],
    )
    return FingerBaseline(
        user=user,
        finger_code=finger,
        calibrated_width_mm=12.4,
        scale_reference_type=ScaleReferenceType.ID1_CARD,
        scale_reference_width_mm=85.60,
        baseline_image_key=f"users/{user.id}/baselines/{finger.value}/day0.jpg",
        anchor_keypoints_json=anchors.model_dump(mode="json"),
        reference_white_json=white.model_dump(mode="json"),
    )


def test_round_trip_user_baseline_capture_and_treatment(session):
    user = _user("person-a")
    baseline = _baseline(user)
    captured_at = utcnow() - timedelta(days=1)
    quality = QualityMetrics(laplacian_var=180.0, glare_ratio=0.02, tilt_deg=3.0, passed=True)
    homography = HomographyRecord(
        matrix=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        ransac_inlier_rms=0.4,
    )
    color = CielabColor(L=72.0, a=4.0, b=8.0)
    feature = TrackedFeature(id="spot_1", type="leukonychia", rel_x=0.4, dist_from_cuticle_mm=3.2)
    capture = NailCapture(
        user=user,
        baseline=baseline,
        finger_code=baseline.finger_code,
        captured_at=captured_at,
        raw_image_key=f"users/{user.id}/captures/raw.jpg",
        aligned_image_key=f"users/{user.id}/captures/aligned.jpg",
        segmentation_mask_key=f"users/{user.id}/captures/mask.png",
        corrected_mask_key=f"users/{user.id}/captures/mask_corrected.png",
        mask_source=MaskSource.USER_CORRECTED,
        mask_updated_at=captured_at,
        homography_matrix_json=homography.model_dump(mode="json"),
        quality_metrics_json=quality.model_dump(mode="json"),
        nail_bed_length_mm=11.2,
        free_margin_length_mm=2.4,
        total_length_mm=13.6,
        lunula_ratio=0.18,
        ridge_gabor_score=0.22,
        cielab_color_json=color.model_dump(mode="json"),
        tracked_features_json=[feature.model_dump(mode="json")],
    )
    treatment = Treatment(
        user=user,
        started_at=captured_at,
        category=TreatmentCategory.TOPICAL_OIL,
        target_fingers_json=["L_INDEX"],
        velocity_modifier=1.0,
        notes="User-set modifier, not an app claim",
    )
    session.add_all([user, baseline, capture, treatment])
    session.commit()

    stored_capture = session.scalar(select(NailCapture).where(NailCapture.id == capture.id))
    assert isinstance(stored_capture.id, uuid.UUID)
    assert stored_capture.baseline_id == baseline.id
    assert stored_capture.user_id == user.id
    assert stored_capture.finger_code is FingerCode.L_INDEX
    assert stored_capture.mask_source is MaskSource.USER_CORRECTED
    assert stored_capture.corrected_mask_key.endswith("mask_corrected.png")
    assert stored_capture.raw_image_key.startswith("users/")
    assert "https://" not in (stored_capture.raw_image_key or "")
    assert baseline.reference_white_json["source"] == "id1_card_white"
    assert baseline.reference_white_json["target"] == "D65"
    assert stored_capture.quality_metrics_json["glare_ratio"] == pytest.approx(0.02)
    assert stored_capture.tracked_features_json[0]["type"] == "leukonychia"
    assert stored_capture.created_at.tzinfo is not None
    assert stored_capture.baseline.calibrated_width_mm == pytest.approx(12.4)
    insight = WellnessInsight(code="ridge", message="Ridges are visible on this nail.")
    assert "not medical advice" in insight.disclaimer.lower()


def test_finger_baseline_is_unique_per_user_not_globally(session):
    left = _user("left")
    right = _user("right")
    session.add_all([_baseline(left, FingerCode.L_THUMB), _baseline(right, FingerCode.L_THUMB)])
    session.commit()
    session.add(_baseline(left, FingerCode.L_THUMB))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_capture_cannot_point_at_another_users_baseline_or_a_different_finger(session):
    owner = _user("owner")
    other = _user("other")
    baseline = _baseline(owner, FingerCode.R_RING)
    session.add_all([owner, other, baseline])
    session.commit()

    mismatched_user = NailCapture(
        user_id=other.id,
        baseline_id=baseline.id,
        finger_code=baseline.finger_code,
        raw_image_key=None,
    )
    session.add(mismatched_user)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    mismatched_finger = NailCapture(
        user_id=owner.id,
        baseline_id=baseline.id,
        finger_code=FingerCode.L_PINKY,
    )
    session.add(mismatched_finger)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_mask_source_and_ridge_score_constraints(session):
    user = _user("masks")
    baseline = _baseline(user)
    session.add_all([user, baseline])
    session.commit()

    missing_correction = NailCapture(
        user_id=user.id,
        baseline_id=baseline.id,
        finger_code=baseline.finger_code,
        mask_source=MaskSource.USER_CORRECTED,
    )
    session.add(missing_correction)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    out_of_range = NailCapture(
        user_id=user.id,
        baseline_id=baseline.id,
        finger_code=baseline.finger_code,
        ridge_gabor_score=1.2,
    )
    session.add(out_of_range)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    classical = NailCapture(
        user_id=user.id,
        baseline_id=baseline.id,
        finger_code=baseline.finger_code,
        mask_source=MaskSource.AUTO_CLASSICAL,
        segmentation_mask_key=f"users/{user.id}/captures/auto.png",
        ridge_gabor_score=0.35,
    )
    session.add(classical)
    session.commit()
    assert classical.mask_source is MaskSource.AUTO_CLASSICAL


def test_deleting_a_user_removes_their_rows(session):
    user = _user("gone")
    baseline = _baseline(user)
    session.add_all(
        [
            user,
            baseline,
            NailCapture(
                user=user,
                baseline=baseline,
                finger_code=baseline.finger_code,
            ),
            Treatment(
                user=user,
                started_at=utcnow(),
                category=TreatmentCategory.GROOMING_TRIM,
                target_fingers_json=["ALL"],
            ),
        ]
    )
    session.commit()
    session.delete(user)
    session.commit()
    assert session.scalar(select(FingerBaseline)) is None
    assert session.scalar(select(NailCapture)) is None
    assert session.scalar(select(Treatment)) is None


def test_velocity_default_follows_config(session, monkeypatch):
    monkeypatch.setenv("DEFAULT_VELOCITY_MM_DAY", "0.2")
    clear_settings_cache()
    user = _user("speed")
    baseline = FingerBaseline(
        user=user,
        finger_code=FingerCode.R_MIDDLE,
        calibrated_width_mm=10.0,
    )
    session.add_all([user, baseline])
    session.commit()
    assert baseline.observed_velocity_mm_day == pytest.approx(0.2)


def test_ddl_uses_portable_uuid_and_json_without_native_enums():
    postgres = postgresql.dialect()
    sqlite_dialect = sqlite.dialect()
    rendered = []
    for table in Base.metadata.sorted_tables:
        rendered.append(str(CreateTable(table).compile(dialect=postgres)))
        sqlite_sql = str(CreateTable(table).compile(dialect=sqlite_dialect))
        assert "JSONB" not in sqlite_sql
    postgres_sql = "\n".join(rendered)
    assert "UUID" in postgres_sql
    assert "JSON" in postgres_sql
    assert "JSONB" not in postgres_sql
    assert "CREATE TYPE" not in postgres_sql
    tables = set(inspect_table_names())
    assert tables == {"users", "finger_baselines", "nail_captures", "treatments"}


def inspect_table_names() -> set[str]:
    return set(Base.metadata.tables)
