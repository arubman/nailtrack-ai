"""Wellness observations. These are not diagnoses and do not recommend treatment.

Each insight carries the shared disclaimer. Ridge wording says some people
like hydration or a coating; it does not tell the user they have a condition.
"""

from __future__ import annotations

from app.constants import (
    INSIGHT_BED_LENGTH_INCREASE,
    INSIGHT_LONGITUDINAL_RIDGES,
    INSIGHT_MATRIX_CYCLE,
    NOT_MEDICAL_ADVICE,
)
from app.schemas import InsightList, WellnessInsight


def build_insights(
    *,
    lengths: list[tuple[float | None, float | None]],
    ridge_score: float | None,
    ridge_threshold: float,
    features: list[dict],
    total_length_mm: float | None,
    velocity_mm_day: float,
) -> InsightList:
    """lengths is oldest-to-newest (nail_bed_mm, total_mm) for this finger."""
    insights: list[WellnessInsight] = []
    if any(bed is not None for bed, _total in lengths):
        insights.append(WellnessInsight(code="matrix_cycle", message=INSIGHT_MATRIX_CYCLE))
    beds = [bed for bed, _total in lengths if bed is not None]
    if len(beds) >= 2 and beds[-1] > beds[0] + 0.3:
        insights.append(WellnessInsight(code="bed_length_increase", message=INSIGHT_BED_LENGTH_INCREASE))
    if ridge_score is not None and ridge_score > ridge_threshold:
        insights.append(WellnessInsight(code="longitudinal_ridges", message=INSIGHT_LONGITUDINAL_RIDGES))
    if total_length_mm is not None and velocity_mm_day > 1e-4:
        for feature in features:
            distance = float(feature["dist_from_cuticle_mm"])
            remaining = total_length_mm - distance
            if remaining <= 0:
                continue
            days = remaining / velocity_mm_day
            kind = "mark" if feature["type"] == "leukonychia" else "line"
            insights.append(
                WellnessInsight(
                    code=f"feature_clear_{feature['id']}",
                    message=(
                        f"The {kind} about {distance:.1f} mm from the cuticle would reach the current "
                        f"free edge in about {days:.0f} days if the nail stays this length and keeps "
                        f"today's pace. That is a wellness observation about growth, not a prediction "
                        f"about health."
                    ),
                )
            )
    return InsightList(insights=insights, disclaimer=NOT_MEDICAL_ADVICE)
