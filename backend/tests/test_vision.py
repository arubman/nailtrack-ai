"""Synthetic-image checks for the classical vision pipeline.

MediaPipe is the real hand detector. Drawn nails are not hands, so the HTTP
analyze path must say so. Measurement, card scale, and homography are tested
on images whose geometry we drew, with a pose that matches that drawing.
"""

from __future__ import annotations

import csv
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.config import Settings, clear_settings_cache
from app.constants import ID1_CARD_WIDTH_MM
from app.models import FingerCode, MaskSource, ScaleReferenceType
from app.services.biometric_metrics import gabor_scores, measure_nail
from app.services.card_detector import detect_card
from app.services.colorimetry import mean_lab, srgb_uint8_to_linear
from app.services.geometry import FingerPose, pitch_degrees
from app.services.hand_recognizer import recognize_finger
from app.services.homography_align import align_to_baseline, build_invariant_mask
from app.services.imaging import encode_jpeg, encode_mask_png
from app.services.nail_segmenter import segment_nail
from app.services.pipeline import HandObservation, analyze_capture, apply_corrected_mask, calibrate_finger
from app.services.quality_gate import blocks_save, evaluate_quality
from tests.synthetic import draw_calibration_scene, draw_striped_nail


def _settings(**overrides) -> Settings:
    base = dict(
        environment="test",
        auth_mode="dev",
        database_url="sqlite://",
        storage_backend="local",
        blur_laplacian_min=1.0,
        glare_ratio_max=0.5,
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_pitch_formula_and_blank_frame_have_no_hand():
    flat = pitch_degrees((0.0, 0.0, 0.0), (0.0, 0.1, 0.0))
    steep = pitch_degrees((0.0, 0.0, 0.0), (0.0, 0.1, 0.1))
    assert flat == pytest.approx(0.0, abs=1e-6)
    assert steep == pytest.approx(45.0, abs=0.05)
    blank = np.zeros((180, 240, 3), np.uint8)
    observation = recognize_finger(
        blank,
        "L_INDEX",
        Settings(_env_file=None, environment="test", auth_mode="dev").resolved_hand_model_path,
    )
    assert observation.pose is None
    assert observation.hand_count == 0


def test_quality_gate_flags_blur_glare_tilt_and_missing_hand():
    settings = _settings(blur_laplacian_min=110.0, glare_ratio_max=0.08, tilt_deg_max=10.0)
    sharp = np.zeros((80, 80, 3), np.uint8)
    sharp[::2, ::2] = (255, 255, 255)
    mask = np.zeros((80, 80), np.uint8)
    mask[:, :] = 2
    pose = FingerPose(
        dip_xy=(40, 10),
        tip_xy=(40, 70),
        dip_xyz=(0.0, 0.0, 0.0),
        tip_xyz=(0.0, 1.0, 0.0),
    )
    sharp_report = evaluate_quality(sharp, mask, pose, settings)
    assert "RETAKE_BLURRY" not in sharp_report.metrics.reasons
    assert sharp_report.metrics.hand_detected is True

    blurred = cv2.GaussianBlur(sharp, (15, 15), 0)
    blur_report = evaluate_quality(blurred, mask, pose, settings)
    assert "RETAKE_BLURRY" in blur_report.metrics.reasons
    assert blur_report.metrics.laplacian_var < 110.0

    glare = sharp.copy()
    glare[10:40, 10:40] = (255, 255, 255)
    glare_report = evaluate_quality(glare, mask, pose, settings)
    assert "RETAKE_GLARE" in glare_report.metrics.reasons
    assert glare_report.metrics.glare_ratio > 0.08

    tilted = FingerPose(
        dip_xy=(40, 10),
        tip_xy=(40, 70),
        dip_xyz=(0.0, 0.0, 0.0),
        tip_xyz=(0.0, 0.2, 0.2),
    )
    tilt_report = evaluate_quality(sharp, mask, tilted, settings)
    assert "RETAKE_TILT" in tilt_report.metrics.reasons
    assert tilt_report.metrics.tilt_deg == pytest.approx(45.0, abs=0.1)

    missing = evaluate_quality(sharp, mask, None, settings)
    assert "RETAKE_NO_HAND" in missing.metrics.reasons
    assert missing.metrics.tilt_deg is None
    assert not blocks_save(list(missing.metrics.reasons), force_save=True)
    assert blocks_save(["RETAKE_NO_HAND"], force_save=False)
    assert blocks_save(["RETAKE_NO_HAND", "RETAKE_NO_NAIL"], force_save=True)
    assert not blocks_save(["RETAKE_BLURRY"], force_save=True)
    assert blocks_save(["RETAKE_BLURRY"], force_save=False)


def test_segmenter_and_raycast_recover_drawn_lengths():
    scene = draw_calibration_scene()
    mask = segment_nail(scene.image, scene.pose)
    nail = np.isin(mask, (1, 2, 3))
    assert int(nail.sum()) > 1000
    # Lunula is proximal, free margin distal, on a vertical finger.
    lunula_y = np.where(mask == 1)[0]
    free_y = np.where(mask == 3)[0]
    bed_y = np.where(mask == 2)[0]
    assert lunula_y.size > 50
    assert bed_y.size > 50
    assert free_y.size > 50
    assert float(lunula_y.mean()) < float(bed_y.mean()) < float(free_y.mean())

    mm_per_px = ID1_CARD_WIDTH_MM / scene.card_long_px
    measured = measure_nail(
        scene.image,
        mask,
        mm_per_px=mm_per_px,
        von_kries_gains=(1.0, 1.0, 1.0),
        gabor_energy_floor=1e-8,
    )
    expected_bed = scene.bed_length_px * mm_per_px
    expected_free = scene.free_length_px * mm_per_px
    assert measured.nail_bed_length_mm == pytest.approx(expected_bed, rel=0.12, abs=1.0)
    assert measured.free_margin_length_mm == pytest.approx(expected_free, rel=0.15, abs=1.0)
    assert measured.total_length_mm == pytest.approx(expected_bed + expected_free, rel=0.12, abs=1.5)
    assert 0.0 < measured.lunula_ratio < 0.6


def test_exact_mask_raycast_is_within_a_pixel():
    mask = np.zeros((200, 80), np.uint8)
    mask[40:60, 20:60] = 1
    mask[60:140, 20:60] = 2
    mask[140:170, 20:60] = 3
    image = np.full((200, 80, 3), (120, 140, 180), np.uint8)
    measured = measure_nail(
        image,
        mask,
        mm_per_px=0.1,
        von_kries_gains=(1.0, 1.0, 1.0),
        gabor_energy_floor=1e-8,
    )
    # Cuticle at y=40, onychocorneal at y=140, tip at y=170. Half-pixel edges.
    assert measured.nail_bed_length_mm == pytest.approx(10.0, abs=0.25)
    assert measured.free_margin_length_mm == pytest.approx(3.0, abs=0.25)


def test_card_scale_and_white_reference():
    scene = draw_calibration_scene()
    card = detect_card(scene.image, reference_type="ID1_CARD", long_edge_mm=ID1_CARD_WIDTH_MM)
    assert card is not None
    assert card.long_edge_px == pytest.approx(scene.card_long_px, rel=0.05)
    assert card.mm_per_px == pytest.approx(ID1_CARD_WIDTH_MM / scene.card_long_px, rel=0.05)
    assert card.reference_white.source == "id1_card_white"
    assert card.reference_white.target == "D65"
    # The card interior is white, so each linear channel is close to 1.
    assert card.reference_white.mean_rgb[0] == pytest.approx(1.0, abs=0.08)
    assert card.reference_white.mean_rgb[1] == pytest.approx(card.reference_white.mean_rgb[0], abs=0.03)
    adapted = tuple(channel * gain for channel, gain in zip(card.reference_white.mean_rgb, card.reference_white.von_kries_gains))
    assert adapted == pytest.approx((1.0, 1.0, 1.0), abs=1e-6)


def test_von_kries_neutralizes_the_reference_before_lab():
    image = np.zeros((30, 30, 3), np.uint8)
    # sRGB encoding of linear 0.8 is about 231.
    image[:, :] = (231, 231, 231)
    mask = np.zeros((30, 30), np.uint8)
    mask[:, :] = 2
    gains = (1.0 / 0.8, 1.0 / 0.8, 1.0 / 0.8)
    # The stored reference is the linear value; the painted patch is the sRGB of that value.
    linear = srgb_uint8_to_linear(np.array([[231, 231, 231]], dtype=np.uint8))[0]
    assert linear[0] == pytest.approx(0.8, abs=0.02)
    lightness, a_star, b_star = mean_lab(image, mask == 2, gains)
    assert lightness == pytest.approx(100.0, abs=1.5)
    assert a_star == pytest.approx(0.0, abs=1.0)
    assert b_star == pytest.approx(0.0, abs=1.0)


def test_gabor_longitudinal_stripes_score_higher_than_transverse():
    image, mask = draw_striped_nail(longitudinal=True)
    centroid = np.array([60.0, 110.0])
    direction = np.array([0.0, 1.0])
    ridge, transverse = gabor_scores(image, mask, centroid, direction, energy_floor=1e-8)
    assert ridge > 0.55
    assert ridge > transverse
    cross_image, cross_mask = draw_striped_nail(longitudinal=False)
    cross_ridge, cross_trans = gabor_scores(cross_image, cross_mask, centroid, direction, energy_floor=1e-8)
    assert cross_trans > cross_ridge


def test_homography_recovers_a_known_warp_and_ecc_fallback_is_affine():
    baseline = _textured_baseline()
    homography = np.array(
        [[1.04, 0.02, 6.0], [-0.015, 1.03, -4.0], [0.00008, -0.00005, 1.0]],
        dtype=np.float64,
    )
    capture = cv2.warpPerspective(baseline, homography, (baseline.shape[1], baseline.shape[0]))
    invariant = np.zeros(baseline.shape[:2], np.uint8)
    invariant[30:270, 30:270] = 255
    # Keep the nail center out of the feature mask, as the aligner does in production.
    cv2.circle(invariant, (150, 150), 40, 0, thickness=-1)
    capture_invariant = cv2.warpPerspective(invariant, homography, (invariant.shape[1], invariant.shape[0]))
    result = align_to_baseline(
        baseline,
        capture,
        baseline_invariant=invariant,
        capture_invariant=capture_invariant,
    )
    assert result.method in {"homography_ecc", "homography_ransac", "affine_fallback"}
    assert 0.0 <= result.alignment_quality <= 1.0
    expected = np.linalg.inv(homography)
    error = _corner_error(result.matrix, expected, baseline.shape[1], baseline.shape[0])
    assert error < 3.0

    # Uncorrelated skin texture makes ECC refuse to converge. Four stable
    # color marks still give the affine fit something to use.
    rng = np.random.default_rng(1)
    flat = rng.integers(80, 180, size=(200, 200, 3), dtype=np.uint8)
    shifted = rng.integers(80, 180, size=(200, 200, 3), dtype=np.uint8)
    for (x_coord, y_coord), color in (
        ((30, 30), (0, 0, 255)),
        ((150, 36), (0, 255, 0)),
        ((34, 150), (255, 0, 0)),
        ((148, 152), (0, 255, 255)),
    ):
        cv2.rectangle(flat, (x_coord, y_coord), (x_coord + 16, y_coord + 16), color, thickness=-1)
        cv2.rectangle(
            shifted,
            (x_coord + 8, y_coord + 3),
            (x_coord + 24, y_coord + 19),
            color,
            thickness=-1,
        )
    full = np.full(flat.shape[:2], 255, np.uint8)
    fallback = align_to_baseline(
        flat,
        shifted,
        baseline_invariant=full,
        capture_invariant=full,
    )
    assert fallback.method == "affine_fallback"
    assert fallback.ecc_correlation is None
    assert 0.0 <= fallback.alignment_quality <= 1.0
    assert np.isfinite(fallback.matrix).all()


def test_invariant_mask_excludes_the_free_margin():
    mask = np.zeros((120, 80), np.uint8)
    mask[40:100, 25:55] = 2
    mask[100:115, 25:55] = 3
    pose = FingerPose(dip_xy=(40, 30), tip_xy=(40, 110))
    invariant = build_invariant_mask(mask, pose)
    assert invariant[108, 40] == 0
    assert int(invariant.sum()) > 0


def test_calibrate_persists_millimetres_and_analyze_requires_a_hand(client, db, monkeypatch):
    monkeypatch.setenv("BLUR_LAPLACIAN_MIN", "0.5")
    monkeypatch.setenv("GLARE_RATIO_MAX", "0.9")
    clear_settings_cache()
    from app.config import get_settings
    from app.models import User

    settings = get_settings()
    scene = draw_calibration_scene()
    me = client.get("/api/v1/me").json()["user"]["id"]
    import uuid

    user = db.get(User, uuid.UUID(me))
    stored = calibrate_finger(
        db,
        client.app.state.storage,
        user,
        scene.image,
        FingerCode.R_INDEX,
        HandObservation(pose=scene.pose, hand_count=1),
        reference_type=ScaleReferenceType.ID1_CARD,
        reference_width_mm=None,
        settings=settings,
    )
    mm_per_px = ID1_CARD_WIDTH_MM / scene.card_long_px
    expected_width = scene.nail_width_px * mm_per_px
    assert stored.baseline.calibrated_width_mm == pytest.approx(expected_width, rel=0.15, abs=1.5)
    assert stored.reference_white.source == "id1_card_white"
    assert stored.baseline.baseline_mask_key.endswith("day0_mask.png")

    encoded = encode_jpeg(scene.image)
    calibrate_response = client.post(
        "/api/v1/calibrate",
        data={"finger_code": "L_INDEX", "reference_type": "ID1_CARD"},
        files={"image": ("nail.jpg", encoded, "image/jpeg")},
    )
    assert calibrate_response.status_code == 422
    assert "RETAKE_NO_HAND" in calibrate_response.json()["reasons"]

    rejected = client.post(
        "/api/v1/captures/analyze",
        data={"finger_code": "R_INDEX", "force_save": "false"},
        files={"image": ("nail.jpg", encoded, "image/jpeg")},
    )
    assert rejected.status_code == 422
    body = rejected.json()
    assert body["passed"] is False
    assert "RETAKE_NO_HAND" in body["reasons"]
    assert body["metrics"]["hand_detected"] is False
    assert any("knuckle" in message.lower() for message in body["messages"])

    from sqlalchemy import func, select
    from app.models import NailCapture

    assert db.scalar(select(func.count()).select_from(NailCapture)) == 0

    # A close-up can hide the palm. Saving anyway stores the photo and still
    # records that no hand was detected.
    saved = client.post(
        "/api/v1/captures/analyze",
        data={"finger_code": "R_INDEX", "force_save": "true"},
        files={"image": ("nail.jpg", encoded, "image/jpeg")},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["quality"]["hand_detected"] is False
    assert db.scalar(select(func.count()).select_from(NailCapture)) == 1

    # A capture saved by the same pipeline can accept a corrected mask over HTTP.
    analyzed = analyze_capture(
        db,
        client.app.state.storage,
        user,
        scene.image,
        FingerCode.R_INDEX,
        HandObservation(pose=scene.pose, hand_count=1),
        force_save=False,
        settings=settings,
    )
    assert analyzed.capture.mask_source is MaskSource.AUTO_CLASSICAL
    assert analyzed.capture.segmentation_mask_key
    assert analyzed.homography is not None
    assert analyzed.capture.transverse_gabor_score is not None
    corrected = db_mask_with_longer_free_margin(client, analyzed.capture.segmentation_mask_key)
    response = client.post(
        f"/api/v1/captures/{analyzed.capture.id}/mask",
        files={"mask": ("mask.png", encode_mask_png(corrected), "image/png")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["mask_source"] == "user_corrected"
    assert payload["corrected_mask"]["access"] == "authenticated_route"
    assert payload["free_margin_length_mm"] > analyzed.capture.free_margin_length_mm
    media = client.get(payload["corrected_mask"]["url"])
    assert media.status_code == 200


def test_analyze_without_calibration_is_conflict(client):
    image = np.full((60, 60, 3), 80, np.uint8)
    response = client.post(
        "/api/v1/captures/analyze",
        data={"finger_code": "L_THUMB", "force_save": "false"},
        files={"image": ("nail.jpg", encode_jpeg(image), "image/jpeg")},
    )
    assert response.status_code == 409


def test_opencv_entrypoint_contract():
    """The headless 5.x build must keep every call the pipeline uses."""
    info = cv2.getBuildInformation()
    assert "GUI" in info
    assert "GUI:                           NONE" in info or "GUI: NONE" in info
    for name in (
        "Laplacian",
        "grabCut",
        "findTransformECC",
        "getGaborKernel",
        "SIFT_create",
        "ORB_create",
        "findHomography",
        "estimateAffine2D",
        "warpPerspective",
    ):
        assert hasattr(cv2, name), name
    kernel = cv2.getGaborKernel((21, 21), 3.0, 0.0, 8, 0.5, 0, ktype=cv2.CV_32F)
    assert kernel.shape == (21, 21)


def test_tune_script_writes_metrics_for_a_folder(tmp_path: Path):
    from scripts.tune_thresholds import measure_file

    scene = draw_calibration_scene()
    photo = tmp_path / "nail.png"
    cv2.imwrite(str(photo), scene.image)
    settings = _settings()
    row = measure_file(photo, settings, "R_INDEX", None)
    assert row["filename"] == "nail.png"
    assert row["hand_detected"] in {False, True}
    assert row["laplacian_var"] != ""
    target = tmp_path / "out.csv"
    # Exercise the CSV writer the same way the CLI does.
    with target.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)
    assert "laplacian_var" in target.read_text()


def _textured_baseline() -> np.ndarray:
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 255, size=(300, 300), dtype=np.uint8)
    color = cv2.cvtColor(noise, cv2.COLOR_GRAY2BGR)
    color = cv2.GaussianBlur(color, (3, 3), 0)
    cv2.circle(color, (150, 150), 36, (180, 140, 200), thickness=-1)
    return color


def _corner_error(actual: np.ndarray, expected: np.ndarray, width: int, height: int) -> float:
    corners = np.array(
        [[40, 40], [width - 40, 40], [width - 40, height - 40], [40, height - 40]],
        dtype=np.float64,
    ).reshape(-1, 1, 2)
    got = cv2.perspectiveTransform(corners, actual.astype(np.float64)).reshape(-1, 2)
    want = cv2.perspectiveTransform(corners, expected.astype(np.float64)).reshape(-1, 2)
    return float(np.mean(np.linalg.norm(got - want, axis=1)))


def db_mask_with_longer_free_margin(client, mask_key: str) -> np.ndarray:
    payload = client.app.state.storage.get_bytes(mask_key)
    from app.services.imaging import decode_mask_png

    mask = decode_mask_png(payload)
    # Extend class 3 upward into the nail bed so the free margin gets longer.
    bed = np.where(mask == 2)
    if bed[0].size == 0:
        raise AssertionError("segmented mask has no nail bed")
    cut = int(np.percentile(bed[0], 70))
    grown = mask.copy()
    grown[(grown == 2) & (np.indices(grown.shape)[0] >= cut)] = 3
    return grown
