from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfWriter

from exam_checker.integrations.mineru_agent import ExtractedDocument, MinerULimitError
from exam_checker.parsing.normalizer import ExamDocumentNormalizer
from exam_checker.services.document_parse_service import DocumentParseService
from exam_checker.services.document_store import InMemoryExamDocumentStore


def make_pdf(page_count: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class FakeMinerU:
    called = False

    async def parse_file(self, file_bytes: bytes, file_name: str) -> ExtractedDocument:
        self.called = True
        assert file_name == "exam.pdf"
        return ExtractedDocument(
            markdown="# 选择题\n1. 测试题",
            provider="mineru-agent",
            provider_task_id="task-1",
            source_file_name=file_name,
        )


class FakeExtractor:
    async def extract(self, markdown: str):
        assert markdown.startswith("# 选择题")
        return {
            "paper": {"title": "测试卷", "subject": "计算机"},
            "questions": [
                {
                    "id": 1,
                    "title": "选择题",
                    "children": [{"id": 1, "content": "测试题"}],
                }
            ],
        }


class UnexpectedWordConverter:
    def convert_to_pdf(self, file_bytes: bytes, original_filename: str) -> bytes:
        raise AssertionError("PDF input must not invoke LibreOffice")


@pytest.mark.asyncio
async def test_parse_service_builds_and_stores_exam_document() -> None:
    mineru = FakeMinerU()
    store = InMemoryExamDocumentStore()
    service = DocumentParseService(
        mineru=mineru,
        structure_extractor=FakeExtractor(),
        normalizer=ExamDocumentNormalizer(),
        store=store,
        word_converter=UnexpectedWordConverter(),
    )

    document = await service.parse(make_pdf(), "exam.pdf")

    assert mineru.called
    assert document.paper.subject == "计算机"
    assert document.nodes["question_1"].question_text == "测试题"
    assert await store.get(document.document_id) == document


@pytest.mark.asyncio
async def test_parse_service_rejects_pdf_over_twenty_pages_before_upload() -> None:
    mineru = FakeMinerU()
    service = DocumentParseService(
        mineru=mineru,
        structure_extractor=FakeExtractor(),
        normalizer=ExamDocumentNormalizer(),
        store=InMemoryExamDocumentStore(),
        word_converter=UnexpectedWordConverter(),
    )

    with pytest.raises(MinerULimitError, match="21 pages"):
        await service.parse(make_pdf(21), "exam.pdf")

    assert not mineru.called
