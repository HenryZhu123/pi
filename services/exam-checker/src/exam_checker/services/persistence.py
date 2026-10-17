from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID
from uuid import uuid4

from ..domain.models import (
    QuestionChangeLog,
    QuestionView,
    ReviewedQuestion,
    StoredQuestionBankEntry,
    StoredQuestionVersion,
)


class ReviewNotFoundError(KeyError):
    pass


class QuestionAlreadyStoredError(ValueError):
    pass


class QuestionBankEntryNotFoundError(KeyError):
    pass


class QuestionVersionNotFoundError(KeyError):
    pass


class QuestionVersionConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StoredQuestionReview:
    review_id: UUID
    review: ReviewedQuestion


class QuestionReviewRepository(Protocol):
    async def save(self, review: ReviewedQuestion) -> UUID: ...

    async def get_latest(
        self, document_id: str, question_id: str
    ) -> StoredQuestionReview: ...

    async def get_latest_for_version(
        self, document_id: str, question_id: str, question_version_id: UUID
    ) -> StoredQuestionReview: ...


class QuestionBankRepository(Protocol):
    async def store(
        self,
        *,
        record_id: UUID,
        source_review_id: UUID,
        source_version_id: UUID,
        document_id: str,
        question_id: str,
        question_json: dict[str, Any],
    ) -> None: ...

    async def get(self, record_id: UUID) -> StoredQuestionBankEntry: ...

    async def get_by_source(
        self, document_id: str, question_id: str
    ) -> StoredQuestionBankEntry | None: ...

    async def search(
        self,
        *,
        subject: str | None,
        question_type: str | None,
        difficulty: str | None,
        knowledge_point: str | None,
        keyword: str | None,
        limit: int,
    ) -> list[StoredQuestionBankEntry]: ...


class QuestionVersionRepository(Protocol):
    async def initialize(self, questions: list[QuestionView]) -> None: ...

    async def get_latest(
        self, document_id: str, question_id: str
    ) -> StoredQuestionVersion: ...

    async def create_revision(
        self,
        *,
        document_id: str,
        question_id: str,
        base_version_id: UUID,
        revision_source: str,
        question: QuestionView,
        change_log: QuestionChangeLog,
    ) -> StoredQuestionVersion: ...


class InMemoryQuestionReviewRepository:
    def __init__(self) -> None:
        self._reviews: dict[tuple[str, str], list[StoredQuestionReview]] = {}

    async def save(self, review: ReviewedQuestion) -> UUID:
        review_id = UUID(review.review_id) if review.review_id else uuid4()
        self._reviews.setdefault((review.document_id, review.question_id), []).append(
            StoredQuestionReview(review_id=review_id, review=review)
        )
        return review_id

    async def get_latest(
        self, document_id: str, question_id: str
    ) -> StoredQuestionReview:
        reviews = self._reviews.get((document_id, question_id))
        if not reviews:
            raise ReviewNotFoundError((document_id, question_id))
        return reviews[-1]

    async def get_latest_for_version(
        self, document_id: str, question_id: str, question_version_id: UUID
    ) -> StoredQuestionReview:
        reviews = self._reviews.get((document_id, question_id), [])
        for stored in reversed(reviews):
            if stored.review.question_version_id == str(question_version_id):
                return stored
        raise ReviewNotFoundError((document_id, question_id, question_version_id))
