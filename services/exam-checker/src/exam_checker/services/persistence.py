from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from ..domain.models import ReviewedQuestion


class ReviewNotFoundError(KeyError):
    pass


class QuestionAlreadyStoredError(ValueError):
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


class QuestionBankRepository(Protocol):
    async def store(
        self,
        *,
        record_id: UUID,
        source_review_id: UUID,
        document_id: str,
        question_id: str,
        question_json: dict[str, Any],
    ) -> None: ...
