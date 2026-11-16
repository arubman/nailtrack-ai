"""Initial NailTrack schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-16

Mirrors app.models: users, per-user finger baselines, captures keyed to a
baseline id, and treatments. Image columns are blob keys. Enums are stored
as strings so SQLite and PostgreSQL share one migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FINGER_CODES = (
    "L_THUMB",
    "L_INDEX",
    "L_MIDDLE",
    "L_RING",
    "L_PINKY",
    "R_THUMB",
    "R_INDEX",
    "R_MIDDLE",
    "R_RING",
    "R_PINKY",
)
TREATMENT_CATEGORIES = (
    "SUPPLEMENT_BIOTIN",
    "TOPICAL_OIL",
    "STRUCTURAL_REPAIR_WRAP_GLUE",
    "BUILDER_GEL_OVERLAY",
    "GROOMING_TRIM",
    "TRAUMA_NOTE",
)
MASK_SOURCES = ("auto_classical", "user_corrected", "model")
SCALE_REFERENCES = ("ID1_CARD", "MANUAL_MM")


def _enum(name: str, values: tuple[str, ...], length: int) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        length=length,
        create_constraint=True,
    )


def upgrade() -> None:
    finger_code = _enum("finger_code", FINGER_CODES, 16)
    finger_code_capture = _enum("finger_code", FINGER_CODES, 16)
    scale_reference = _enum("scale_reference_type", SCALE_REFERENCES, 32)
    mask_source = _enum("mask_source", MASK_SOURCES, 32)
    treatment_category = _enum("treatment_category", TREATMENT_CATEGORIES, 40)

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("external_subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_users_external_subject", "users", ["external_subject"], unique=True)

    op.create_table(
        "finger_baselines",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("finger_code", finger_code, nullable=False),
        sa.Column("calibrated_width_mm", sa.Float(), nullable=False),
        sa.Column("scale_reference_type", scale_reference, nullable=True),
        sa.Column("scale_reference_width_mm", sa.Float(), nullable=True),
        sa.Column("baseline_image_key", sa.String(length=512), nullable=True),
        sa.Column("anchor_keypoints_json", sa.JSON(), nullable=True),
        sa.Column("reference_white_json", sa.JSON(), nullable=True),
        sa.Column(
            "observed_velocity_mm_day",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.11"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_finger_baselines_user_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "calibrated_width_mm > 0",
            name="ck_finger_baselines_width_positive",
        ),
        sa.CheckConstraint(
            "scale_reference_width_mm IS NULL OR scale_reference_width_mm > 0",
            name="ck_finger_baselines_scale_ref_positive",
        ),
        sa.CheckConstraint(
            "observed_velocity_mm_day > 0",
            name="ck_finger_baselines_velocity_positive",
        ),
    )
    op.create_index(
        "uq_finger_baselines_user_finger",
        "finger_baselines",
        ["user_id", "finger_code"],
        unique=True,
    )
    op.create_index(
        "uq_finger_baselines_id_user_finger",
        "finger_baselines",
        ["id", "user_id", "finger_code"],
        unique=True,
    )

    op.create_table(
        "nail_captures",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("baseline_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("finger_code", finger_code_capture, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_image_key", sa.String(length=512), nullable=True),
        sa.Column("aligned_image_key", sa.String(length=512), nullable=True),
        sa.Column("segmentation_mask_key", sa.String(length=512), nullable=True),
        sa.Column("corrected_mask_key", sa.String(length=512), nullable=True),
        sa.Column("mask_source", mask_source, nullable=True),
        sa.Column("mask_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("homography_matrix_json", sa.JSON(), nullable=True),
        sa.Column("quality_metrics_json", sa.JSON(), nullable=True),
        sa.Column("nail_bed_length_mm", sa.Float(), nullable=True),
        sa.Column("free_margin_length_mm", sa.Float(), nullable=True),
        sa.Column("total_length_mm", sa.Float(), nullable=True),
        sa.Column("lunula_ratio", sa.Float(), nullable=True),
        sa.Column("ridge_gabor_score", sa.Float(), nullable=True),
        sa.Column("cielab_color_json", sa.JSON(), nullable=True),
        sa.Column("tracked_features_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_nail_captures_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_id", "user_id", "finger_code"],
            [
                "finger_baselines.id",
                "finger_baselines.user_id",
                "finger_baselines.finger_code",
            ],
            name="fk_nail_captures_baseline_scope",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "nail_bed_length_mm IS NULL OR nail_bed_length_mm >= 0",
            name="ck_nail_captures_bed_nonnegative",
        ),
        sa.CheckConstraint(
            "free_margin_length_mm IS NULL OR free_margin_length_mm >= 0",
            name="ck_nail_captures_free_nonnegative",
        ),
        sa.CheckConstraint(
            "total_length_mm IS NULL OR total_length_mm >= 0",
            name="ck_nail_captures_total_nonnegative",
        ),
        sa.CheckConstraint(
            "lunula_ratio IS NULL OR (lunula_ratio >= 0 AND lunula_ratio <= 1)",
            name="ck_nail_captures_lunula_ratio_unit_interval",
        ),
        sa.CheckConstraint(
            "ridge_gabor_score IS NULL OR (ridge_gabor_score >= 0 AND ridge_gabor_score <= 1)",
            name="ck_nail_captures_ridge_unit_interval",
        ),
        sa.CheckConstraint(
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
    op.create_index("ix_nail_captures_baseline_id", "nail_captures", ["baseline_id"])
    op.create_index("ix_nail_captures_captured_at", "nail_captures", ["captured_at"])
    op.create_index(
        "ix_nail_captures_user_finger_captured",
        "nail_captures",
        ["user_id", "finger_code", "captured_at"],
    )

    op.create_table(
        "treatments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("category", treatment_category, nullable=False),
        sa.Column("target_fingers_json", sa.JSON(), nullable=False),
        sa.Column(
            "velocity_modifier",
            sa.Float(),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_treatments_user_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "velocity_modifier > 0",
            name="ck_treatments_velocity_modifier_positive",
        ),
    )
    op.create_index(
        "ix_treatments_user_started",
        "treatments",
        ["user_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_treatments_user_started", table_name="treatments")
    op.drop_table("treatments")
    op.drop_index("ix_nail_captures_user_finger_captured", table_name="nail_captures")
    op.drop_index("ix_nail_captures_captured_at", table_name="nail_captures")
    op.drop_index("ix_nail_captures_baseline_id", table_name="nail_captures")
    op.drop_table("nail_captures")
    op.drop_index("uq_finger_baselines_id_user_finger", table_name="finger_baselines")
    op.drop_index("uq_finger_baselines_user_finger", table_name="finger_baselines")
    op.drop_table("finger_baselines")
    op.drop_index("uq_users_external_subject", table_name="users")
    op.drop_table("users")
