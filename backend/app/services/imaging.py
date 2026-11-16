"""Decode uploads and encode the rasters we store."""

from __future__ import annotations

import cv2
import numpy as np

MAX_UPLOAD_BYTES = 20_000_000


class ImageDecodeError(ValueError):
    """The upload is not a JPEG, PNG, or WebP we can read."""


def decode_bgr(payload: bytes, *, max_edge: int) -> np.ndarray:
    if not payload:
        raise ImageDecodeError("image is empty")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ImageDecodeError("image is larger than 20 MB")
    array = np.frombuffer(payload, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise ImageDecodeError("image must be JPEG, PNG, or WebP")
    return limit_edge(image, max_edge)


def limit_edge(image: np.ndarray, max_edge: int) -> np.ndarray:
    height, width = image.shape[:2]
    long_edge = max(height, width)
    if long_edge <= max_edge:
        return image
    scale = max_edge / float(long_edge)
    return cv2.resize(image, (int(round(width * scale)), int(round(height * scale))), interpolation=cv2.INTER_AREA)


def encode_jpeg(image: np.ndarray, quality: int = 92) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise ImageDecodeError("could not encode JPEG")
    return buffer.tobytes()


def encode_mask_png(mask: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", mask.astype(np.uint8))
    if not ok:
        raise ImageDecodeError("could not encode mask")
    return buffer.tobytes()


def decode_mask_png(payload: bytes) -> np.ndarray:
    array = np.frombuffer(payload, dtype=np.uint8)
    mask = cv2.imdecode(array, cv2.IMREAD_UNCHANGED)
    if mask is None:
        raise ImageDecodeError("mask must be a PNG")
    if mask.ndim == 3:
        if mask.shape[2] == 4:
            mask = mask[:, :, :3]
        if not np.array_equal(mask[:, :, 0], mask[:, :, 1]) or not np.array_equal(mask[:, :, 0], mask[:, :, 2]):
            raise ImageDecodeError("mask PNG must be a single index channel")
        mask = mask[:, :, 0]
    return mask.astype(np.uint8)
