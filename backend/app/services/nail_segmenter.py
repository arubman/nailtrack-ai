"""4-class nail mask from color clustering and GrabCut.

Classes: 0 background (including skin), 1 lunula, 2 nail bed, 3 free margin.
Inside a band distal to the DIP, Lab k-means separates skin from the plate.
Clusters that sit on the finger axis are the plate; they are ordered from the
cuticle toward the tip. GrabCut then snaps that plate against the skin, and
pixels it rejects are dropped.
"""

from __future__ import annotations

import cv2
import numpy as np

from app.services.geometry import (
    CLASS_FREE_MARGIN,
    CLASS_LUNULA,
    CLASS_NAIL_BED,
    FingerPose,
    finger_band,
)


def segment_nail(image_bgr: np.ndarray, pose: FingerPose) -> np.ndarray:
    height, width = image_bgr.shape[:2]
    label = np.zeros((height, width), np.uint8)
    try:
        band = finger_band(pose, height, width)
    except Exception:
        return label
    if int((band > 0).sum()) < 80:
        return label
    painted = _color_classes(image_bgr, band, pose)
    if int(np.isin(painted, (CLASS_LUNULA, CLASS_NAIL_BED, CLASS_FREE_MARGIN)).sum()) < 30:
        return label
    prior = np.where(np.isin(painted, (CLASS_LUNULA, CLASS_NAIL_BED, CLASS_FREE_MARGIN)), 255, 0).astype(np.uint8)
    plate = _grabcut_plate(image_bgr, band, prior)
    if int(plate.sum()) < 0.5 * int((prior > 0).sum()):
        plate = prior
    label[plate > 0] = painted[plate > 0]
    return label


def _color_classes(image_bgr: np.ndarray, band: np.ndarray, pose: FingerPose) -> np.ndarray:
    label = np.zeros(band.shape, np.uint8)
    ys, xs = np.where(band > 0)
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    samples = lab[ys, xs].astype(np.float32)
    cluster_count = 5 if samples.shape[0] >= 400 else 3
    labels, centers = _kmeans(samples, cluster_count)
    dip, axis, _length = pose.axis()
    points = np.stack([xs, ys], axis=1).astype(np.float64)
    projection = (points - dip) @ axis
    normal = np.array([-axis[1], axis[0]], dtype=np.float64)
    lateral = np.abs((points - dip) @ normal)
    stats = []
    for cluster in range(cluster_count):
        selected = labels == cluster
        if not np.any(selected):
            continue
        stats.append(
            {
                "id": cluster,
                "proj": float(projection[selected].mean()),
                "lat": float(lateral[selected].mean()),
                "lightness": float(centers[cluster][0]),
            }
        )
    if not stats:
        return label
    tightest = min(item["lat"] for item in stats)
    nail_stats = [
        item
        for item in stats
        if item["lightness"] >= 50.0 and item["lat"] <= max(tightest * 1.8, tightest + 8.0)
    ]
    if not nail_stats:
        return label
    nail_stats.sort(key=lambda item: item["proj"])
    class_for: dict[int, int] = {}
    if len(nail_stats) == 1:
        class_for[nail_stats[0]["id"]] = CLASS_NAIL_BED
    elif len(nail_stats) == 2:
        class_for[nail_stats[0]["id"]] = CLASS_NAIL_BED
        class_for[nail_stats[1]["id"]] = CLASS_FREE_MARGIN
    else:
        class_for[nail_stats[0]["id"]] = CLASS_LUNULA
        class_for[nail_stats[-1]["id"]] = CLASS_FREE_MARGIN
        for item in nail_stats[1:-1]:
            class_for[item["id"]] = CLASS_NAIL_BED
    for index, (x_coord, y_coord) in enumerate(zip(xs, ys)):
        label[y_coord, x_coord] = class_for.get(int(labels[index]), 0)
    return label


def _grabcut_plate(image_bgr: np.ndarray, band: np.ndarray, prior: np.ndarray) -> np.ndarray:
    if int((prior > 0).sum()) < 40:
        return prior
    grab = np.full(image_bgr.shape[:2], cv2.GC_BGD, np.uint8)
    grab[band > 0] = cv2.GC_PR_BGD
    grab[prior > 0] = cv2.GC_PR_FGD
    core = cv2.erode(prior, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    if int((core > 0).sum()) > 20:
        grab[core > 0] = cv2.GC_FGD
    background_model = np.zeros((1, 65), np.float64)
    foreground_model = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(
            image_bgr,
            grab,
            None,
            background_model,
            foreground_model,
            3,
            cv2.GC_INIT_WITH_MASK,
        )
    except cv2.error:
        return prior
    return np.where((grab == cv2.GC_FGD) | (grab == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)


def _kmeans(samples: np.ndarray, clusters: int) -> tuple[np.ndarray, np.ndarray]:
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_COUNT, 40, 0.2)
    _compactness, labels, centers = cv2.kmeans(
        samples,
        clusters,
        None,
        criteria,
        8,
        cv2.KMEANS_PP_CENTERS,
    )
    return labels.reshape(-1), centers
