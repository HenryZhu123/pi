"""Add immutable question versions and link downstream records.

Revision ID: 20261015_01
Revises: 20261014_01
Create Date: 2026-10-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261015_01"
down_revision: str | None = "20261014_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "question_versions",
        sa.Column("version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("question_id", sa.String(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revision_source", sa.String(), nullable=False),
        sa.Column(
            "question_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["exam_documents.document_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["question_versions.version_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("version_id"),
        sa.UniqueConstraint(
            "document_id",
            "question_id",
            "version_number",
            name="uq_question_version_number",
        ),
    )
    op.add_column(
        "question_reviews",
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_question_reviews_version",
        "question_reviews",
        "question_versions",
        ["question_version_id"],
        ["version_id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "question_bank",
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_question_bank_version",
        "question_bank",
        "question_versions",
        ["source_version_id"],
        ["version_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_question_bank_version", "question_bank", type_="foreignkey"
    )
    op.drop_column("question_bank", "source_version_id")
    op.drop_constraint(
        "fk_question_reviews_version", "question_reviews", type_="foreignkey"
    )
    op.drop_column("question_reviews", "question_version_id")
    op.drop_table("question_versions")
