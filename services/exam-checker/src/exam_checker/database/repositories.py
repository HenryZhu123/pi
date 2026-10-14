from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain.models import ExamDocument, ReviewedQuestion
from ..services.document_store import DocumentNotFoundError
from ..services.persistence import (
    QuestionAlreadyStoredError,
    ReviewNotFoundError,
    StoredQuestionReview,
)
from .models import ExamDocumentRow, QuestionBankRow, QuestionReviewRow


class PostgresExamDocumentStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def save(self, document: ExamDocument) -> None:
        statement = insert(ExamDocumentRow).values(
            document_id=document.document_id,
            document_json=document.model_dump(mode="json"),
        )
        statement = statement.on_conflict_do_update(
            index_elements=[ExamDocumentRow.document_id],
            set_={"document_json": statement.excluded.document_json},
        )
        async with self.sessions.begin() as session:
            await session.execute(statement)

    async def get(self, document_id: str) -> ExamDocument:
        async with self.sessions() as session:
            payload = await session.scalar(
                select(ExamDocumentRow.document_json).where(
                    ExamDocumentRow.document_id == document_id
                )
            )
        if payload is None:
            raise DocumentNotFoundError(document_id)
        return ExamDocument.model_validate(payload)


class PostgresQuestionReviewRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def save(self, review: ReviewedQuestion) -> UUID:
        review_id = uuid4()
        async with self.sessions.begin() as session:
            session.add(
                QuestionReviewRow(
                    review_id=review_id,
                    document_id=review.document_id,
                    question_id=review.question_id,
                    review_json=review.model_dump(mode="json"),
                )
            )
        return review_id

    async def get_latest(
        self, document_id: str, question_id: str
    ) -> StoredQuestionReview:
        statement = (
            select(QuestionReviewRow)
            .where(
                QuestionReviewRow.document_id == document_id,
                QuestionReviewRow.question_id == question_id,
            )
            .order_by(QuestionReviewRow.created_at.desc())
            .limit(1)
        )
        async with self.sessions() as session:
            row = await session.scalar(statement)
        if row is None:
            raise ReviewNotFoundError((document_id, question_id))
        return StoredQuestionReview(
            review_id=row.review_id,
            review=ReviewedQuestion.model_validate(row.review_json),
        )


class PostgresQuestionBankRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def store(
        self,
        *,
        record_id: UUID,
        source_review_id: UUID,
        document_id: str,
        question_id: str,
        question_json: dict[str, Any],
    ) -> None:
        async with self.sessions.begin() as session:
            existing = await session.scalar(
                select(QuestionBankRow.record_id).where(
                    QuestionBankRow.document_id == document_id,
                    QuestionBankRow.question_id == question_id,
                )
            )
            if existing is not None:
                raise QuestionAlreadyStoredError((document_id, question_id))
            session.add(
                QuestionBankRow(
                    record_id=record_id,
                    document_id=document_id,
                    question_id=question_id,
                    source_review_id=source_review_id,
                    question_json=question_json,
                )
            )
