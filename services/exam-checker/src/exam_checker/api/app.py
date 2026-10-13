from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile

from ..config import Settings
from ..domain.models import CheckerPayload, ExamDocument, QuestionView, ReviewedQuestion
from ..integrations.libreoffice import LibreOfficeConverter, LibreOfficeError
from ..integrations.mineru_agent import (
    MAX_FILE_BYTES,
    MinerUAgentClient,
    MinerUError,
    MinerULimitError,
)
from ..integrations.openrouter_decisions import OpenRouterJevClient
from ..integrations.specialist_reviewer import OpenAICompatibleSpecialistReviewer
from ..parsing.normalizer import ExamDocumentNormalizer, NormalizationError
from ..parsing.structure_extractor import (
    OpenAICompatibleStructureExtractor,
    StructureExtractionError,
)
from ..prompts.repositories import JevPromptRepository, ReviewPromptRepository
from ..services.document_parse_service import (
    DocumentParseService,
    UnsupportedDocumentError,
)
from ..services.document_store import DocumentNotFoundError, InMemoryExamDocumentStore
from ..services.question_service import QuestionNotFoundError, QuestionService
from ..services.question_review_service import QuestionReviewService


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
    store = InMemoryExamDocumentStore()
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
        max_retries=resolved.review_llm_max_retries,
    )
    review_service = QuestionReviewService(
        router=jev_client,
        reviewer=specialist_reviewer,
        jev_prompts=jev_prompts,
    )
    parse_service = DocumentParseService(
        mineru=mineru,
        structure_extractor=extractor,
        normalizer=ExamDocumentNormalizer(),
        store=store,
        word_converter=LibreOfficeConverter(),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await mineru.close()
        await extractor.close()
        await jev_client.close()
        await specialist_reviewer.close()

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
            return question_service.list_questions(document)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc

    @application.get(
        "/v1/exam-checker/documents/{document_id}/questions/{question_id}",
        response_model=QuestionView,
    )
    async def get_question(document_id: str, question_id: str) -> QuestionView:
        try:
            document = await store.get(document_id)
            return question_service.get_question(document, question_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc
        except QuestionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Question not found") from exc

    @application.get(
        "/v1/exam-checker/documents/{document_id}/questions/{question_id}/checker-payload",
        response_model=CheckerPayload,
    )
    async def get_checker_payload(document_id: str, question_id: str) -> CheckerPayload:
        try:
            document = await store.get(document_id)
            return question_service.to_checker_payload(document, question_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc
        except QuestionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Question not found") from exc

    @application.post(
        "/v1/exam-checker/documents/{document_id}/questions/{question_id}/review",
        response_model=ReviewedQuestion,
    )
    async def review_question(
        document_id: str, question_id: str
    ) -> ReviewedQuestion:
        try:
            document = await store.get(document_id)
            payload = question_service.to_checker_payload(document, question_id)
            return await application.state.review_service.review_question(
                document_id=document_id,
                question_id=question_id,
                payload=payload,
            )
        except DocumentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Document not found") from exc
        except QuestionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Question not found") from exc

    application.state.parse_service = parse_service
    application.state.document_store = store
    application.state.question_service = question_service
    application.state.review_service = review_service
    return application


app = create_app()
