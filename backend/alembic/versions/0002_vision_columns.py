"""Day-0 mask key and transverse Gabor score.

Revision ID: 0002_vision
Revises: 0001_initial
Create Date: 2026-11-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_vision"
down_revision: Union[str, Sequence[str], None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "finger_baselines",
        sa.Column("baseline_mask_key", sa.String(length=512), nullable=True),
    )
    with op.batch_alter_table("nail_captures") as batch:
        batch.add_column(sa.Column("transverse_gabor_score", sa.Float(), nullable=True))
        batch.create_check_constraint(
            "ck_nail_captures_transverse_unit_interval",
            "transverse_gabor_score IS NULL OR "
            "(transverse_gabor_score >= 0 AND transverse_gabor_score <= 1)",
        )


def downgrade() -> None:
    with op.batch_alter_table("nail_captures") as batch:
        batch.drop_constraint("ck_nail_captures_transverse_unit_interval", type_="check")
        batch.drop_column("transverse_gabor_score")
    op.drop_column("finger_baselines", "baseline_mask_key")
