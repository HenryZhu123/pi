from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ExamDocumentRow(Base):
    __tablename__ = "exam_documents"

    document_id: Mapped[str] = mapped_column(String, primary_key=True)
    document_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QuestionReviewRow(Base):
    __tablename__ = "question_reviews"

    review_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("exam_documents.document_id", ondelete="RESTRICT"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String, nullable=False)
    review_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QuestionBankRow(Base):
    __tablename__ = "question_bank"
    __table_args__ = (
        UniqueConstraint("document_id", "question_id", name="uq_question_bank_source"),
    )

    record_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("exam_documents.document_id", ondelete="RESTRICT"), nullable=False
    )
    question_id: Mapped[str] = mapped_column(String, nullable=False)
    source_review_id: Mapped[UUID] = mapped_column(
        ForeignKey("question_reviews.review_id", ondelete="RESTRICT"), nullable=False
    )
    question_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
