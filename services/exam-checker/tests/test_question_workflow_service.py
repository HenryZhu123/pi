from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from exam_checker.domain.models import (
    CheckerPayload,
    QuestionMetadata,
    QuestionReview,
    QuestionRevisionResult,
    QuestionView,
    ReviewedQuestion,
    ReviewError,
    ReviewSummary,
    ReviewTiming,
)
from exam_checker.services.persistence import InMemoryQuestionReviewRepository
from exam_checker.services.question_version_store import (
    InMemoryQuestionVersionRepository,
)
from exam_checker.services.question_workflow_service import QuestionWorkflowService


def make_question() -> QuestionView:
    return QuestionView(
        document_id="exam_1",
        question_id="question_1",
        question_number="1",
        question_type="single_choice",
        question_text="以下哪项错吴？",
        options=["A. 甲", "B. 乙"],
        answer_text="A",
        points=2,
        subject="计算机",
        paper_title="测试卷",
        section_titles=["选择题"],
        numbering_path=["1", "1"],
        numbering_description="第1大题-第1小题",
        reference="",
    )


class FakeModel:
    model = "revision-model"
    received_errors: list[ReviewError] = []

    async def revise(
        self, question, errors, user_instruction, proposed_question=None
    ):
        self.received_errors = errors
        assert user_instruction == "保持原题难度"
        revised = proposed_question or question.model_copy(
            update={"question_text": "以下哪项错误？"}
        )
        return QuestionRevisionResult(
            question=revised,
            changes="将题干中的‘错吴’修改为‘错误’，修正错别字。",
        )

    async def annotate(self, question):
        return QuestionMetadata(
            difficulty="easy", knowledge_points=["基础概念"]
        )


class PassingReviewService:
    def __init__(self, repository) -> None:
        self.repository = repository
        self.calls: list[str] = []

    async def review_question(
        self,
        *,
        document_id,
        question_id,
        question_number,
        payload,
        question_version_id=None,
    ):
        self.calls.append(question_version_id)
        review = ReviewedQuestion(
            document_id=document_id,
            question_id=question_id,
            question_number=question_number,
            question_version_id=question_version_id,
            review_id=str(uuid4()),
            question=payload,
            review=QuestionReview(
                status="PASS",
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
        await self.repository.save(review)
        return review


@pytest.mark.asyncio
async def test_ai_revision_creates_new_version_then_reviews_it() -> None:
    question = make_question()
    versions = InMemoryQuestionVersionRepository()
    await versions.initialize([question])
    version_1 = await versions.get_latest("exam_1", "question_1")
    reviews = InMemoryQuestionReviewRepository()
    prior = ReviewedQuestion(
        document_id="exam_1",
        question_id="question_1",
        question_number="1",
        question_version_id=version_1.version_id,
        review_id=str(uuid4()),
        question=CheckerPayload(reference="", detection_content=question.question_text),
        review=QuestionReview(
            status="NEEDS_REVISION",
            checks=[],
            summary=ReviewSummary(
                has_error=True,
                total_errors=1,
                errors=[
                    ReviewError(
                        error_type="错别字",
                        original_text="错吴",
                        correction="错误",
                    )
                ],
                unresolved_checks=[],
            ),
            timing_ms=ReviewTiming(routing=0, specialist_checks=0, total=1),
        ),
    )
    await reviews.save(prior)
    model = FakeModel()
    reviewer = PassingReviewService(reviews)
    workflow = QuestionWorkflowService(
        version_repository=versions,
        review_repository=reviews,
        review_service=reviewer,
        model=model,
        question_bank_service=None,
    )

    result = await workflow.revise_and_review(
        document_id="exam_1",
        question_id="question_1",
        base_version_id=UUID(version_1.version_id),
        question_json=None,
        user_instruction="保持原题难度",
    )

    version_2 = await versions.get_latest("exam_1", "question_1")
    assert version_2.version_number == 2
    assert version_2.parent_version_id == version_1.version_id
    assert version_2.revision_source == "AI_EDIT"
    assert version_2.question.question_text == "以下哪项错误？"
    assert version_2.change_log is not None
    assert set(version_2.change_log.model_dump()) == {
        "user_instruction",
        "review_feedback",
        "changes",
        "model",
    }
    assert version_2.change_log.user_instruction == "保持原题难度"
    assert version_2.change_log.review_feedback[0].error_type == "错别字"
    assert version_2.change_log.changes == (
        "将题干中的‘错吴’修改为‘错误’，修正错别字。"
    )
    assert version_2.change_log.model == "revision-model"
    assert reviewer.calls == [version_2.version_id]
    assert model.received_errors[0].error_type == "错别字"
    assert result.review.status == "PASS"
    assert result.ingestion is not None
    assert result.ingestion.status == "SKIPPED"
