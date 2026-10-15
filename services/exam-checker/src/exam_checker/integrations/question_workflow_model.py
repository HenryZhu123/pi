from __future__ import annotations

import asyncio
import json
import re

import httpx
from pydantic import ValidationError

from ..domain.models import QuestionMetadata, QuestionView, ReviewError
from ..prompts.repositories import SinglePromptRepository


class QuestionWorkflowModelError(RuntimeError):
    pass


class OpenAICompatibleQuestionWorkflowModel:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        revision_prompts: SinglePromptRepository,
        metadata_prompts: SinglePromptRepository,
        timeout_seconds: float = 180,
        max_concurrency: int = 8,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.revision_prompts = revision_prompts
        self.metadata_prompts = metadata_prompts
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def revise(
        self,
        question: QuestionView,
        errors: list[ReviewError],
        user_instruction: str,
    ) -> QuestionView:
        user_prompt = (
            "【原题 JSON】\n"
            f"{json.dumps(question.model_dump(mode='json'), ensure_ascii=False)}\n\n"
            "【审校修改意见】\n"
            f"{json.dumps([error.model_dump(mode='json') for error in errors], ensure_ascii=False)}\n\n"
            "【用户补充修改意见】\n"
            f"{user_instruction}"
        )
        content = await self._complete(
            self.revision_prompts.system_prompt(), user_prompt
        )
        try:
            revised = QuestionView.model_validate_json(content)
        except ValidationError as exc:
            raise QuestionWorkflowModelError(
                f"Revision response violates the question schema: {exc}"
            ) from exc
        if revised.document_id != question.document_id:
            raise QuestionWorkflowModelError("Revision changed document_id")
        if revised.question_id != question.question_id:
            raise QuestionWorkflowModelError("Revision changed question_id")
        return revised

    async def annotate(self, question: QuestionView) -> QuestionMetadata:
        user_prompt = json.dumps(question.model_dump(mode="json"), ensure_ascii=False)
        content = await self._complete(
            self.metadata_prompts.system_prompt(), user_prompt
        )
        try:
            return QuestionMetadata.model_validate_json(content)
        except ValidationError as exc:
            raise QuestionWorkflowModelError(
                f"Metadata response violates the result schema: {exc}"
            ) from exc

    async def _complete(self, system_prompt: str, user_prompt: str) -> str:
        if not self.base_url or not self.api_key or not self.model:
            raise QuestionWorkflowModelError(
                "Question workflow LLM endpoint, API key and model are required"
            )
        try:
            async with self._semaphore:
                response = await self._client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0,
                        "response_format": {"type": "json_object"},
                    },
                )
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise QuestionWorkflowModelError(
                f"Question workflow model request failed: {exc}"
            ) from exc
        if not isinstance(content, str):
            raise QuestionWorkflowModelError("Model response content is not text")
        stripped = content.strip()
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        return re.sub(r"\s*```$", "", stripped)
