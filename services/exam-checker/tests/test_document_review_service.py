from __future__ import annotations

from exam_checker.domain.models import (
    QuestionReview,
    ReviewedQuestion,
    ReviewSummary,
    ReviewTiming,
)
from exam_checker.parsing.normalizer import ExamDocumentNormalizer
from exam_checker.services.document_review_service import DocumentReviewService
from exam_checker.services.question_service import QuestionService


class StatusReviewService:
    async def review_question(
        self, *, document_id, question_id, question_number, payload
    ) -> ReviewedQuestion:
        status = "UNRESOLVED" if question_id == "question_2" else "PASS"
        return ReviewedQuestion(
            document_id=document_id,
            question_id=question_id,
            question_number=question_number,
            question=payload,
            review=QuestionReview(
                status=status,
                routing={},
                checks=[],
                summary=ReviewSummary(
                    has_error=False,
                    total_errors=0,
                    errors=[],
                    unresolved_checks=(
                        ["logic_check"] if status == "UNRESOLVED" else []
                    ),
                ),
                timing_ms=ReviewTiming(routing=0, specialist_checks=0, total=1),
            ),
        )


async def test_document_review_aggregates_every_question_and_status() -> None:
    document = ExamDocumentNormalizer().normalize(
        {
            "paper": {"title": "测试卷", "subject": "计算机"},
            "questions": [
                {
                    "id": 1,
                    "title": "选择题",
                    "children": [
                        {"id": 1, "content": "第一题"},
                        {"id": 2, "content": "第二题"},
                    ],
                }
            ],
        },
        source_file_name="exam.pdf",
        provider_task_id="task",
        markdown="text",
    )
    service = DocumentReviewService(
        question_service=QuestionService(),
        question_review_service=StatusReviewService(),
    )

    result = await service.review_document(document)

    assert result.status == "UNRESOLVED"
    assert [question.question_id for question in result.questions] == [
        "question_1",
        "question_2",
    ]
