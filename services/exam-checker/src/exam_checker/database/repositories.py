from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..domain.models import (
    ExamDocument,
    QuestionChangeLog,
    QuestionView,
    ReviewedQuestion,
    StoredQuestionBankEntry,
    StoredQuestionVersion,
)
from ..services.document_store import DocumentNotFoundError
from ..services.persistence import (
    QuestionAlreadyStoredError,
    QuestionBankEntryNotFoundError,
    ReviewNotFoundError,
    QuestionVersionConflictError,
    QuestionVersionNotFoundError,
    StoredQuestionReview,
)
from .models import (
    ExamDocumentRow,
    QuestionBankRow,
    QuestionReviewRow,
    QuestionVersionRow,
)


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
        review_id = UUID(review.review_id) if review.review_id else uuid4()
        async with self.sessions.begin() as session:
            session.add(
                QuestionReviewRow(
                    review_id=review_id,
                    document_id=review.document_id,
                    question_id=review.question_id,
                    question_version_id=(
                        UUID(review.question_version_id)
                        if review.question_version_id
                        else None
                    ),
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

    async def get_latest_for_version(
        self, document_id: str, question_id: str, question_version_id: UUID
    ) -> StoredQuestionReview:
        statement = (
            select(QuestionReviewRow)
            .where(
                QuestionReviewRow.document_id == document_id,
                QuestionReviewRow.question_id == question_id,
                QuestionReviewRow.question_version_id == question_version_id,
            )
            .order_by(QuestionReviewRow.created_at.desc())
            .limit(1)
        )
        async with self.sessions() as session:
            row = await session.scalar(statement)
        if row is None:
            raise ReviewNotFoundError(
                (document_id, question_id, question_version_id)
            )
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
        source_version_id: UUID,
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
                    source_version_id=source_version_id,
                    question_json=question_json,
                )
            )

    async def get(self, record_id: UUID) -> StoredQuestionBankEntry:
        async with self.sessions() as session:
            row = await session.get(QuestionBankRow, record_id)
        if row is None:
            raise QuestionBankEntryNotFoundError(record_id)
        return self._to_domain(row)

    async def get_by_source(
        self, document_id: str, question_id: str
    ) -> StoredQuestionBankEntry | None:
        statement = select(QuestionBankRow).where(
            QuestionBankRow.document_id == document_id,
            QuestionBankRow.question_id == question_id,
        )
        async with self.sessions() as session:
            row = await session.scalar(statement)
        return self._to_domain(row) if row is not None else None

    async def search(
        self,
        *,
        subject: str | None,
        question_type: str | None,
        difficulty: str | None,
        knowledge_point: str | None,
        keyword: str | None,
        limit: int,
    ) -> list[StoredQuestionBankEntry]:
        statement = select(QuestionBankRow)
        if subject:
            statement = statement.where(
                QuestionBankRow.question_json["question"]["subject"].astext
                == subject
            )
        if question_type:
            statement = statement.where(
                QuestionBankRow.question_json["question"]["question_type"].astext
                == question_type
            )
        if difficulty:
            statement = statement.where(
                QuestionBankRow.question_json["metadata"]["difficulty"].astext
                == difficulty
            )
        if knowledge_point:
            statement = statement.where(
                QuestionBankRow.question_json["metadata"][
                    "knowledge_points"
                ].contains([knowledge_point])
            )
        if keyword:
            statement = statement.where(
                QuestionBankRow.question_json["question"]["question_text"]
                .astext.ilike(f"%{keyword}%")
            )
        statement = statement.order_by(QuestionBankRow.created_at.desc()).limit(limit)
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [self._to_domain(row) for row in rows]

    @staticmethod
    def _to_domain(row: QuestionBankRow) -> StoredQuestionBankEntry:
        return StoredQuestionBankEntry(
            record_id=str(row.record_id),
            document_id=row.document_id,
            question_id=row.question_id,
            source_version_id=(
                str(row.source_version_id) if row.source_version_id else None
            ),
            question_json=row.question_json,
        )


class PostgresQuestionVersionRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def initialize(self, questions: list[QuestionView]) -> None:
        if not questions:
            return
        async with self.sessions.begin() as session:
            for question in questions:
                statement = insert(QuestionVersionRow).values(
                    version_id=uuid4(),
                    document_id=question.document_id,
                    question_id=question.question_id,
                    version_number=1,
                    parent_version_id=None,
                    revision_source="PARSED",
                    question_json=question.model_dump(mode="json"),
                    change_log=None,
                )
                await session.execute(
                    statement.on_conflict_do_nothing(
                        constraint="uq_question_version_number"
                    )
                )

    async def get_latest(
        self, document_id: str, question_id: str
    ) -> StoredQuestionVersion:
        statement = (
            select(QuestionVersionRow)
            .where(
                QuestionVersionRow.document_id == document_id,
                QuestionVersionRow.question_id == question_id,
            )
            .order_by(QuestionVersionRow.version_number.desc())
            .limit(1)
        )
        async with self.sessions() as session:
            row = await session.scalar(statement)
        if row is None:
            raise QuestionVersionNotFoundError((document_id, question_id))
        return self._to_domain(row)

    async def create_revision(
        self,
        *,
        document_id: str,
        question_id: str,
        base_version_id: UUID,
        revision_source: str,
        question: QuestionView,
        change_log: QuestionChangeLog,
    ) -> StoredQuestionVersion:
        statement = (
            select(QuestionVersionRow)
            .where(
                QuestionVersionRow.document_id == document_id,
                QuestionVersionRow.question_id == question_id,
            )
            .order_by(QuestionVersionRow.version_number.desc())
            .limit(1)
            .with_for_update()
        )
        async with self.sessions.begin() as session:
            await session.scalar(
                select(ExamDocumentRow.document_id)
                .where(ExamDocumentRow.document_id == document_id)
                .with_for_update()
            )
            latest = await session.scalar(statement)
            if latest is None:
                raise QuestionVersionNotFoundError((document_id, question_id))
            if latest.version_id != base_version_id:
                raise QuestionVersionConflictError(
                    "The question changed after the requested base version"
                )
            row = QuestionVersionRow(
                version_id=uuid4(),
                document_id=document_id,
                question_id=question_id,
                version_number=latest.version_number + 1,
                parent_version_id=latest.version_id,
                revision_source=revision_source,
                question_json=question.model_dump(mode="json"),
                change_log=change_log.model_dump(mode="json"),
            )
            session.add(row)
            await session.flush()
            return self._to_domain(row)

    @staticmethod
    def _to_domain(row: QuestionVersionRow) -> StoredQuestionVersion:
        return StoredQuestionVersion(
            version_id=str(row.version_id),
            document_id=row.document_id,
            question_id=row.question_id,
            version_number=row.version_number,
            parent_version_id=(
                str(row.parent_version_id) if row.parent_version_id else None
            ),
            revision_source=row.revision_source,
            question=QuestionView.model_validate(row.question_json),
            change_log=(
                QuestionChangeLog.model_validate(row.change_log)
                if row.change_log is not None
                else None
            ),
        )
