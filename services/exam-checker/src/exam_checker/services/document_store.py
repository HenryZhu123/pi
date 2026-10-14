from __future__ import annotations

import asyncio
from typing import Protocol

from ..domain.models import ExamDocument


class DocumentNotFoundError(KeyError):
    pass


class ExamDocumentStore(Protocol):
    async def save(self, document: ExamDocument) -> None: ...

    async def get(self, document_id: str) -> ExamDocument: ...


class InMemoryExamDocumentStore:
    """MVP store. Replace with persistent storage before multi-process deployment."""

    def __init__(self) -> None:
        self._documents: dict[str, ExamDocument] = {}
        self._lock = asyncio.Lock()

    async def save(self, document: ExamDocument) -> None:
        async with self._lock:
            self._documents[document.document_id] = document

    async def get(self, document_id: str) -> ExamDocument:
        async with self._lock:
            document = self._documents.get(document_id)
        if document is None:
            raise DocumentNotFoundError(document_id)
        return document
