from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

from ..domain.models import (
    IngestionResult,
    QuestionBankSearchResult,
    StoredQuestionBankEntry,
)
from .persistence import QuestionBankRepository, QuestionReviewRepository


class QuestionBankValidationError(ValueError):
    pass


class ReviewNotPassedError(ValueError):
    pass


class QuestionBankService:
    def __init__(
        self,
        *,
        review_repository: QuestionReviewRepository,
        question_bank_repository: QuestionBankRepository,
    ) -> None:
        self.review_repository = review_repository
        self.question_bank_repository = question_bank_repository

    async def get(self, record_id: UUID) -> StoredQuestionBankEntry:
        return await self.question_bank_repository.get(record_id)

    async def search(
        self,
        *,
        subject: str | None = None,
        question_type: str | None = None,
        difficulty: str | None = None,
        knowledge_point: str | None = None,
        keyword: str | None = None,
        limit: int = 20,
    ) -> QuestionBankSearchResult:
        questions = await self.question_bank_repository.search(
            subject=subject,
            question_type=question_type,
            difficulty=difficulty,
            knowledge_point=knowledge_point,
            keyword=keyword,
            limit=limit,
        )
        return QuestionBankSearchResult(questions=questions)

    async def ingest(
        self,
        *,
        document_id: str,
        question_id: str,
        question_json: dict[str, Any],
        source_version_id: UUID | None = None,
    ) -> IngestionResult:
        stored_review = await self.review_repository.get_latest(
            document_id, question_id
        )
        if stored_review.review.review.status != "PASS":
            raise ReviewNotPassedError(
                "Only a question whose latest review passed can be stored"
            )
        if question_json.get("document_id") != document_id:
            raise QuestionBankValidationError(
                "question_json.document_id must match the requested document"
            )
        if question_json.get("question_id") != question_id:
            raise QuestionBankValidationError(
                "question_json.question_id must match the requested question"
            )
        if question_json.get("review_status") != "PASS":
            raise QuestionBankValidationError(
                "question_json.review_status must be PASS"
            )
        question_content = question_json.get("question")
        if not isinstance(question_content, dict) or not question_content:
            raise QuestionBankValidationError(
                "question_json.question must be a complete question JSON object"
            )
        metadata = question_json.get("metadata")
        if not isinstance(metadata, dict):
            raise QuestionBankValidationError(
                "question_json.metadata must be an object"
            )
        difficulty = metadata.get("difficulty")
        if not isinstance(difficulty, str) or not difficulty.strip():
            raise QuestionBankValidationError(
                "question_json.metadata.difficulty must be a non-empty string"
            )
        knowledge_points = metadata.get("knowledge_points")
        if (
            not isinstance(knowledge_points, list)
            or not knowledge_points
            or any(
                not isinstance(item, str) or not item.strip()
                for item in knowledge_points
            )
        ):
            raise QuestionBankValidationError(
                "question_json.metadata.knowledge_points must be an array of strings"
            )

        record_id = uuid4()
        stored_json = deepcopy(question_json)
        stored_json["ingestion"] = {
            "status": "STORED",
            "record_id": str(record_id),
        }
        resolved_version_id = source_version_id
        if resolved_version_id is None:
            if not stored_review.review.question_version_id:
                raise QuestionBankValidationError(
                    "The passed review is not linked to a question version"
                )
            resolved_version_id = UUID(stored_review.review.question_version_id)
        await self.question_bank_repository.store(
            record_id=record_id,
            source_review_id=stored_review.review_id,
            source_version_id=resolved_version_id,
            document_id=document_id,
            question_id=question_id,
            question_json=stored_json,
        )
        return IngestionResult(status="STORED", record_id=str(record_id))
