from __future__ import annotations

import asyncio

from ..domain.models import ExamDocument, ReviewedDocument, ReviewedQuestion
from .question_review_service import QuestionReviewService
from .question_service import QuestionService
from .question_workflow_service import QuestionWorkflowService


class DocumentReviewService:
    def __init__(
        self,
        *,
        question_service: QuestionService,
        question_review_service: QuestionReviewService,
        question_workflow_service: QuestionWorkflowService | None = None,
    ) -> None:
        self.question_service = question_service
        self.question_review_service = question_review_service
        self.question_workflow_service = question_workflow_service

    async def review_document(self, document: ExamDocument) -> ReviewedDocument:
        questions = self.question_service.list_questions(document)
        reviews = await asyncio.gather(
            *(
                self._review_question(document, question.question_id)
                for question in questions
            )
        )
        statuses = {review.review.status for review in reviews}
        if "UNRESOLVED" in statuses:
            status = "UNRESOLVED"
        elif "NEEDS_REVISION" in statuses:
            status = "NEEDS_REVISION"
        else:
            status = "PASS"
        return ReviewedDocument(
            document_id=document.document_id,
            status=status,
            questions=reviews,
        )

    async def _review_question(
        self, document: ExamDocument, question_id: str
    ) -> ReviewedQuestion:
        if self.question_workflow_service is not None:
            return await self.question_workflow_service.review_latest(
                document.document_id, question_id
            )
        question = self.question_service.get_question(document, question_id)
        payload = self.question_service.to_checker_payload(document, question_id)
        return await self.question_review_service.review_question(
            document_id=document.document_id,
            question_id=question_id,
            question_number=question.question_number,
            payload=payload,
        )
