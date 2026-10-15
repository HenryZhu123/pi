from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from ..domain.models import ExamDocument
from ..integrations.libreoffice import LibreOfficeConverter
from ..integrations.mineru_agent import (
    MAX_FILE_BYTES,
    MinerUAgentClient,
    MinerULimitError,
)
from ..parsing.normalizer import ExamDocumentNormalizer
from ..parsing.structure_extractor import StructureExtractor
from .document_store import ExamDocumentStore
from .persistence import QuestionVersionRepository
from .question_service import QuestionService


class UnsupportedDocumentError(ValueError):
    pass


class DocumentParseService:
    def __init__(
        self,
        *,
        mineru: MinerUAgentClient,
        structure_extractor: StructureExtractor,
        normalizer: ExamDocumentNormalizer,
        store: ExamDocumentStore,
        word_converter: LibreOfficeConverter,
        question_service: QuestionService | None = None,
        version_repository: QuestionVersionRepository | None = None,
    ) -> None:
        self.mineru = mineru
        self.structure_extractor = structure_extractor
        self.normalizer = normalizer
        self.store = store
        self.word_converter = word_converter
        self.question_service = question_service
        self.version_repository = version_repository

    async def parse(self, file_bytes: bytes, file_name: str) -> ExamDocument:
        safe_name = Path(file_name).name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in {".pdf", ".doc", ".docx"}:
            raise UnsupportedDocumentError(
                "Only PDF, DOC, and DOCX files are supported"
            )
        if not file_bytes:
            raise UnsupportedDocumentError("Uploaded document is empty")

        pdf_bytes = file_bytes
        pdf_name = safe_name
        if suffix in {".doc", ".docx"}:
            pdf_bytes = await asyncio.to_thread(
                self.word_converter.convert_to_pdf, file_bytes, safe_name
            )
            pdf_name = f"{Path(safe_name).stem}.pdf"
        if len(pdf_bytes) > MAX_FILE_BYTES:
            raise MinerULimitError(
                "Converted PDF exceeds MinerU Agent API's 10 MB limit"
            )
        page_count = await asyncio.to_thread(self._pdf_page_count, pdf_bytes)
        if page_count > 20:
            raise MinerULimitError(
                f"PDF contains {page_count} pages; MinerU Agent API accepts at most 20"
            )

        extracted = await self.mineru.parse_file(pdf_bytes, pdf_name)
        raw_tree = await self.structure_extractor.extract(extracted.markdown)
        document = self.normalizer.normalize(
            raw_tree,
            source_file_name=safe_name,
            provider_task_id=extracted.provider_task_id,
            markdown=extracted.markdown,
        )
        await self.store.save(document)
        if self.version_repository is not None and self.question_service is not None:
            await self.version_repository.initialize(
                self.question_service.list_questions(document)
            )
        return document

    @staticmethod
    def _pdf_page_count(pdf_bytes: bytes) -> int:
        try:
            return len(PdfReader(BytesIO(pdf_bytes)).pages)
        except Exception as exc:
            raise UnsupportedDocumentError(f"Invalid or unreadable PDF: {exc}") from exc
