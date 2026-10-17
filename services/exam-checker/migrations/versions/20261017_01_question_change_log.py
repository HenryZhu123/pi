"""Add revision change logs to question versions.

Revision ID: 20261017_01
Revises: 20261015_01
Create Date: 2026-10-17
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261017_01"
down_revision: str | None = "20261015_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "question_versions",
        sa.Column(
            "change_log", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("question_versions", "change_log")
