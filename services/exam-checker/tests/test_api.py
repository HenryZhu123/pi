import asyncio

from fastapi.testclient import TestClient

from exam_checker.api.app import app
from exam_checker.domain.models import (
    QuestionReview,
    ReviewedQuestion,
    ReviewSummary,
    ReviewTiming,
)
from exam_checker.parsing.normalizer import ExamDocumentNormalizer


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class FakeReviewService:
    async def review_question(self, *, document_id, question_id, payload):
        return ReviewedQuestion(
            document_id=document_id,
            question_id=question_id,
            question=payload,
            review=QuestionReview(
                status="PASS",
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


def test_review_endpoint_builds_payload_and_returns_aggregated_review() -> None:
    document = ExamDocumentNormalizer().normalize(
        {
            "paper": {"title": "测试卷", "subject": "计算机"},
            "questions": [
                {
                    "id": 1,
                    "title": "选择题",
                    "children": [
                        {
                            "id": 1,
                            "content": "以下哪项正确？",
                            "options": ["A. 甲", "B. 乙"],
                        }
                    ],
                }
            ],
        },
        source_file_name="exam.pdf",
        provider_task_id="task",
        markdown="text",
    )
    original_service = app.state.review_service
    app.state.review_service = FakeReviewService()
    asyncio.run(app.state.document_store.save(document))
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/v1/exam-checker/documents/{document.document_id}"
                "/questions/question_1/review"
            )
    finally:
        app.state.review_service = original_service

    assert response.status_code == 200
    body = response.json()
    assert body["question"]["detection_content"] == "以下哪项正确？\nA. 甲\nB. 乙"
    assert body["review"]["status"] == "PASS"
