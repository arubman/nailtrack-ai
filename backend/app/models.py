"""ORM tables.

Every finger baseline, capture, and treatment belongs to a user. Image columns
store blob keys (paths), never permanent public URLs. A capture points at its
Day-0 baseline by id; finger_code is denormalized and locked to that baseline
by a composite foreign key so the copy cannot drift.

Mask columns leave room for a classical segmenter and a later user correction:
the automatic mask and the corrected mask are both kept, and mask_source says
which one is current (auto_classical, user_corrected, or model). Corrected
masks are the training pairs a future model can learn from.

reference_white_json holds the Von Kries reference measured from the white
area of the ID-1 card or a gray/white card, not from periungual skin.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.config import get_settings
from app.database import Base, UTCDateTime, utcnow


def _default_velocity() -> float:
    return get_settings().default_velocity_mm_day


class FingerCode(str, enum.Enum):
    L_THUMB = "L_THUMB"
    L_INDEX = "L_INDEX"
    L_MIDDLE = "L_MIDDLE"
    L_RING = "L_RING"
    L_PINKY = "L_PINKY"
    R_THUMB = "R_THUMB"
    R_INDEX = "R_INDEX"
    R_MIDDLE = "R_MIDDLE"
    R_RING = "R_RING"
    R_PINKY = "R_PINKY"


class TreatmentCategory(str, enum.Enum):
    SUPPLEMENT_BIOTIN = "SUPPLEMENT_BIOTIN"
    TOPICAL_OIL = "TOPICAL_OIL"
    STRUCTURAL_REPAIR_WRAP_GLUE = "STRUCTURAL_REPAIR_WRAP_GLUE"
    BUILDER_GEL_OVERLAY = "BUILDER_GEL_OVERLAY"
    GROOMING_TRIM = "GROOMING_TRIM"
    TRAUMA_NOTE = "TRAUMA_NOTE"


class MaskSource(str, enum.Enum):
    """How the mask that should be treated as current was produced."""

    AUTO_CLASSICAL = "auto_classical"
    USER_CORRECTED = "user_corrected"
    MODEL = "model"


class ScaleReferenceType(str, enum.Enum):
    """Physical object used to turn pixels into millimetres.

    Color reference is separate and lives in reference_white_json. A gray card
    used only for white balance is MANUAL_MM when its width is known, or is
    omitted here when it does not provide scale.
    """

    ID1_CARD = "ID1_CARD"
    MANUAL_MM = "MANUAL_MM"


def _enum(enum_cls: type[enum.Enum], name: str, length: int) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=length,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Entra object id, prefixed with the tenant id when the token has tid: "{tid}:{oid}".
    # Dev mode uses the configured DEV_USER_SUBJECT (default "local-dev-user").
    external_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )

    baselines: Mapped[list[FingerBaseline]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    captures: Mapped[list[NailCapture]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="NailCapture.user_id",
    )
    treatments: Mapped[list[Treatment]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (Index("uq_users_external_subject", "external_subject", unique=True),)


class FingerBaseline(Base):
    """One Day-0 calibration per finger per user."""

    __tablename__ = "finger_baselines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    finger_code: Mapped[FingerCode] = mapped_column(_enum(FingerCode, "finger_code", 16), nullable=False)
    # Invariant lateral nail-fold width in millimetres, measured once.
    calibrated_width_mm: Mapped[float] = mapped_column(Float, nullable=False)
    scale_reference_type: Mapped[ScaleReferenceType | None] = mapped_column(
        _enum(ScaleReferenceType, "scale_reference_type", 32), nullable=True
    )
    # Physical width of the scale object itself (85.60 for an ID-1 card), not the nail.
    scale_reference_width_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_image_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Indexed PNG of the Day-0 4-class mask, same pixel grid as baseline_image_key.
    baseline_mask_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    anchor_keypoints_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {"space", "mean_rgb", "von_kries_gains", "target", "source", "patch_norm_bbox"}
    reference_white_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Last forecast summary (deltas, alpha, insights) rewritten when captures or treatments change.
    forecast_cache_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    observed_velocity_mm_day: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=_default_velocity,
        server_default=text("0.11"),
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="baselines")
    captures: Mapped[list[NailCapture]] = relationship(
        back_populates="baseline",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="NailCapture.baseline_id",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_finger_baselines_user_id",
            ondelete="CASCADE",
        ),
        # Business key: one baseline per finger per person.
        Index(
            "uq_finger_baselines_user_finger",
            "user_id",
            "finger_code",
            unique=True,
        ),
        # Exact parent key for nail_captures' composite foreign key. id is already
        # unique; SQLite and PostgreSQL still require a unique constraint on the
        # full referenced column list.
        Index(
            "uq_finger_baselines_id_user_finger",
            "id",
            "user_id",
            "finger_code",
            unique=True,
        ),
        CheckConstraint(
            "calibrated_width_mm > 0",
            name="ck_finger_baselines_width_positive",
        ),
        CheckConstraint(
            "scale_reference_width_mm IS NULL OR scale_reference_width_mm > 0",
            name="ck_finger_baselines_scale_ref_positive",
        ),
        CheckConstraint(
            "observed_velocity_mm_day > 0",
            name="ck_finger_baselines_velocity_positive",
        ),
    )


class NailCapture(Base):
    __tablename__ = "nail_captures"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    baseline_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    # Denormalized from the baseline and constrained to match it.
    finger_code: Mapped[FingerCode] = mapped_column(_enum(FingerCode, "finger_code", 16), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    raw_image_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    aligned_image_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Automatic mask (classical segmenter or, later, a model). Indexed PNG:
    # 0 background, 1 lunula, 2 nail bed, 3 free margin.
    segmentation_mask_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # User-corrected mask kept beside the automatic one so corrections can
    # become training pairs. This is the mask to train on when present.
    corrected_mask_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mask_source: Mapped[MaskSource | None] = mapped_column(
        _enum(MaskSource, "mask_source", 32), nullable=True
    )
    mask_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    homography_matrix_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quality_metrics_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    nail_bed_length_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    free_margin_length_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_length_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    lunula_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    ridge_gabor_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Share of oriented Gabor energy that is transverse (Beau's lines). Same 0..1 normalization.
    transverse_gabor_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cielab_color_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tracked_features_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    user: Mapped[User] = relationship(back_populates="captures", foreign_keys=[user_id])
    baseline: Mapped[FingerBaseline] = relationship(
        back_populates="captures",
        foreign_keys=[baseline_id],
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_nail_captures_user_id",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["baseline_id", "user_id", "finger_code"],
            [
                "finger_baselines.id",
                "finger_baselines.user_id",
                "finger_baselines.finger_code",
            ],
            name="fk_nail_captures_baseline_scope",
            ondelete="CASCADE",
        ),
        Index("ix_nail_captures_baseline_id", "baseline_id"),
        Index("ix_nail_captures_captured_at", "captured_at"),
        Index(
            "ix_nail_captures_user_finger_captured",
            "user_id",
            "finger_code",
            "captured_at",
        ),
        CheckConstraint(
            "nail_bed_length_mm IS NULL OR nail_bed_length_mm >= 0",
            name="ck_nail_captures_bed_nonnegative",
        ),
        CheckConstraint(
            "free_margin_length_mm IS NULL OR free_margin_length_mm >= 0",
            name="ck_nail_captures_free_nonnegative",
        ),
        CheckConstraint(
            "total_length_mm IS NULL OR total_length_mm >= 0",
            name="ck_nail_captures_total_nonnegative",
        ),
        CheckConstraint(
            "lunula_ratio IS NULL OR (lunula_ratio >= 0 AND lunula_ratio <= 1)",
            name="ck_nail_captures_lunula_ratio_unit_interval",
        ),
        CheckConstraint(
            "ridge_gabor_score IS NULL OR (ridge_gabor_score >= 0 AND ridge_gabor_score <= 1)",
            name="ck_nail_captures_ridge_unit_interval",
        ),
        CheckConstraint(
            "transverse_gabor_score IS NULL OR (transverse_gabor_score >= 0 AND transverse_gabor_score <= 1)",
            name="ck_nail_captures_transverse_unit_interval",
        ),
        CheckConstraint(
            "("
            "mask_source IS NULL AND segmentation_mask_key IS NULL AND corrected_mask_key IS NULL"
            ") OR ("
            "mask_source = 'auto_classical' AND segmentation_mask_key IS NOT NULL"
            ") OR ("
            "mask_source = 'model' AND segmentation_mask_key IS NOT NULL"
            ") OR ("
            "mask_source = 'user_corrected' AND corrected_mask_key IS NOT NULL"
            ")",
            name="ck_nail_captures_mask_source_consistent",
        ),
    )


class Treatment(Base):
    """A user-logged regimen. velocity_modifier is the user's own multiplier.

    The column does not assert that a supplement or coating changed growth.
    Phase 3 multiplies it into the forecast (alpha_tx) only because the user set it.
    """

    __tablename__ = "treatments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_treatments_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    category: Mapped[TreatmentCategory] = mapped_column(
        _enum(TreatmentCategory, "treatment_category", 40), nullable=False
    )
    # FingerCode values, or ["ALL"].
    target_fingers_json: Mapped[list] = mapped_column(JSON, nullable=False)
    velocity_modifier: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        server_default=text("1.0"),
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )

    user: Mapped[User] = relationship(back_populates="treatments")

    __table_args__ = (
        Index("ix_treatments_user_started", "user_id", "started_at"),
        CheckConstraint(
            "velocity_modifier > 0",
            name="ck_treatments_velocity_modifier_positive",
        ),
    )

    @property
    def target_fingers(self) -> list[str]:
        return list(self.target_fingers_json or [])
