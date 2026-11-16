"""Product constants shared by later phases.

Quality thresholds and growth-rate defaults that operators may override live
in config.Settings. Values here are part of the measurement contract and stay
fixed so Phase 2 and Phase 3 do not invent a second set of numbers.
"""

from __future__ import annotations

import math

# ISO/IEC 7810 ID-1. Scale uses the width. The white area of the same card is
# the Von Kries reference (see app.reference_white); periungual skin is not.
ID1_CARD_WIDTH_MM = 85.60
ID1_CARD_HEIGHT_MM = 53.98

# Forecast horizons are {+7, +14, +30, +60, +90} everywhere, including the
# timeline endpoint. The slider spans -90..+90 in one-day steps.
FORECAST_HORIZONS_DAYS: tuple[int, ...] = (7, 14, 30, 60, 90)
TIMELINE_SLIDER_MIN_DAYS = -90
TIMELINE_SLIDER_MAX_DAYS = 90
TIMELINE_SLIDER_STEP_DAYS = 1

# Phase 3 growth formula:
#   delta_mm(t) = (OBSERVED_WEIGHT * v_obs + POPULATION_WEIGHT * v_pop) * alpha_tx * t
# v_obs defaults and v_pop are configurable. The weights are the spec's blend.
VELOCITY_OBSERVED_WEIGHT = 0.7
VELOCITY_POPULATION_WEIGHT = 0.3
# New interval rate vs the previous EMA. Trims (length decrease) are ignored.
VELOCITY_EMA_NEW_WEIGHT = 0.3
MIN_VELOCITY_INTERVAL_DAYS = 1.0
# Product of active user-set modifiers, clamped so stacked regimens stay bounded.
ALPHA_TX_MIN = 0.05
ALPHA_TX_MAX = 5.0
FREE_MARGIN_SYNTHESIS_BELOW_MM = 1.5
NAIL_BED_PIN_FRACTION = 0.85
LATERAL_TAPER_PER_DELTA = 0.02
FORECAST_MESH_COLS = 16
FORECAST_MESH_ROWS = 20
# ECC below this correlation is treated as a failure to converge.
ECC_MIN_CORRELATION = 0.45
HOMOGRAPHY_RANSAC_REPROJ_THRESHOLD_PX = 3.0

# Glare pixel test on the nail plate (HSV). The accept/reject cutoff is the
# configurable glare ratio, not these channel cuts.
GLARE_VALUE_MIN = 245
GLARE_SATURATION_MAX = 25

# Gabor kernels for the ridge score. See GABOR_RIDGE_SCORE_NORMALIZATION.
GABOR_KSIZE = 21
GABOR_SIGMA = 3.0
GABOR_LAMBD = 8
GABOR_GAMMA = 0.5
GABOR_PSI = 0.0
GABOR_THETA_LONGITUDINAL = 0.0
GABOR_THETA_TRANSVERSE = math.pi / 2

NOT_MEDICAL_ADVICE = (
    "NailTrack shares wellness observations only. It is not medical advice "
    "and does not diagnose, treat, or predict disease."
)

# Stable retake codes. Copy is fixed so the client HUD and the server
# re-validation of a submitted photo say the same thing.
QUALITY_REASON_MESSAGES: dict[str, str] = {
    "RETAKE_BLURRY": "Hold steady—nail surface microstructure is out of focus.",
    "RETAKE_GLARE": (
        "Tilt your finger slightly to eliminate overhead light reflection on the nail plate."
    ),
    "RETAKE_TILT": "Flatten your finger parallel to the camera lens.",
    "RETAKE_NO_HAND": (
        "No hand was detected. Keep the whole finger and the first knuckle in frame. "
        "You can save this photo anyway; the tilt check will be skipped."
    ),
    "RETAKE_NO_NAIL": (
        "The nail plate could not be separated from the surrounding skin. "
        "Use a plain background and fill the frame with the fingertip."
    ),
    "RETAKE_NO_CARD": (
        "The calibration card was not found. Place the full card flat beside the finger, "
        "with its border visible."
    ),
    "ALIGNMENT_FAILED": (
        "The photo could not be registered to the Day-0 baseline. "
        "Retake with the finger in a similar position and lighting."
    ),
}

# Blur, glare, tilt, and a missing hand can be stored anyway when the client
# passes force_save. A close-up of one nail often hides the palm, so a missing
# hand is the same kind of retake as tilt. A missing nail or calibration card
# still cannot be saved, because there is nothing to measure.
QUALITY_REASONS_FORCE_SAVE_CAN_OVERRIDE = frozenset(
    {"RETAKE_BLURRY", "RETAKE_GLARE", "RETAKE_TILT", "RETAKE_NO_HAND"}
)

# Wellness copy for the Phase 3 observation rules. Triggers stay in that phase:
# matrix cycle as general context, bed-length increase across aligned captures,
# and longitudinal ridges when ridge_gabor_score exceeds the configured threshold.
INSIGHT_MATRIX_CYCLE = (
    "A fingernail usually takes about 4–6 months to grow from the matrix to the free edge. "
    "This is a general wellness observation, not a prediction about your health."
)
INSIGHT_BED_LENGTH_INCREASE = (
    "The measured nail bed on this finger is longer than on earlier aligned photos. "
    "That can happen as the plate sits back down on the bed. It is an observation, not a diagnosis."
)
INSIGHT_LONGITUDINAL_RIDGES = (
    "Longitudinal ridges account for more of this nail's texture than the usual observation threshold. "
    "Some people like extra hydration or a protective coating. NailTrack is not recommending treatment."
)

GABOR_RIDGE_SCORE_NORMALIZATION = """
The ridge score is a unitless 0..1 measure of how much of the nail plate's
oriented texture is longitudinal ridging. Phase 2 computes it as follows.

1. Take the nail-plate ROI (lunula, nail bed, and free margin) and convert it
   to grayscale float32 in [0, 1].
2. Build two real Gabor kernels with cv2.getGaborKernel using ksize=21,
   sigma=3.0, lambd=8, gamma=0.5, psi=0. The longitudinal kernel uses theta=0.
   The transverse kernel uses theta=pi/2 (the Beau's-line orientation).
   Divide each kernel by its L2 norm so the two filters have unit energy and
   the energy floor below is comparable across images.
3. Energy of an orientation is the mean squared response over nail-plate
   pixels: E = mean(filter2D(roi, CV_32F, kernel) ** 2).
4. If E_long + E_trans is below the configurable GABOR_ENERGY_FLOOR
   (default 1e-4), the plate has no measurable striation and the score is 0.
   A ratio of two near-zero energies is noise, including on a smooth nail.
   Phase 2 should confirm this floor on real captures after the kernels are
   L2-normalized; the ratio itself does not need a camera-specific gain.
5. Otherwise ridge_gabor_score = clip(E_long / (E_long + E_trans), 0, 1).

Both orientations scale together when contrast changes, so the score stays in
0..1 without a per-device constant. A score above RIDGE_SCORE_INSIGHT_THRESHOLD
(default 0.35) means at least 35% of the oriented energy is longitudinal, which
is when the wellness ridge observation is shown. The score is not a diagnosis.

A transverse score, if a later phase stores one, uses the same floor and
E_trans / (E_long + E_trans).
""".strip()
