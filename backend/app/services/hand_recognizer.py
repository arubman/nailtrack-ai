"""MediaPipe HandLandmarker. The server re-checks tilt itself.

Image landmarks are converted to pixels. World landmarks (meters) supply the
3D DIP and tip used by the tilt formula. If the model finds no hand, the
caller receives RETAKE_NO_HAND instead of a silent pass.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

os.environ.setdefault("GLOG_minloglevel", "2")

from app.services.geometry import FINGER_TIP_AND_DIP, FingerPose, finger_name

_LOCK = threading.Lock()
_LANDMARKER = None
_LOADED_PATH: str | None = None


class HandModelUnavailable(RuntimeError):
    """The HandLandmarker weights are missing or failed to load."""


@dataclass
class HandObservation:
    pose: FingerPose | None
    hand_count: int


def recognize_finger(image_bgr: np.ndarray, finger_code: str, model_path: Path) -> HandObservation:
    landmarker = _landmarker(model_path)
    rgb = np.ascontiguousarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
    import mediapipe as mp

    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    with _LOCK:
        result = landmarker.detect(mp_image)
    if not result.hand_landmarks:
        return HandObservation(pose=None, hand_count=0)
    height, width = image_bgr.shape[:2]
    tip_index, dip_index = FINGER_TIP_AND_DIP[finger_name(finger_code)]
    best_pose: FingerPose | None = None
    best_reach = -1.0
    for hand_index, landmarks in enumerate(result.hand_landmarks):
        if len(landmarks) < 21:
            continue
        tip = landmarks[tip_index]
        dip = landmarks[dip_index]
        wrist = landmarks[0]
        reach = (tip.x - wrist.x) ** 2 + (tip.y - wrist.y) ** 2
        world = None
        if result.hand_world_landmarks and hand_index < len(result.hand_world_landmarks):
            world_landmarks = result.hand_world_landmarks[hand_index]
            if len(world_landmarks) >= 21:
                world = world_landmarks
        handedness, score = _handedness(result, hand_index)
        pose = FingerPose(
            dip_xy=(dip.x * width, dip.y * height),
            tip_xy=(tip.x * width, tip.y * height),
            dip_xyz=_xyz(world[dip_index] if world is not None else dip),
            tip_xyz=_xyz(world[tip_index] if world is not None else tip),
            handedness=handedness,
            handedness_score=score,
        )
        if reach > best_reach:
            best_reach = reach
            best_pose = pose
    return HandObservation(pose=best_pose, hand_count=len(result.hand_landmarks))


def _xyz(landmark: object) -> tuple[float, float, float]:
    return (float(landmark.x), float(landmark.y), float(landmark.z))


def _handedness(result: object, hand_index: int) -> tuple[str | None, float | None]:
    groups = getattr(result, "handedness", None) or []
    if hand_index >= len(groups) or not groups[hand_index]:
        return None, None
    category = groups[hand_index][0]
    name = getattr(category, "category_name", None) or getattr(category, "display_name", None)
    score = getattr(category, "score", None)
    return (str(name) if name else None, float(score) if score is not None else None)


def _landmarker(model_path: Path):
    global _LANDMARKER, _LOADED_PATH
    path = str(model_path)
    with _LOCK:
        if _LANDMARKER is not None and _LOADED_PATH == path:
            return _LANDMARKER
        if not model_path.is_file():
            raise HandModelUnavailable(
                f"MediaPipe hand model was not found at {model_path}. "
                "Expected backend/models/hand_landmarker.task."
            )
        import mediapipe as mp

        try:
            landmarker = mp.tasks.vision.HandLandmarker.create_from_model_path(path)
        except (OSError, RuntimeError, ValueError) as exc:
            raise HandModelUnavailable("MediaPipe HandLandmarker failed to load") from exc
        _LANDMARKER = landmarker
        _LOADED_PATH = path
        return landmarker
