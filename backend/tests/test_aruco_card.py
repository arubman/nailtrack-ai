"""ArUco DICT_4X4_50 card detection, with fallback to the generic quad."""

from __future__ import annotations

import cv2
import numpy as np

from app.constants import ID1_CARD_HEIGHT_MM, ID1_CARD_WIDTH_MM
from app.services.aruco_card import MARKER_SIZE_MM, detect_aruco_card
from app.services.card_detector import detect_card
from tests.synthetic import draw_calibration_scene

_MARKER_ORIGIN_MM = {
    0: (4.5, 41.48),
    1: (73.1, 41.48),
    2: (73.1, 4.5),
    3: (4.5, 4.5),
}
_WHITE = (16.0, 18.4, 32.0, 16.5)
_GRAY = (51.0, 18.4, 16.5, 16.5)


def _render_card(px_per_mm: float = 10.0) -> np.ndarray:
    margin = 40
    width = int(round(ID1_CARD_WIDTH_MM * px_per_mm)) + margin * 2
    height = int(round(ID1_CARD_HEIGHT_MM * px_per_mm)) + margin * 2
    image = np.full((height, width, 3), (30, 30, 30), np.uint8)
    origin_x = margin
    origin_y = margin

    def to_px(x_mm: float, y_mm: float) -> tuple[int, int]:
        # Card y is up; image y is down.
        return (
            int(round(origin_x + x_mm * px_per_mm)),
            int(round(origin_y + (ID1_CARD_HEIGHT_MM - y_mm) * px_per_mm)),
        )

    top_left = to_px(0, ID1_CARD_HEIGHT_MM)
    bottom_right = to_px(ID1_CARD_WIDTH_MM, 0)
    cv2.rectangle(image, top_left, bottom_right, (255, 255, 255), thickness=-1)
    white = _WHITE
    gray = _GRAY
    cv2.rectangle(image, to_px(white[0], white[1] + white[3]), to_px(white[0] + white[2], white[1]), (255, 255, 255), -1)
    cv2.rectangle(image, to_px(gray[0], gray[1] + gray[3]), to_px(gray[0] + gray[2], gray[1]), (118, 118, 118), -1)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    side = int(round(MARKER_SIZE_MM * px_per_mm))
    for marker_id, (x_mm, y_mm) in _MARKER_ORIGIN_MM.items():
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, side, borderBits=1)
        x_px, y_top = to_px(x_mm, y_mm + MARKER_SIZE_MM)
        image[y_top : y_top + side, x_px : x_px + side] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    return image


def test_aruco_card_recovers_scale_and_both_patches():
    px_per_mm = 10.0
    image = _render_card(px_per_mm)
    detected = detect_aruco_card(image)
    assert detected is not None
    assert detected.method == "aruco"
    assert detected.mm_per_px == pytest_approx(1.0 / px_per_mm, abs=0.002)
    assert detected.reference_white.calibration_method == "aruco"
    assert detected.reference_white.source == "id1_card_white"
    assert detected.reference_white.target == "D65"
    assert detected.reference_white.mean_rgb[0] == pytest_approx(1.0, abs=0.05)
    adapted = tuple(
        channel * gain
        for channel, gain in zip(detected.reference_white.mean_rgb, detected.reference_white.von_kries_gains, strict=True)
    )
    assert adapted == pytest_approx((1.0, 1.0, 1.0), abs=1e-5)
    assert detected.reference_white.gray_mean_rgb is not None
    assert detected.reference_white.gray_mean_rgb[0] == pytest_approx(0.18, abs=0.03)


def test_missing_markers_fall_back_to_the_generic_quad():
    scene = draw_calibration_scene()
    assert detect_aruco_card(scene.image) is None
    generic = detect_card(scene.image, reference_type="ID1_CARD", long_edge_mm=ID1_CARD_WIDTH_MM)
    assert generic is not None
    assert generic.method == "quad"


def pytest_approx(value, **kwargs):
    import pytest

    return pytest.approx(value, **kwargs)
