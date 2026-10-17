from __future__ import annotations

from typing import Protocol
from uuid import UUID

from ..domain.models import (
    IngestionResult,
    QuestionChangeLog,
    QuestionMetadata,
    QuestionRevisionResult,
    QuestionView,
    ReviewedQuestion,
    ReviewError,
    StoredQuestionVersion,
)
from .persistence import QuestionReviewRepository, QuestionVersionRepository
from .question_bank_service import QuestionBankService
from .question_review_service import QuestionReviewService
from .question_service import QuestionService


class QuestionRevisionNotAllowedError(ValueError):
    pass


class QuestionIdentityError(ValueError):
    pass


class QuestionWorkflowModel(Protocol):
    model: str

    async def revise(
        self,
        question: QuestionView,
        errors: list[ReviewError],
        user_instruction: str,
        proposed_question: QuestionView | None = None,
    ) -> QuestionRevisionResult: ...

    async def annotate(self, question: QuestionView) -> QuestionMetadata: ...


class QuestionWorkflowService:
    def __init__(
        self,
        *,
        version_repository: QuestionVersionRepository,
        review_repository: QuestionReviewRepository,
        review_service: QuestionReviewService,
        model: QuestionWorkflowModel,
        question_bank_service: QuestionBankService | None,
    ) -> None:
        self.version_repository = version_repository
        self.review_repository = review_repository
        self.review_service = review_service
        self.model = model
        self.question_bank_service = question_bank_service

    async def review_latest(
        self, document_id: str, question_id: str
    ) -> ReviewedQuestion:
        version = await self.version_repository.get_latest(document_id, question_id)
        return await self._review_version(version)

    async def revise_and_review(
        self,
        *,
        document_id: str,
        question_id: str,
        base_version_id: UUID,
        question_json: dict[str, object] | None,
        user_instruction: str,
    ) -> ReviewedQuestion:
        current = await self.version_repository.get_latest(document_id, question_id)
        if current.version_id != str(base_version_id):
            from .persistence import QuestionVersionConflictError

            raise QuestionVersionConflictError(
                "The question changed after the requested base version"
            )
        stored_review = await self.review_repository.get_latest(
            document_id, question_id
        )
        if stored_review.review.review.status != "NEEDS_REVISION":
            raise QuestionRevisionNotAllowedError(
                "Revision requires a latest NEEDS_REVISION review"
            )
        review_feedback = stored_review.review.review.summary.errors
        proposed_question = (
            QuestionView.model_validate(question_json)
            if question_json is not None
            else None
        )
        revision_result = await self.model.revise(
            current.question,
            review_feedback,
            user_instruction,
            proposed_question,
        )
        revised = revision_result.question
        revision_source = "USER_EDIT" if proposed_question is not None else "AI_EDIT"
        self._validate_identity(current.question, revised)
        change_log = QuestionChangeLog(
            user_instruction=user_instruction,
            review_feedback=review_feedback,
            changes=revision_result.changes,
            model=self.model.model,
        )
        version = await self.version_repository.create_revision(
            document_id=document_id,
            question_id=question_id,
            base_version_id=base_version_id,
            revision_source=revision_source,
            question=revised,
            change_log=change_log,
        )
        return await self._review_version(version)

    async def _review_version(
        self, version: StoredQuestionVersion
    ) -> ReviewedQuestion:
        question = version.question
        payload = QuestionService.to_checker_payload_from_view(question)
        reviewed = await self.review_service.review_question(
            document_id=question.document_id,
            question_id=question.question_id,
            question_number=question.question_number,
            payload=payload,
            question_version_id=version.version_id,
        )
        if reviewed.review.status != "PASS":
            return reviewed
        if self.question_bank_service is None:
            return reviewed.model_copy(
                update={
                    "ingestion": IngestionResult(
                        status="SKIPPED", error_code="DATABASE_NOT_CONFIGURED"
                    )
                }
            )
        try:
            metadata = await self.model.annotate(question)
            bank_json = {
                "document_id": question.document_id,
                "question_id": question.question_id,
                "question": question.model_dump(mode="json"),
                "review_status": "PASS",
                "metadata": metadata.model_dump(mode="json"),
            }
            ingestion = await self.question_bank_service.ingest(
                document_id=question.document_id,
                question_id=question.question_id,
                question_json=bank_json,
                source_version_id=UUID(version.version_id),
            )
        except Exception as exc:
            ingestion = IngestionResult(
                status="FAILED", error_code=type(exc).__name__
            )
        return reviewed.model_copy(update={"ingestion": ingestion})

    @staticmethod
    def _validate_identity(original: QuestionView, revised: QuestionView) -> None:
        if revised.document_id != original.document_id:
            raise QuestionIdentityError("document_id cannot be changed")
        if revised.question_id != original.question_id:
            raise QuestionIdentityError("question_id cannot be changed")
