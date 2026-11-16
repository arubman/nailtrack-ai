"""Register a capture onto the Day-0 baseline.

Keypoints are ORB/SIFT features inside the invariant mask (lateral folds and
the DIP crease), plus the two fold corners. The distal nail is not a feature
source. RANSAC homography is refined with ECC. When ECC fails to converge,
which is common on low-texture skin, the result falls back to an affine fit
and reports that method plus a 0..1 alignment quality score.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.constants import HOMOGRAPHY_RANSAC_REPROJ_THRESHOLD_PX
from app.services.geometry import NAIL_CLASSES, FingerPose


class AlignmentError(Exception):
    """Neither ECC, affine, nor RANSAC produced a usable transform."""


@dataclass
class AlignmentResult:
    matrix: np.ndarray
    method: str
    alignment_quality: float
    ransac_inlier_rms: float | None
    inlier_ratio: float | None
    ecc_correlation: float | None


def build_invariant_mask(class_mask: np.ndarray, pose: FingerPose | None = None) -> np.ndarray:
    """Skin at the lateral folds and DIP crease. The nail plate and tip are excluded."""
    nail = np.isin(class_mask, NAIL_CLASSES).astype(np.uint8)
    if int(nail.sum()) < 20:
        return np.zeros(class_mask.shape[:2], np.uint8)
    ys, xs = np.where(nail > 0)
    if pose is not None:
        try:
            origin, axis, length = pose.axis()
        except Exception:
            origin = np.array([xs.mean(), ys.mean()])
            axis = np.array([0.0, 1.0])
            length = float(max(ys.max() - ys.min(), 10))
    else:
        origin = np.array([float(xs.mean()), float(ys.mean())])
        axis = np.array([0.0, 1.0])
        length = float(max(int(ys.max() - ys.min()), 10))
    kernel_size = max(3, int(round(0.08 * length)) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    ring = (cv2.dilate(nail, kernel) > 0) & (nail == 0)
    points = np.stack([xs, ys], axis=1).astype(np.float64)
    projection = (points - origin) @ axis
    distal_cut = float(np.percentile(projection, 80))
    proximal_cut = float(np.percentile(projection, 5))
    height, width = class_mask.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width]
    grid = np.stack([xx, yy], axis=-1).astype(np.float64)
    grid_proj = (grid - origin) @ axis
    # Keep the proximal skin ring and a band just before the cuticle. Drop the tip.
    dip_band = (grid_proj > proximal_cut - 0.25 * length) & (grid_proj < proximal_cut)
    invariant = (ring & (grid_proj < distal_cut)) | (dip_band & (nail == 0) & (cv2.dilate(nail, kernel) > 0))
    return (invariant.astype(np.uint8)) * 255


def align_to_baseline(
    baseline_bgr: np.ndarray,
    capture_bgr: np.ndarray,
    *,
    baseline_invariant: np.ndarray,
    capture_invariant: np.ndarray,
    baseline_folds: tuple[tuple[float, float], tuple[float, float]] | None = None,
    capture_folds: tuple[tuple[float, float], tuple[float, float]] | None = None,
) -> AlignmentResult:
    if baseline_bgr.shape[:2] != capture_bgr.shape[:2]:
        # Feature matching is in each image's own pixels. ECC needs a shared canvas,
        # so the RANSAC homography is estimated first and ECC refines after warping.
        pass
    source, destination = _correspondences(
        capture_bgr,
        baseline_bgr,
        capture_invariant,
        baseline_invariant,
        capture_folds,
        baseline_folds,
    )
    homography, inlier_ratio, rms = _ransac_homography(source, destination)
    affine, affine_ratio, affine_rms = _affine(source, destination)
    try:
        ecc_matrix, correlation = _ecc(baseline_bgr, capture_bgr, homography)
        # OpenCV sometimes returns instead of throwing when the correlation is
        # collapsing. That is the same failure as "iterations do not converge".
        if not np.isfinite(correlation) or correlation < 0.45:
            raise cv2.error("ECC correlation did not converge")
    except cv2.error:
        if affine is None and homography is None:
            raise AlignmentError("low-texture skin did not yield an alignment") from None
        if affine is not None:
            return AlignmentResult(
                matrix=affine,
                method="affine_fallback",
                alignment_quality=_quality(affine_ratio),
                ransac_inlier_rms=affine_rms,
                inlier_ratio=affine_ratio,
                ecc_correlation=None,
            )
        assert homography is not None
        return AlignmentResult(
            matrix=homography,
            method="homography_ransac",
            alignment_quality=_quality(None if inlier_ratio is None else inlier_ratio * 0.5),
            ransac_inlier_rms=rms,
            inlier_ratio=inlier_ratio,
            ecc_correlation=None,
        )
    return AlignmentResult(
        matrix=ecc_matrix,
        method="homography_ecc",
        alignment_quality=_quality(correlation),
        ransac_inlier_rms=rms,
        inlier_ratio=inlier_ratio,
        ecc_correlation=correlation,
    )


def warp_bgr(image: np.ndarray, matrix: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    width, height = size
    return cv2.warpPerspective(image, matrix.astype(np.float64), (width, height))


def warp_mask(mask: np.ndarray, matrix: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    width, height = size
    return cv2.warpPerspective(
        mask,
        matrix.astype(np.float64),
        (width, height),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )


def _correspondences(
    capture_bgr: np.ndarray,
    baseline_bgr: np.ndarray,
    capture_invariant: np.ndarray,
    baseline_invariant: np.ndarray,
    capture_folds: tuple[tuple[float, float], tuple[float, float]] | None,
    baseline_folds: tuple[tuple[float, float], tuple[float, float]] | None,
) -> tuple[np.ndarray, np.ndarray]:
    detector_name, capture_kp, capture_desc, baseline_kp, baseline_desc = _detect_pair(
        capture_bgr, baseline_bgr, capture_invariant, baseline_invariant
    )
    source_points: list[tuple[float, float]] = []
    dest_points: list[tuple[float, float]] = []
    if capture_desc is not None and baseline_desc is not None and len(capture_kp) >= 2 and len(baseline_kp) >= 2:
        norm = cv2.NORM_HAMMING if detector_name == "orb" else cv2.NORM_L2
        matcher = cv2.BFMatcher(norm)
        pairs = matcher.knnMatch(capture_desc, baseline_desc, k=2)
        for pair in pairs:
            if len(pair) < 2:
                continue
            best, second = pair
            if best.distance < 0.75 * second.distance:
                source_points.append(capture_kp[best.queryIdx].pt)
                dest_points.append(baseline_kp[best.trainIdx].pt)
    if capture_folds and baseline_folds:
        # Repeat the fold corners so the invariant anatomy anchors RANSAC.
        for _repeat in range(6):
            source_points.extend(capture_folds)
            dest_points.extend(baseline_folds)
    if len(source_points) < 3:
        return np.zeros((0, 1, 2), np.float32), np.zeros((0, 1, 2), np.float32)
    return (
        np.array(source_points, dtype=np.float32).reshape(-1, 1, 2),
        np.array(dest_points, dtype=np.float32).reshape(-1, 1, 2),
    )


def _detect_pair(capture_bgr, baseline_bgr, capture_mask, baseline_mask):
    capture_gray = cv2.cvtColor(capture_bgr, cv2.COLOR_BGR2GRAY)
    baseline_gray = cv2.cvtColor(baseline_bgr, cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create(nfeatures=1200)
    cap_kp, cap_desc = sift.detectAndCompute(capture_gray, _mask8(capture_mask))
    base_kp, base_desc = sift.detectAndCompute(baseline_gray, _mask8(baseline_mask))
    if _usable(cap_kp, cap_desc) and _usable(base_kp, base_desc):
        return "sift", cap_kp, cap_desc, base_kp, base_desc
    orb = cv2.ORB_create(nfeatures=1500)
    cap_kp, cap_desc = orb.detectAndCompute(capture_gray, _mask8(capture_mask))
    base_kp, base_desc = orb.detectAndCompute(baseline_gray, _mask8(baseline_mask))
    return "orb", cap_kp, cap_desc, base_kp, base_desc


def _usable(keypoints, descriptors) -> bool:
    return bool(keypoints) and descriptors is not None and len(keypoints) >= 4


def _mask8(mask: np.ndarray) -> np.ndarray:
    if mask.dtype != np.uint8:
        mask = mask.astype(np.uint8)
    if mask.max() == 1:
        return mask * 255
    return mask


def _ransac_homography(source: np.ndarray, destination: np.ndarray):
    if len(source) < 4:
        return None, None, None
    matrix, inliers = cv2.findHomography(
        source,
        destination,
        cv2.RANSAC,
        HOMOGRAPHY_RANSAC_REPROJ_THRESHOLD_PX,
    )
    if matrix is None:
        return None, None, None
    ratio, rms = _inlier_stats(source, destination, matrix, inliers)
    return matrix.astype(np.float64), ratio, rms


def _affine(source: np.ndarray, destination: np.ndarray):
    if len(source) < 3:
        return None, None, None
    matrix, inliers = cv2.estimateAffine2D(
        source,
        destination,
        method=cv2.RANSAC,
        ransacReprojThreshold=HOMOGRAPHY_RANSAC_REPROJ_THRESHOLD_PX,
    )
    if matrix is None:
        return None, None, None
    homography = np.eye(3, dtype=np.float64)
    homography[:2, :] = matrix
    ratio, rms = _inlier_stats(source, destination, homography, inliers)
    return homography, ratio, rms


def _ecc(baseline_bgr: np.ndarray, capture_bgr: np.ndarray, initial: np.ndarray | None):
    # findTransformECC returns a warp that maps template coordinates into the
    # moving image (the WARP_INVERSE_MAP convention). Our stored matrix is the
    # opposite: warpPerspective(capture, matrix) lands on the baseline.
    composed_initial = None
    if baseline_bgr.shape[:2] != capture_bgr.shape[:2]:
        if initial is None:
            raise cv2.error("ECC needs a shared canvas")
        capture_bgr = warp_bgr(capture_bgr, initial, (baseline_bgr.shape[1], baseline_bgr.shape[0]))
        seed = np.eye(3, dtype=np.float32)
        composed_initial = initial
    elif initial is None:
        seed = np.eye(3, dtype=np.float32)
    else:
        seed = np.linalg.inv(initial).astype(np.float32)
    template = cv2.cvtColor(baseline_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    moving = cv2.cvtColor(capture_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 1e-4)
    correlation, warp = cv2.findTransformECC(template, moving, seed, cv2.MOTION_HOMOGRAPHY, criteria)
    refined = np.linalg.inv(_normalize_homography(warp))
    if composed_initial is not None:
        refined = refined @ composed_initial.astype(np.float64)
    return refined, float(correlation)


def _normalize_homography(matrix: np.ndarray) -> np.ndarray:
    homography = matrix.astype(np.float64)
    scale = homography[2, 2]
    if abs(scale) < 1e-12:
        return homography
    return homography / scale


def _inlier_stats(source, destination, matrix, inliers) -> tuple[float | None, float | None]:
    if inliers is None:
        return None, None
    mask = inliers.reshape(-1).astype(bool)
    if int(mask.sum()) == 0:
        return 0.0, None
    projected = cv2.perspectiveTransform(source[mask], matrix.astype(np.float64))
    error = np.linalg.norm(projected.reshape(-1, 2) - destination[mask].reshape(-1, 2), axis=1)
    ratio = float(mask.mean())
    rms = float(np.sqrt(np.mean(error**2)))
    return ratio, rms


def _quality(value: float | None) -> float:
    if value is None or not np.isfinite(value):
        return 0.0
    return float(np.clip(value, 0.0, 1.0))
