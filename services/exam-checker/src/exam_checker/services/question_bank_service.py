from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from ..domain.models import IngestionResult
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

    async def ingest(
        self,
        *,
        document_id: str,
        question_id: str,
        question_json: dict[str, Any],
    ) -> IngestionResult:
        stored_review = await self.review_repository.get_latest(
            document_id, question_id
        )
        if stored_review.review.review.status != "PASS":
            raise ReviewNotPassedError(
                "Only a question whose latest review passed can be stored"
            )
        if question_json.get("question_id") != question_id:
            raise QuestionBankValidationError(
                "question_json.question_id must match the requested question"
            )
        if question_json.get("review_status") != "PASS":
            raise QuestionBankValidationError(
                "question_json.review_status must be PASS"
            )
        question_content = question_json.get("question_content")
        if not isinstance(question_content, str) or not question_content.strip():
            raise QuestionBankValidationError(
                "question_json.question_content must be a non-empty string"
            )
        difficulty = question_json.get("difficulty")
        if not isinstance(difficulty, str) or not difficulty.strip():
            raise QuestionBankValidationError(
                "question_json.difficulty must be a non-empty string"
            )
        knowledge_points = question_json.get("knowledge_points")
        if (
            not isinstance(knowledge_points, list)
            or not knowledge_points
            or any(
                not isinstance(item, str) or not item.strip()
                for item in knowledge_points
            )
        ):
            raise QuestionBankValidationError(
                "question_json.knowledge_points must be an array of strings"
            )

        record_id = uuid4()
        stored_json = deepcopy(question_json)
        stored_json["ingestion"] = {
            "status": "STORED",
            "record_id": str(record_id),
        }
        await self.question_bank_repository.store(
            record_id=record_id,
            source_review_id=stored_review.review_id,
            document_id=document_id,
            question_id=question_id,
            question_json=stored_json,
        )
        return IngestionResult(status="STORED", record_id=str(record_id))
