from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import FastAPI, File, HTTPException, Query, UploadFile

from ..config import Settings
from ..database import (
    Database,
    PostgresExamDocumentStore,
    PostgresQuestionBankRepository,
    PostgresQuestionReviewRepository,
    PostgresQuestionVersionRepository,
)
from ..domain.models import (
    CheckerPayload,
    ExamDocument,
    IngestionResult,
    QuestionBankSearchResult,
    QuestionState,
    QuestionView,
    ReviseQuestionRequest,
    ReviewedDocument,
    ReviewedQuestion,
    StoredQuestionBankEntry,
)
from ..integrations.libreoffice import LibreOfficeConverter, LibreOfficeError
from ..integrations.mineru_agent import (
    MAX_FILE_BYTES,
    MinerUAgentClient,
    MinerUError,
    MinerULimitError,
)
from ..integrations.openrouter_decisions import OpenRouterJevClient
from ..integrations.specialist_reviewer import OpenAICompatibleSpecialistReviewer
from ..integrations.question_workflow_model import (
    OpenAICompatibleQuestionWorkflowModel,
    QuestionWorkflowModelError,
)
from ..parsing.normalizer import ExamDocumentNormalizer, NormalizationError
from ..parsing.structure_extractor import (
    OpenAICompatibleStructureExtractor,
    StructureExtractionError,
)
from ..prompts.repositories import (
    JevPromptRepository,
    ReviewPromptRepository,
    SinglePromptRepository,
)
from ..services.document_parse_service import (
    DocumentParseService,
    UnsupportedDocumentError,
)
from ..services.document_review_service import DocumentReviewService
from ..services.document_store import DocumentNotFoundError, InMemoryExamDocumentStore
from ..services.persistence import (
    InMemoryQuestionReviewRepository,
    QuestionAlreadyStoredError,
    QuestionBankEntryNotFoundError,
    QuestionVersionConflictError,
    QuestionVersionNotFoundError,
    ReviewNotFoundError,
)
from ..services.question_bank_service import (
    QuestionBankService,
    QuestionBankValidationError,
    ReviewNotPassedError,
)
from ..services.question_service import QuestionNotFoundError, QuestionService
from ..services.question_state_service import QuestionStateService
from ..services.question_review_service import QuestionReviewService
from ..services.question_version_store import InMemoryQuestionVersionRepository
from ..services.question_workflow_service import (
    QuestionIdentityError,
    QuestionRevisionNotAllowedError,
    QuestionWorkflowService,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    mineru = MinerUAgentClient(
        resolved.mineru_base_url,
        timeout_seconds=resolved.mineru_timeout_seconds,
        poll_interval_seconds=resolved.mineru_poll_interval_seconds,
        max_retries=resolved.mineru_max_retries,
    )
    extractor = OpenAICompatibleStructureExtractor(
        resolved.llm_base_url,
        resolved.llm_api_key,
        resolved.llm_model,
        timeout_seconds=resolved.llm_timeout_seconds,
    )
    database = Database(resolved.database_url) if resolved.database_url else None
    if database is None:
        store = InMemoryExamDocumentStore()
        review_repository = InMemoryQuestionReviewRepository()
        version_repository = InMemoryQuestionVersionRepository()
        question_bank_repository = None
        question_bank_service = None
    else:
        store = PostgresExamDocumentStore(database.sessions)
        review_repository = PostgresQuestionReviewRepository(database.sessions)
        version_repository = PostgresQuestionVersionRepository(database.sessions)
        question_bank_repository = PostgresQuestionBankRepository(database.sessions)
        question_bank_service = QuestionBankService(
            review_repository=review_repository,
            question_bank_repository=question_bank_repository,
        )
    question_service = QuestionService()
    jev_prompts = JevPromptRepository(resolved.jev_prompt_root)
    review_prompts = ReviewPromptRepository(resolved.review_prompt_root)
    jev_client = OpenRouterJevClient(
        resolved.jev_base_url,
        resolved.jev_api_key,
        resolved.jev_model,
        jev_prompts,
        timeout_seconds=resolved.jev_timeout_seconds,
        max_concurrency=resolved.jev_max_concurrency,
    )
    specialist_reviewer = OpenAICompatibleSpecialistReviewer(
        resolved.review_llm_base_url,
        resolved.review_llm_api_key,
        resolved.review_llm_model,
        review_prompts,
        timeout_seconds=resolved.review_llm_timeout_seconds,
        max_concurrency=resolved.review_llm_max_concurrency,
        max_attempts=resolved.review_llm_max_attempts,
    )
    review_service = QuestionReviewService(
        router=jev_client,
        reviewer=specialist_reviewer,
        jev_prompts=jev_prompts,
        review_repository=review_repository,
    )
    workflow_model = OpenAICompatibleQuestionWorkflowModel(
        resolved.workflow_llm_base_url,
        resolved.workflow_llm_api_key,
        resolved.workflow_llm_model,
        revision_prompts=SinglePromptRepository(resolved.revision_prompt_root),
        metadata_prompts=SinglePromptRepository(resolved.metadata_prompt_root),
        timeout_seconds=resolved.workflow_llm_timeout_seconds,
        max_concurrency=resolved.workflow_llm_max_concurrency,
    )
    question_workflow_service = QuestionWorkflowService(
        version_repository=version_repository,
        review_repository=review_repository,
        review_service=review_service,
        model=workflow_model,
        question_bank_service=question_bank_service,
    )
    document_review_service = DocumentReviewService(
        question_service=question_service,
        question_review_service=review_service,
        question_workflow_service=question_workflow_service,
    )
    parse_service = DocumentParseService(
        mineru=mineru,
        structure_extractor=extractor,
        normalizer=ExamDocumentNormalizer(),
        store=store,
        word_converter=LibreOfficeConverter(),
        question_service=question_service,
        version_repository=version_repository,
    )
    question_state_service = QuestionStateService(
        version_repository=version_repository,
        review_repository=review_repository,
        question_bank_repository=question_bank_repository,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await mineru.close()
        await extractor.close()
        await jev_client.close()
        await specialist_reviewer.close()
        await workflow_model.close()
        if database is not None:
            await database.close()

    application = FastAPI(
        title="Exam Checker Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/v1/exam-checker/documents/parse", response_model=ExamDocument)
    async def parse_document(file: UploadFile = File(...)) -> ExamDocument:
        try:
            content = await file.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise MinerULimitError(
                    "Uploaded file exceeds the 10 MB MinerU Agent API limit"
                )
            return await parse_service.parse(content, file.filename or "document")
        except MinerULimitError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except (UnsupportedDocumentError, NormalizationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except LibreOfficeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (MinerUError, StructureExtractionError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            await file.close()

    @application.get(
        "/v1/exam-checker/documents/{document_id}", response_model=ExamDocument
    )
    async def get_document(document_id: str) -> ExamDocument:
        try:
            return await store.get(document_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc

    @application.get(
        "/v1/exam-checker/documents/{document_id}/questions",
        response_model=list[QuestionView],
    )
    async def list_questions(document_id: str) -> list[QuestionView]:
        try:
            document = await store.get(document_id)
            original = question_service.list_questions(document)
            return [
                (
                    await version_repository.get_latest(
                        document_id, question.question_id
                    )
                ).question
                for question in original
            ]
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc

    @application.get(
        "/v1/exam-checker/documents/{document_id}/questions/{question_id}",
        response_model=QuestionView,
    )
    async def get_question(document_id: str, question_id: str) -> QuestionView:
        try:
            await store.get(document_id)
            return (
                await version_repository.get_latest(document_id, question_id)
            ).question
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc
        except QuestionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Question not found") from exc
        except QuestionVersionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Question not found") from exc

    @application.get(
        "/v1/exam-checker/documents/{document_id}/questions/{question_id}/state",
        response_model=QuestionState,
    )
    async def get_question_state(
        document_id: str, question_id: str
    ) -> QuestionState:
        try:
            await store.get(document_id)
            return await question_state_service.get(document_id, question_id)
        except (DocumentNotFoundError, QuestionVersionNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="Question not found") from exc

    @application.get(
        "/v1/exam-checker/documents/{document_id}/questions/{question_id}/checker-payload",
        response_model=CheckerPayload,
    )
    async def get_checker_payload(document_id: str, question_id: str) -> CheckerPayload:
        try:
            await store.get(document_id)
            version = await version_repository.get_latest(document_id, question_id)
            return question_service.to_checker_payload_from_view(version.question)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc
        except QuestionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Question not found") from exc
        except QuestionVersionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Question not found") from exc

    @application.post(
        "/v1/exam-checker/documents/{document_id}/review",
        response_model=ReviewedDocument,
    )
    async def review_document(document_id: str) -> ReviewedDocument:
        try:
            document = await store.get(document_id)
            return await application.state.document_review_service.review_document(
                document
            )
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc

    @application.post(
        "/v1/exam-checker/documents/{document_id}/questions/{question_id}/review",
        response_model=ReviewedQuestion,
    )
    async def review_question(document_id: str, question_id: str) -> ReviewedQuestion:
        try:
            await store.get(document_id)
            return await application.state.question_workflow_service.review_latest(
                document_id, question_id
            )
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc
        except QuestionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Question not found") from exc
        except QuestionVersionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Question not found") from exc

    @application.post(
        "/v1/exam-checker/documents/{document_id}/questions/{question_id}/revise",
        response_model=ReviewedQuestion,
    )
    async def revise_question(
        document_id: str,
        question_id: str,
        request: ReviseQuestionRequest,
    ) -> ReviewedQuestion:
        try:
            await store.get(document_id)
            return await application.state.question_workflow_service.revise_and_review(
                document_id=document_id,
                question_id=question_id,
                base_version_id=UUID(request.base_version_id),
                question_json=request.question_json,
                user_instruction=request.user_instruction,
            )
        except QuestionVersionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except QuestionRevisionNotAllowedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (DocumentNotFoundError, QuestionVersionNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="Question not found") from exc
        except ReviewNotFoundError as exc:
            raise HTTPException(
                status_code=409, detail="Review the question before requesting AI revision"
            ) from exc
        except QuestionWorkflowModelError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except (ValueError, QuestionIdentityError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.post(
        "/v1/exam-checker/documents/{document_id}/questions/{question_id}/ingest",
        response_model=IngestionResult,
    )
    async def ingest_question(
        document_id: str,
        question_id: str,
        question_json: dict[str, Any],
    ) -> IngestionResult:
        if question_bank_service is None:
            raise HTTPException(
                status_code=503, detail="PostgreSQL persistence is not configured"
            )
        try:
            return await question_bank_service.ingest(
                document_id=document_id,
                question_id=question_id,
                question_json=question_json,
            )
        except ReviewNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="No review exists for this question"
            ) from exc
        except ReviewNotPassedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except QuestionAlreadyStoredError as exc:
            raise HTTPException(
                status_code=409, detail="Question is already stored"
            ) from exc
        except QuestionBankValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get(
        "/v1/question-bank/search", response_model=QuestionBankSearchResult
    )
    async def search_question_bank(
        subject: str | None = None,
        question_type: str | None = None,
        difficulty: str | None = None,
        knowledge_point: str | None = None,
        keyword: str | None = None,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> QuestionBankSearchResult:
        if question_bank_service is None:
            raise HTTPException(
                status_code=503, detail="PostgreSQL persistence is not configured"
            )
        return await question_bank_service.search(
            subject=subject,
            question_type=question_type,
            difficulty=difficulty,
            knowledge_point=knowledge_point,
            keyword=keyword,
            limit=limit,
        )

    @application.get(
        "/v1/question-bank/{record_id}",
        response_model=StoredQuestionBankEntry,
    )
    async def get_question_bank_entry(record_id: UUID) -> StoredQuestionBankEntry:
        if question_bank_service is None:
            raise HTTPException(
                status_code=503, detail="PostgreSQL persistence is not configured"
            )
        try:
            return await question_bank_service.get(record_id)
        except QuestionBankEntryNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Question not found") from exc

    application.state.parse_service = parse_service
    application.state.document_store = store
    application.state.question_service = question_service
    application.state.review_service = review_service
    application.state.document_review_service = document_review_service
    application.state.database = database
    application.state.question_bank_service = question_bank_service
    application.state.question_workflow_service = question_workflow_service
    application.state.question_state_service = question_state_service
    application.state.version_repository = version_repository
    return application


app = create_app()
