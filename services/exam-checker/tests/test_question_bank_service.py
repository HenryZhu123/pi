from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from exam_checker.domain.models import (
    CheckerPayload,
    QuestionReview,
    ReviewedQuestion,
    ReviewSummary,
    ReviewTiming,
    StoredQuestionBankEntry,
)
from exam_checker.services.persistence import StoredQuestionReview
from exam_checker.services.question_bank_service import (
    QuestionBankService,
    QuestionBankValidationError,
    ReviewNotPassedError,
)


def make_review(status: str = "PASS") -> ReviewedQuestion:
    return ReviewedQuestion(
        document_id="exam_1",
        question_id="question_1",
        question_number="1",
        question_version_id="00000000-0000-0000-0000-000000000001",
        question=CheckerPayload(reference="", detection_content="测试题"),
        review=QuestionReview(
            status=status,
            routing={},
            checks=[],
            summary=ReviewSummary(
                has_error=False,
                total_errors=0,
                errors=[],
                unresolved_checks=[],
            ),
            timing_ms=ReviewTiming(routing=0, specialist_checks=0, total=1),
        ),
    )


class FakeReviewRepository:
    def __init__(self, review: ReviewedQuestion) -> None:
        self.stored = StoredQuestionReview(review_id=uuid4(), review=review)

    async def save(self, review: ReviewedQuestion) -> UUID:
        raise AssertionError("ingestion must not create a review")

    async def get_latest(
        self, document_id: str, question_id: str
    ) -> StoredQuestionReview:
        assert (document_id, question_id) == ("exam_1", "question_1")
        return self.stored


class FakeQuestionBankRepository:
    stored: dict[str, Any] | None = None

    def __init__(self) -> None:
        self.entry = StoredQuestionBankEntry(
            record_id=str(uuid4()),
            document_id="exam_1",
            question_id="question_1",
            question_json={"question": {"question_text": "测试题"}},
        )

    async def store(self, **values: Any) -> None:
        self.stored = values

    async def get(self, record_id: UUID) -> StoredQuestionBankEntry:
        assert str(record_id) == self.entry.record_id
        return self.entry

    async def get_by_source(
        self, document_id: str, question_id: str
    ) -> StoredQuestionBankEntry | None:
        return self.entry

    async def search(self, **filters: Any) -> list[StoredQuestionBankEntry]:
        assert filters == {
            "subject": "Computer Science",
            "question_type": "single_choice",
            "difficulty": "medium",
            "knowledge_point": "TCP",
            "keyword": "可靠",
            "limit": 10,
        }
        return [self.entry]


@pytest.mark.asyncio
async def test_ingest_stores_complete_json_after_passed_review() -> None:
    bank_repository = FakeQuestionBankRepository()
    service = QuestionBankService(
        review_repository=FakeReviewRepository(make_review()),
        question_bank_repository=bank_repository,
    )
    question_json = {
        "document_id": "exam_1",
        "question_id": "question_1",
        "question": {"question_id": "question_1", "question_text": "测试题"},
        "metadata": {
            "difficulty": "medium",
            "knowledge_points": ["测试知识点"],
        },
        "review_status": "PASS",
    }

    result = await service.ingest(
        document_id="exam_1",
        question_id="question_1",
        question_json=question_json,
    )

    assert result.status == "STORED"
    assert bank_repository.stored is not None
    stored_json = bank_repository.stored["question_json"]
    assert stored_json["question"]["question_text"] == "测试题"
    assert stored_json["ingestion"] == {
        "status": "STORED",
        "record_id": result.record_id,
    }
    assert "ingestion" not in question_json


@pytest.mark.asyncio
async def test_ingest_rejects_question_whose_latest_review_did_not_pass() -> None:
    service = QuestionBankService(
        review_repository=FakeReviewRepository(make_review("NEEDS_REVISION")),
        question_bank_repository=FakeQuestionBankRepository(),
    )

    with pytest.raises(ReviewNotPassedError):
        await service.ingest(
            document_id="exam_1",
            question_id="question_1",
            question_json={},
        )


@pytest.mark.asyncio
async def test_ingest_requires_metadata() -> None:
    service = QuestionBankService(
        review_repository=FakeReviewRepository(make_review()),
        question_bank_repository=FakeQuestionBankRepository(),
    )

    with pytest.raises(QuestionBankValidationError, match="difficulty"):
        await service.ingest(
            document_id="exam_1",
            question_id="question_1",
            question_json={
                "document_id": "exam_1",
                "question_id": "question_1",
                "question": {"question_text": "测试题"},
                "review_status": "PASS",
                "metadata": {"knowledge_points": []},
            },
        )


@pytest.mark.asyncio
async def test_read_and_search_return_complete_stored_json() -> None:
    repository = FakeQuestionBankRepository()
    service = QuestionBankService(
        review_repository=FakeReviewRepository(make_review()),
        question_bank_repository=repository,
    )

    entry = await service.get(UUID(repository.entry.record_id))
    result = await service.search(
        subject="Computer Science",
        question_type="single_choice",
        difficulty="medium",
        knowledge_point="TCP",
        keyword="可靠",
        limit=10,
    )

    assert entry.question_json == {"question": {"question_text": "测试题"}}
    assert result.questions == [entry]
