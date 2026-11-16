"""Von Kries gains from a calibration-card white or gray patch.

Chromatic adaptation uses the white area of the ID-1 card, or another
gray/white reference photographed with the hand. It does not sample
periungual skin. Phase 2 finds the patch and calls these functions; Phase 1
only defines the math and the stored shape (see schemas.ReferenceWhite).

Gains are applied in linear sRGB, before the CIELAB conversion:

    adapted_rgb = (r * gain_r, g * gain_g, b * gain_b)
"""

from __future__ import annotations

_MIN_CHANNEL = 1e-6


def _check_linear_rgb(mean_linear_rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    if len(mean_linear_rgb) != 3:
        raise ValueError("reference patch must have three linear RGB channels")
    channels: list[float] = []
    for channel in mean_linear_rgb:
        value = float(channel)
        if value <= _MIN_CHANNEL or value > 1.0:
            raise ValueError("reference patch channels must be in (0, 1] linear sRGB")
        channels.append(value)
    return (channels[0], channels[1], channels[2])


def von_kries_gains_white_to_d65(
    mean_linear_rgb: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Gains that map a white reference patch onto D65 (1, 1, 1) in linear sRGB.

    Use this for the white area of an ID-1 card or another white reference.
    """
    red, green, blue = _check_linear_rgb(mean_linear_rgb)
    return (1.0 / red, 1.0 / green, 1.0 / blue)


def von_kries_gains_neutral_gray(
    mean_linear_rgb: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Gains that neutralize a gray reference while preserving its mean luminance.

    A gray card is assumed neutral but is not D65 white, so it must not be
    mapped to (1, 1, 1). Each channel is scaled toward the patch mean.
    """
    red, green, blue = _check_linear_rgb(mean_linear_rgb)
    luminance = (red + green + blue) / 3.0
    return (luminance / red, luminance / green, luminance / blue)
