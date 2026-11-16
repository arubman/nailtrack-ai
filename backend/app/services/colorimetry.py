"""sRGB, Von Kries, and CIELAB. Adaptation uses the stored card reference, not skin."""

from __future__ import annotations

import numpy as np

# Linear sRGB (D65) to CIE XYZ.
_SRGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float64,
)
_D65_XYZ = np.array([0.95047, 1.0, 1.08883], dtype=np.float64)


def srgb_uint8_to_linear(rgb_uint8: np.ndarray) -> np.ndarray:
    encoded = rgb_uint8.astype(np.float64) / 255.0
    linear = np.empty_like(encoded)
    small = encoded <= 0.04045
    linear[small] = encoded[small] / 12.92
    linear[~small] = ((encoded[~small] + 0.055) / 1.055) ** 2.4
    return linear


def linear_to_srgb_uint8(linear: np.ndarray) -> np.ndarray:
    clipped = np.clip(linear, 0.0, 1.0)
    encoded = np.empty_like(clipped)
    small = clipped <= 0.0031308
    encoded[small] = clipped[small] * 12.92
    encoded[~small] = 1.055 * np.power(clipped[~small], 1.0 / 2.4) - 0.055
    return np.clip(np.round(encoded * 255.0), 0, 255).astype(np.uint8)


def bgr_mean_to_linear_rgb(bgr_pixels: np.ndarray) -> tuple[float, float, float]:
    if bgr_pixels.size == 0:
        raise ValueError("white reference patch is empty")
    mean_encoded = bgr_pixels.reshape(-1, 3).mean(axis=0)[::-1] / 255.0
    channels = []
    for channel in mean_encoded:
        if channel <= 0.04045:
            channels.append(float(channel / 12.92))
        else:
            channels.append(float(((channel + 0.055) / 1.055) ** 2.4))
    return (channels[0], channels[1], channels[2])


def xyz_to_lab(xyz: np.ndarray) -> np.ndarray:
    scaled = xyz / _D65_XYZ
    delta = 6.0 / 29.0
    branch = scaled > delta**3
    f_value = np.empty_like(scaled)
    f_value[branch] = np.cbrt(scaled[branch])
    f_value[~branch] = scaled[~branch] / (3 * delta**2) + 4.0 / 29.0
    lightness = 116.0 * f_value[..., 1] - 16.0
    a_star = 500.0 * (f_value[..., 0] - f_value[..., 1])
    b_star = 200.0 * (f_value[..., 1] - f_value[..., 2])
    return np.stack([lightness, a_star, b_star], axis=-1)


def adapt_bgr(image_bgr: np.ndarray, gains: tuple[float, float, float]) -> np.ndarray:
    """Apply Von Kries gains in linear sRGB and return BGR uint8."""
    rgb = image_bgr[:, :, ::-1]
    linear = srgb_uint8_to_linear(rgb)
    adapted = np.clip(linear * np.array(gains, dtype=np.float64).reshape(1, 1, 3), 0.0, 1.0)
    out_rgb = linear_to_srgb_uint8(adapted)
    return out_rgb[:, :, ::-1]


def mean_lab(
    image_bgr: np.ndarray, selection: np.ndarray, gains: tuple[float, float, float]
) -> tuple[float, float, float]:
    pixels_bgr = image_bgr[selection]
    if pixels_bgr.size == 0:
        raise ValueError("no pixels to average")
    rgb = pixels_bgr[:, ::-1]
    linear = srgb_uint8_to_linear(rgb)
    adapted = linear * np.array(gains, dtype=np.float64).reshape(1, 3)
    lab = xyz_to_lab(adapted @ _SRGB_TO_XYZ.T)
    mean = lab.mean(axis=0)
    return (float(mean[0]), float(mean[1]), float(mean[2]))
