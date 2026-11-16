"""Run the vision pipeline over a folder of photos and write every metric.

The thresholds themselves stay in the environment. This script does not change
them. Use the CSV on a set of real nails (the owner plans to supply 10-20)
to decide whether the defaults should move.

Example, from the backend directory:

    python scripts/tune_thresholds.py ./sample_nails --csv metrics.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings  # noqa: E402
from app.services.biometric_metrics import measure_nail  # noqa: E402
from app.services.geometry import MeasurementError, nail_pixels  # noqa: E402
from app.services.hand_recognizer import HandModelUnavailable, recognize_finger  # noqa: E402
from app.services.imaging import ImageDecodeError, decode_bgr  # noqa: E402
from app.services.nail_segmenter import segment_nail  # noqa: E402
from app.services.quality_gate import evaluate_quality  # noqa: E402

COLUMNS = [
    "filename",
    "error",
    "hand_detected",
    "handedness",
    "tilt_deg",
    "laplacian_var",
    "glare_ratio",
    "nail_pixels",
    "ridge_gabor_score",
    "transverse_gabor_score",
    "nail_bed_length",
    "free_margin_length",
    "length_unit",
    "reasons",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Print and save NailTrack quality and measurement metrics.")
    parser.add_argument("folder", type=Path, help="Directory of JPEG, PNG, or WebP photos")
    parser.add_argument("--csv", type=Path, default=Path("threshold_metrics.csv"))
    parser.add_argument("--finger", default="L_INDEX", help="Finger code passed to the hand landmarker")
    parser.add_argument("--mm-per-px", type=float, default=None, help="If set, also print lengths in millimetres")
    args = parser.parse_args()
    settings = get_settings()
    paths = sorted(
        path
        for path in args.folder.iterdir()
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and path.is_file()
    )
    if not paths:
        print(f"No images in {args.folder}", file=sys.stderr)
        return 1
    rows = [measure_file(path, settings, args.finger, args.mm_per_px) for path in paths]
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    _print_table(rows)
    print(f"\nWrote {len(rows)} rows to {args.csv}")
    return 0


def measure_file(path: Path, settings, finger_code: str, mm_per_px: float | None) -> dict[str, object]:
    row: dict[str, object] = {column: "" for column in COLUMNS}
    row["filename"] = path.name
    try:
        image = decode_bgr(path.read_bytes(), max_edge=settings.max_image_edge_px)
    except (OSError, ImageDecodeError) as exc:
        row["error"] = str(exc)
        return row
    try:
        observation = recognize_finger(image, finger_code, settings.resolved_hand_model_path)
    except HandModelUnavailable as exc:
        row["error"] = str(exc)
        return row
    mask = None
    if observation.pose is not None:
        mask = segment_nail(image, observation.pose)
    quality = evaluate_quality(image, mask, observation.pose, settings)
    row["hand_detected"] = quality.metrics.hand_detected
    row["handedness"] = quality.metrics.handedness or ""
    row["tilt_deg"] = "" if quality.metrics.tilt_deg is None else round(quality.metrics.tilt_deg, 3)
    row["laplacian_var"] = round(quality.metrics.laplacian_var, 3)
    row["glare_ratio"] = round(quality.metrics.glare_ratio, 5)
    row["reasons"] = " ".join(quality.metrics.reasons)
    nail_count = 0 if mask is None else int(len(nail_pixels(mask)[0]))
    row["nail_pixels"] = nail_count
    if mask is not None and nail_count >= 30:
        try:
            scale = mm_per_px if mm_per_px and mm_per_px > 0 else 1.0
            measured = measure_nail(
                image,
                mask,
                mm_per_px=scale,
                von_kries_gains=(1.0, 1.0, 1.0),
                gabor_energy_floor=settings.gabor_energy_floor,
            )
            row["ridge_gabor_score"] = round(measured.ridge_gabor_score, 4)
            row["transverse_gabor_score"] = round(measured.transverse_gabor_score, 4)
            row["nail_bed_length"] = round(measured.nail_bed_length_mm, 3)
            row["free_margin_length"] = round(measured.free_margin_length_mm, 3)
            row["length_unit"] = "mm" if mm_per_px else "px"
        except MeasurementError as exc:
            row["error"] = str(exc)
    return row


def _print_table(rows: list[dict[str, object]]) -> None:
    shown = [
        "filename",
        "hand_detected",
        "tilt_deg",
        "laplacian_var",
        "glare_ratio",
        "ridge_gabor_score",
        "reasons",
    ]
    widths = {column: max(len(column), *(len(str(row.get(column, ""))) for row in rows)) for column in shown}
    print(" ".join(column.ljust(widths[column]) for column in shown))
    for row in rows:
        print(" ".join(str(row.get(column, "")).ljust(widths[column]) for column in shown))


if __name__ == "__main__":
    raise SystemExit(main())
