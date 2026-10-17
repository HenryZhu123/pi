from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from ..domain.models import QuestionChangeLog, QuestionView, StoredQuestionVersion
from .persistence import QuestionVersionConflictError, QuestionVersionNotFoundError


class InMemoryQuestionVersionRepository:
    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], list[StoredQuestionVersion]] = {}
        self._lock = asyncio.Lock()

    async def initialize(self, questions: list[QuestionView]) -> None:
        async with self._lock:
            for question in questions:
                key = (question.document_id, question.question_id)
                if key in self._versions:
                    continue
                self._versions[key] = [
                    StoredQuestionVersion(
                        version_id=str(uuid4()),
                        document_id=question.document_id,
                        question_id=question.question_id,
                        version_number=1,
                        revision_source="PARSED",
                        question=question,
                    )
                ]

    async def get_latest(
        self, document_id: str, question_id: str
    ) -> StoredQuestionVersion:
        versions = self._versions.get((document_id, question_id))
        if not versions:
            raise QuestionVersionNotFoundError((document_id, question_id))
        return versions[-1]

    async def create_revision(
        self,
        *,
        document_id: str,
        question_id: str,
        base_version_id: UUID,
        revision_source: str,
        question: QuestionView,
        change_log: QuestionChangeLog,
    ) -> StoredQuestionVersion:
        async with self._lock:
            versions = self._versions.get((document_id, question_id))
            if not versions:
                raise QuestionVersionNotFoundError((document_id, question_id))
            latest = versions[-1]
            if latest.version_id != str(base_version_id):
                raise QuestionVersionConflictError(
                    "The question changed after the requested base version"
                )
            version = StoredQuestionVersion(
                version_id=str(uuid4()),
                document_id=document_id,
                question_id=question_id,
                version_number=latest.version_number + 1,
                parent_version_id=latest.version_id,
                revision_source=revision_source,
                question=question,
                change_log=change_log,
            )
            versions.append(version)
            return version
