import asyncio

from fastapi.testclient import TestClient

from exam_checker.api.app import app
from exam_checker.domain.models import (
    QuestionReview,
    ReviewedDocument,
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
    async def review_question(
        self, *, document_id, question_id, question_number, payload
    ):
        return ReviewedQuestion(
            document_id=document_id,
            question_id=question_id,
            question_number=question_number,
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


class FakeDocumentReviewService:
    async def review_document(self, document):
        reviewer = FakeReviewService()
        questions = []
        for question_id in ["question_1", "question_2"]:
            question = app.state.question_service.get_question(document, question_id)
            payload = app.state.question_service.to_checker_payload(
                document, question_id
            )
            questions.append(
                await reviewer.review_question(
                    document_id=document.document_id,
                    question_id=question_id,
                    question_number=question.question_number,
                    payload=payload,
                )
            )
        return ReviewedDocument(
            document_id=document.document_id,
            status="PASS",
            questions=questions,
        )


class FakeQuestionWorkflowService:
    async def review_latest(self, document_id, question_id):
        document = await app.state.document_store.get(document_id)
        question = app.state.question_service.get_question(document, question_id)
        return await FakeReviewService().review_question(
            document_id=document_id,
            question_id=question_id,
            question_number=question.question_number,
            payload=app.state.question_service.to_checker_payload_from_view(question),
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
    original_service = app.state.question_workflow_service
    app.state.question_workflow_service = FakeQuestionWorkflowService()
    asyncio.run(app.state.document_store.save(document))
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/v1/exam-checker/documents/{document.document_id}"
                "/questions/question_1/review"
            )
    finally:
        app.state.question_workflow_service = original_service

    assert response.status_code == 200
    body = response.json()
    assert body["question"]["detection_content"] == "以下哪项正确？\nA. 甲\nB. 乙"
    assert body["question_number"] == "1"
    assert body["review"]["status"] == "PASS"


def test_document_review_endpoint_reviews_all_questions() -> None:
    document = ExamDocumentNormalizer().normalize(
        {
            "paper": {"title": "测试卷", "subject": "计算机"},
            "questions": [
                {
                    "id": 1,
                    "title": "选择题",
                    "children": [
                        {"id": 1, "content": "第一题", "options": ["A. 甲"]},
                        {"id": 2, "content": "第二题", "options": ["A. 乙"]},
                    ],
                }
            ],
        },
        source_file_name="exam.pdf",
        provider_task_id="task",
        markdown="text",
    )
    original_service = app.state.document_review_service
    app.state.document_review_service = FakeDocumentReviewService()
    asyncio.run(app.state.document_store.save(document))
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/v1/exam-checker/documents/{document.document_id}/review"
            )
    finally:
        app.state.document_review_service = original_service

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == document.document_id
    assert body["status"] == "PASS"
    assert [question["question_id"] for question in body["questions"]] == [
        "question_1",
        "question_2",
    ]
