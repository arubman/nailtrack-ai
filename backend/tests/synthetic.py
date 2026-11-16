"""Drawn nails and calibration cards with known geometry."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.constants import ID1_CARD_HEIGHT_MM, ID1_CARD_WIDTH_MM
from app.services.geometry import FingerPose


@dataclass
class NailScene:
    image: np.ndarray
    pose: FingerPose
    nail_width_px: float
    bed_length_px: float
    free_length_px: float
    card_long_px: float
    card_origin: tuple[int, int]


def draw_calibration_scene() -> NailScene:
    """Finger on the right, ID-1 card on the left, both on a dark background.

    The nail is three solid colors stacked along a vertical axis so the
    segmenter can separate them. Pose matches the drawing exactly.
    """
    image = np.full((700, 640, 3), (28, 36, 30), np.uint8)
    card_long = 360
    card_short = int(round(card_long * ID1_CARD_HEIGHT_MM / ID1_CARD_WIDTH_MM))
    card_origin = (40, 80)
    _draw_card(image, card_origin, card_long, card_short)

    center_x = 460
    dip_y = 180
    tip_y = 560
    finger_half = 78
    nail_half = 36
    cuticle_y = 250
    lunula_end = 310
    bed_end = 460
    free_end = 530
    skin = (90, 145, 190)
    cv2.rectangle(
        image,
        (center_x - finger_half, dip_y - 40),
        (center_x + finger_half, tip_y + 20),
        skin,
        thickness=-1,
    )
    # Rounded tip so the finger is not a second calibration rectangle.
    cv2.ellipse(
        image,
        (center_x, tip_y),
        (finger_half, 28),
        0,
        0,
        360,
        skin,
        thickness=-1,
    )
    cv2.rectangle(
        image,
        (center_x - nail_half, cuticle_y),
        (center_x + nail_half, lunula_end),
        (200, 175, 210),
        thickness=-1,
    )
    cv2.rectangle(
        image,
        (center_x - nail_half, lunula_end),
        (center_x + nail_half, bed_end),
        (150, 90, 210),
        thickness=-1,
    )
    cv2.rectangle(
        image,
        (center_x - nail_half, bed_end),
        (center_x + nail_half, free_end),
        (40, 210, 230),
        thickness=-1,
    )
    pose = FingerPose(
        dip_xy=(float(center_x), float(dip_y)),
        tip_xy=(float(center_x), float(tip_y)),
        dip_xyz=(0.0, 0.0, 0.0),
        tip_xyz=(0.0, 0.02, 0.0),
        handedness="Right",
        handedness_score=0.99,
    )
    return NailScene(
        image=image,
        pose=pose,
        nail_width_px=float(2 * nail_half),
        bed_length_px=float(bed_end - cuticle_y),
        free_length_px=float(free_end - bed_end),
        card_long_px=float(card_long),
        card_origin=card_origin,
    )


def draw_striped_nail(*, longitudinal: bool) -> tuple[np.ndarray, np.ndarray]:
    """Vertical finger with a stripe pattern on the nail plate. Mask is exact."""
    image = np.full((220, 120, 3), (90, 140, 180), np.uint8)
    mask = np.zeros((220, 120), np.uint8)
    x0, x1 = 40, 80
    y0, y1 = 40, 180
    mask[y0:y1, x0:x1] = 2
    mask[30:40, x0:x1] = 1
    mask[180:200, x0:x1] = 3
    if longitudinal:
        period = 6
        for x in range(x0, x1):
            color = 40 if ((x // period) % 2 == 0) else 220
            image[30:200, x] = (color, color, color)
    else:
        period = 6
        for y in range(30, 200):
            color = 40 if ((y // period) % 2 == 0) else 220
            image[y, x0:x1] = (color, color, color)
    return image, mask


def _draw_card(image: np.ndarray, origin: tuple[int, int], long_px: int, short_px: int) -> None:
    x_coord, y_coord = origin
    cv2.rectangle(
        image,
        (x_coord, y_coord),
        (x_coord + long_px, y_coord + short_px),
        (250, 250, 250),
        thickness=-1,
    )
    cv2.rectangle(
        image,
        (x_coord, y_coord),
        (x_coord + long_px, y_coord + short_px),
        (20, 20, 20),
        thickness=4,
    )
    # A small non-white mark so the card is not a featureless blob, without
    # covering the white area the reference is sampled from.
    cv2.rectangle(
        image,
        (x_coord + 12, y_coord + 12),
        (x_coord + 36, y_coord + 36),
        (180, 40, 40),
        thickness=-1,
    )
