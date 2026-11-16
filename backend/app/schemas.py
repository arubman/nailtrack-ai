"""Pydantic v2 contracts.

JSON columns on captures and baselines are validated with the models in this
module before they are stored. Insight responses always carry the wellness
disclaimer; nothing here is a diagnosis, and treatment velocity_modifier is a
number the user chose.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.constants import NOT_MEDICAL_ADVICE
from app.models import FingerCode, MaskSource, ScaleReferenceType, TreatmentCategory
from app.reference_white import (
    von_kries_gains_neutral_gray,
    von_kries_gains_white_to_d65,
)

ReferenceWhiteSource = Literal["id1_card_white", "gray_card", "manual_white"]
QualityReasonCode = Literal[
    "RETAKE_BLURRY",
    "RETAKE_GLARE",
    "RETAKE_TILT",
    "RETAKE_NO_HAND",
    "RETAKE_NO_NAIL",
    "RETAKE_NO_CARD",
]


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_target_fingers(value: list[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("target_fingers must be a non-empty list of finger codes or ['ALL']")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("target_fingers entries must be strings")
        normalized.append(item.strip().upper())
    if "ALL" in normalized:
        if normalized != ["ALL"]:
            raise ValueError("ALL cannot be combined with specific finger codes")
        return ["ALL"]
    allowed = {member.value for member in FingerCode}
    codes: list[str] = []
    for item in normalized:
        if item not in allowed:
            raise ValueError(f"unknown finger code: {item}")
        if item in codes:
            raise ValueError(f"duplicate finger code: {item}")
        codes.append(item)
    return codes


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None
    display_name: str | None


class QualityThresholds(BaseModel):
    blur_laplacian_min: float
    glare_ratio_max: float
    tilt_deg_max: float


class TimelineSlider(BaseModel):
    min_days: int
    max_days: int
    step_days: int


class StoragePublicInfo(BaseModel):
    backend: Literal["local", "azure"]
    read_access: Literal["authenticated_route", "sas"]
    sas_ttl_seconds: int | None = None


class SessionRead(BaseModel):
    """Bootstrap payload for the client, including the numbers the HUD must use."""

    user: UserRead
    auth_mode: Literal["dev", "entra"]
    disclaimer: str
    forecast_horizons_days: list[int]
    timeline_slider: TimelineSlider
    quality_thresholds: QualityThresholds
    ridge_score_insight_threshold: float
    gabor_energy_floor: float
    default_velocity_mm_day: float
    population_velocity_mm_day: float
    storage: StoragePublicInfo


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    environment: str
    auth_mode: Literal["dev", "entra"]
    database: Literal["ok"]


class ImageLink(BaseModel):
    """A way to read one private image.

    Azure returns a short-lived read SAS URL. Local dev returns a path on this
    API that still requires the caller's bearer token (or the dev bypass).
    Neither form is stored in the database.
    """

    url: str
    access: Literal["sas", "authenticated_route"]
    expires_in_seconds: int | None = None


class FingerSummary(BaseModel):
    finger_code: FingerCode
    baseline_id: uuid.UUID | None
    calibrated_width_mm: float | None
    observed_velocity_mm_day: float | None
    has_reference_white: bool
    scale_reference_type: ScaleReferenceType | None
    scale_reference_width_mm: float | None
    baseline_image: ImageLink | None


class ReferenceWhite(BaseModel):
    """Stored on finger_baselines.reference_white_json."""

    model_config = ConfigDict(extra="forbid")

    space: Literal["linear_srgb"] = "linear_srgb"
    mean_rgb: tuple[float, float, float]
    von_kries_gains: tuple[float, float, float]
    target: Literal["D65", "neutral_gray"]
    source: ReferenceWhiteSource
    patch_norm_bbox: tuple[float, float, float, float] | None = None
    gray_mean_rgb: tuple[float, float, float] | None = None
    gray_von_kries_gains: tuple[float, float, float] | None = None
    calibration_method: Literal["aruco", "quad"] | None = None

    @model_validator(mode="after")
    def _ranges(self) -> ReferenceWhite:
        for channel in self.mean_rgb:
            if channel <= 0 or channel > 1:
                raise ValueError("mean_rgb channels must be in (0, 1]")
        for gain in self.von_kries_gains:
            if gain <= 0:
                raise ValueError("von_kries_gains must be positive")
        if self.gray_mean_rgb is not None:
            for channel in self.gray_mean_rgb:
                if channel <= 0 or channel > 1:
                    raise ValueError("gray_mean_rgb channels must be in (0, 1]")
        if self.gray_von_kries_gains is not None:
            for gain in self.gray_von_kries_gains:
                if gain <= 0:
                    raise ValueError("gray_von_kries_gains must be positive")
        if self.patch_norm_bbox is not None:
            x, y, width, height = self.patch_norm_bbox
            if min(x, y, width, height) < 0 or max(x + width, y + height) > 1:
                raise ValueError("patch_norm_bbox must lie within the unit square")
        if self.source == "gray_card" and self.target != "neutral_gray":
            raise ValueError("gray_card references use target neutral_gray")
        if self.source in {"id1_card_white", "manual_white"} and self.target != "D65":
            raise ValueError("white references use target D65")
        return self


def reference_white_from_patch(
    *,
    mean_rgb: tuple[float, float, float],
    source: ReferenceWhiteSource,
    patch_norm_bbox: tuple[float, float, float, float] | None = None,
) -> ReferenceWhite:
    """Build the stored white-balance record from a measured linear-sRGB patch."""
    if source == "gray_card":
        gains = von_kries_gains_neutral_gray(mean_rgb)
        target: Literal["D65", "neutral_gray"] = "neutral_gray"
    else:
        gains = von_kries_gains_white_to_d65(mean_rgb)
        target = "D65"
    return ReferenceWhite(
        mean_rgb=mean_rgb,
        von_kries_gains=gains,
        target=target,
        source=source,
        patch_norm_bbox=patch_norm_bbox,
    )


class AnchorKeypoints(BaseModel):
    """Day-0 lock points. Alignment uses folds and the DIP crease, not the free edge."""

    model_config = ConfigDict(extra="forbid")

    left_fold: tuple[float, float]
    right_fold: tuple[float, float]
    dip_crease: list[tuple[float, float]] = Field(min_length=2)


class HomographyRecord(BaseModel):
    """3x3 map from the capture pixel grid onto the Day-0 baseline.

    `warpPerspective(capture, matrix)` lands on the baseline. `method` is
    `homography_ecc` when ECC refines the RANSAC homography, `affine_fallback`
    when ECC does not converge (typical on low-texture skin), or
    `homography_ransac` when the affine fit also fails and the RANSAC
    homography is kept. `alignment_quality` is in 0..1.
    """

    model_config = ConfigDict(extra="forbid")

    matrix: list[list[float]]
    ransac_inlier_rms: float | None = Field(default=None, ge=0)
    method: Literal["homography_ecc", "affine_fallback", "homography_ransac"] | None = None
    alignment_quality: float | None = Field(default=None, ge=0, le=1)
    inlier_ratio: float | None = Field(default=None, ge=0, le=1)
    ecc_correlation: float | None = None

    @model_validator(mode="after")
    def _matrix_shape(self) -> HomographyRecord:
        if len(self.matrix) != 3 or any(len(row) != 3 for row in self.matrix):
            raise ValueError("homography matrix must be 3x3")
        return self


class QualityMetrics(BaseModel):
    """Stored on nail_captures.quality_metrics_json after the server re-checks a photo."""

    model_config = ConfigDict(extra="forbid")

    laplacian_var: float = Field(ge=0)
    glare_ratio: float = Field(ge=0, le=1)
    tilt_deg: float | None = Field(default=None, ge=0)
    passed: bool
    reasons: list[QualityReasonCode] = Field(default_factory=list)
    hand_detected: bool = False
    handedness: str | None = None


class CielabColor(BaseModel):
    """Mean nail-bed color after Von Kries adaptation to the stored reference white."""

    model_config = ConfigDict(extra="forbid")

    L: float = Field(ge=0, le=100)
    a: float
    b: float


class TrackedFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    type: Literal["crack", "leukonychia"]
    rel_x: float = Field(ge=0, le=1)
    dist_from_cuticle_mm: float = Field(ge=0)


class WellnessInsight(BaseModel):
    """One wellness observation. disclaimer is always present on the object."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1)
    disclaimer: str = NOT_MEDICAL_ADVICE


class InsightList(BaseModel):
    """Response wrapper for any endpoint that returns insights."""

    insights: list[WellnessInsight]
    disclaimer: str = NOT_MEDICAL_ADVICE


class TreatmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started_at: datetime
    ended_at: datetime | None = None
    category: TreatmentCategory
    target_fingers: list[str]
    velocity_modifier: float = Field(
        default=1.0,
        gt=0,
        le=5,
        description=(
            "User-set multiplier for this person's own forecast. "
            "NailTrack does not claim this value is a measured effect of the logged regimen."
        ),
    )
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("started_at", "ended_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)

    @field_validator("target_fingers")
    @classmethod
    def _fingers(cls, value: list[str]) -> list[str]:
        return parse_target_fingers(value)

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _date_order(self) -> TreatmentCreate:
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at must be on or after started_at")
        return self


class TreatmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    started_at: datetime | None = None
    ended_at: datetime | None = None
    category: TreatmentCategory | None = None
    target_fingers: list[str] | None = None
    velocity_modifier: float | None = Field(default=None, gt=0, le=5)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("started_at", "ended_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)

    @field_validator("target_fingers")
    @classmethod
    def _fingers(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return parse_target_fingers(value)

    @field_validator("notes")
    @classmethod
    def _notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _date_order(self) -> TreatmentUpdate:
        # ended_at and notes may be cleared with JSON null. The other fields may not.
        for name in self.model_fields_set - {"ended_at", "notes"}:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        if (
            self.started_at is not None
            and self.ended_at is not None
            and self.ended_at < self.started_at
        ):
            raise ValueError("ended_at must be on or after started_at")
        return self


class TreatmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None
    category: TreatmentCategory
    target_fingers: list[str]
    velocity_modifier: float
    notes: str | None
    created_at: datetime
    updated_at: datetime


class TreatmentListResponse(BaseModel):
    items: list[TreatmentRead]
    total: int
    limit: int
    offset: int


class QualityRejection(BaseModel):
    """Body of HTTP 422 when a photo is refused or cannot be measured."""

    passed: bool = False
    reasons: list[str]
    messages: list[str]
    metrics: QualityMetrics


class CalibrationRead(BaseModel):
    finger_code: FingerCode
    baseline_id: uuid.UUID
    calibrated_width_mm: float
    scale_reference_type: ScaleReferenceType
    scale_reference_width_mm: float
    mm_per_px: float
    reference_white: ReferenceWhite
    anchor_keypoints: AnchorKeypoints
    quality: QualityMetrics
    baseline_image: ImageLink


class CaptureMeasurementRead(BaseModel):
    id: uuid.UUID
    finger_code: FingerCode
    baseline_id: uuid.UUID
    captured_at: datetime
    quality: QualityMetrics
    nail_bed_length_mm: float | None
    free_margin_length_mm: float | None
    total_length_mm: float | None
    lunula_ratio: float | None
    ridge_gabor_score: float | None
    transverse_gabor_score: float | None
    cielab: CielabColor | None
    homography: HomographyRecord | None
    mask_source: MaskSource | None
    raw_image: ImageLink | None
    aligned_image: ImageLink | None
    segmentation_mask: ImageLink | None
    corrected_mask: ImageLink | None


class MeshVertex(BaseModel):
    """One control point on the latest aligned frame.

    (x, y) is the Day-0 / today pixel. (dx, dy) is the forecast displacement.
    Pinned vertices stay put so the client can interpolate between horizons.
    """

    x: float
    y: float
    dx: float
    dy: float
    pinned: bool


class TimelineFeature(BaseModel):
    id: str
    type: Literal["crack", "leukonychia"]
    rel_x: float
    dist_from_cuticle_mm: float
    clipped: bool
    days_until_clear: float | None = None


class TimelineFrame(BaseModel):
    t_days: float
    kind: Literal["history", "today", "forecast"]
    capture_id: uuid.UUID | None = None
    image: ImageLink | None = None
    mask: ImageLink | None = None
    delta_length_mm: float = 0.0
    nail_bed_length_mm: float | None = None
    free_margin_length_mm: float | None = None
    total_length_mm: float | None = None
    mesh: list[MeshVertex] | None = None
    features: list[TimelineFeature] = Field(default_factory=list)
    synthesized_free_margin: bool = False


class TimelineRead(BaseModel):
    finger_code: FingerCode
    disclaimer: str
    insights: list[WellnessInsight]
    v_observed_mm_day: float
    v_effective_mm_day: float
    alpha_tx: float
    frames: list[TimelineFrame]


class CaptureMaskState(BaseModel):
    """Schema support for the segmenter that does not exist yet.

    auto_classical and model require the automatic mask key. user_corrected
    requires the corrected mask key. The automatic mask is kept when the user
    corrects it so the pair can be exported as training data later.
    """

    model_config = ConfigDict(extra="forbid")

    mask_source: MaskSource | None = None
    segmentation_mask_key: str | None = None
    corrected_mask_key: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> CaptureMaskState:
        if self.mask_source is None:
            if self.segmentation_mask_key or self.corrected_mask_key:
                raise ValueError("mask keys require mask_source")
            return self
        if self.mask_source is MaskSource.USER_CORRECTED and not self.corrected_mask_key:
            raise ValueError("user_corrected requires corrected_mask_key")
        if self.mask_source in {MaskSource.AUTO_CLASSICAL, MaskSource.MODEL} and (
            not self.segmentation_mask_key
        ):
            raise ValueError(f"{self.mask_source.value} requires segmentation_mask_key")
        return self
