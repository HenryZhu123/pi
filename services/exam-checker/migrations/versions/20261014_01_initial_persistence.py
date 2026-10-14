"""Create exam document, review, and question bank tables.

Revision ID: 20261014_01
Revises:
Create Date: 2026-10-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261014_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exam_documents",
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column(
            "document_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("document_id"),
    )
    op.create_table(
        "question_reviews",
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("question_id", sa.String(), nullable=False),
        sa.Column(
            "review_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
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
        sa.PrimaryKeyConstraint("review_id"),
    )
    op.create_table(
        "question_bank",
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("question_id", sa.String(), nullable=False),
        sa.Column("source_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "question_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["exam_documents.document_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_review_id"],
            ["question_reviews.review_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("record_id"),
        sa.UniqueConstraint(
            "document_id", "question_id", name="uq_question_bank_source"
        ),
    )


def downgrade() -> None:
    op.drop_table("question_bank")
    op.drop_table("question_reviews")
    op.drop_table("exam_documents")
