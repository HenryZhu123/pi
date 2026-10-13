from __future__ import annotations

import asyncio
import json
import re
import time

import httpx
from pydantic import ValidationError

from ..domain.models import CheckerPayload, ReviewTaskId, SpecialistReviewResult
from ..prompts.repositories import ReviewPromptRepository


class SpecialistReviewError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        retryable: bool,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.attempts = attempts


EXPECTED_ERROR_TYPES: dict[ReviewTaskId, str] = {
    "typo_check": "错别字",
    "punctuation_check": "标点符号错误",
    "options_check": "选项错误",
    "mismatch_check": "题目与题型不一致",
    "missing_check": "信息缺失",
    "ambiguity_check": "语义不清",
    "unsolvability_check": "题目无解",
    "answer_leak_check": "答案泄露",
    "logic_check": "事实/逻辑错误",
}


class OpenAICompatibleSpecialistReviewer:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        prompts: ReviewPromptRepository,
        *,
        timeout_seconds: float = 180,
        max_concurrency: int = 8,
        max_attempts: int = 3,
        retry_base_delay_seconds: float = 1,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.prompts = prompts
        self.max_attempts = max(1, max_attempts)
        self.retry_base_delay_seconds = max(0, retry_base_delay_seconds)
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def review(
        self, task_id: ReviewTaskId, payload: CheckerPayload
    ) -> tuple[SpecialistReviewResult, int, int]:
        if not self.base_url or not self.api_key or not self.model:
            raise SpecialistReviewError(
                "EXAM_CHECKER_REVIEW_LLM_BASE_URL, "
                "EXAM_CHECKER_REVIEW_LLM_API_KEY and "
                "EXAM_CHECKER_REVIEW_LLM_MODEL are required",
                error_code="CONFIGURATION_ERROR",
                retryable=False,
            )
        system_prompt = self.prompts.system_prompt(task_id)
        user_prompt = (
            "[INPUT_PAYLOAD]\n"
            f"{json.dumps(payload.model_dump(), ensure_ascii=False)}"
        )
        started = time.perf_counter()
        last_error: SpecialistReviewError | None = None
        for attempt in range(1, self.max_attempts + 1):
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
                            "seed": 42,
                            "response_format": {"type": "json_object"},
                            "enable_thinking": False,
                            "chat_template_kwargs": {"enable_thinking": False},
                        },
                    )
                result = self._parse_response(response)
                self._validate_result(task_id, result, payload)
                latency_ms = round((time.perf_counter() - started) * 1000)
                return result, latency_ms, attempt
            except (httpx.HTTPError, SpecialistReviewError) as exc:
                if isinstance(exc, SpecialistReviewError):
                    last_error = exc
                else:
                    error_code = (
                        "UPSTREAM_TIMEOUT"
                        if isinstance(exc, httpx.TimeoutException)
                        else "UPSTREAM_NETWORK_ERROR"
                    )
                    last_error = SpecialistReviewError(
                        f"Specialist request failed: {exc}",
                        error_code=error_code,
                        retryable=True,
                    )
                last_error.attempts = attempt
                if not last_error.retryable or attempt == self.max_attempts:
                    raise last_error
                await asyncio.sleep(
                    self.retry_base_delay_seconds * (2 ** (attempt - 1))
                )
        assert last_error is not None
        raise SpecialistReviewError(
            f"Specialist review failed after {self.max_attempts} attempts: {last_error}",
            error_code=last_error.error_code,
            retryable=last_error.retryable,
            attempts=self.max_attempts,
        ) from last_error

    @staticmethod
    def _parse_response(response: httpx.Response) -> SpecialistReviewResult:
        if not response.is_success:
            status_code = response.status_code
            if status_code in {401, 403}:
                error_code = "AUTHENTICATION_FAILED"
                retryable = False
            elif status_code == 400:
                error_code = "INVALID_REQUEST"
                retryable = False
            elif status_code == 429:
                error_code = "RATE_LIMITED"
                retryable = True
            elif status_code >= 500:
                error_code = "UPSTREAM_SERVER_ERROR"
                retryable = True
            else:
                error_code = "UPSTREAM_REQUEST_FAILED"
                retryable = False
            raise SpecialistReviewError(
                f"Specialist request failed with HTTP {response.status_code}: "
                f"{response.text[:500]}",
                error_code=error_code,
                retryable=retryable,
            )
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise SpecialistReviewError(
                "Specialist response has an invalid shape",
                error_code="INVALID_MODEL_RESPONSE",
                retryable=True,
            ) from exc
        if not isinstance(content, str):
            raise SpecialistReviewError(
                "Specialist response content is not text",
                error_code="INVALID_MODEL_RESPONSE",
                retryable=True,
            )
        stripped = content.strip()
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
        try:
            return SpecialistReviewResult.model_validate_json(stripped)
        except ValidationError as exc:
            raise SpecialistReviewError(
                f"Specialist response violates the result schema: {exc}",
                error_code="INVALID_MODEL_RESPONSE",
                retryable=True,
            ) from exc

    @staticmethod
    def _validate_result(
        task_id: ReviewTaskId,
        result: SpecialistReviewResult,
        payload: CheckerPayload,
    ) -> None:
        for error in result.errors:
            if error.error_type != EXPECTED_ERROR_TYPES[task_id]:
                raise SpecialistReviewError(
                    f"Specialist returned {error.error_type!r} for {task_id}",
                    error_code="INVALID_MODEL_RESPONSE",
                    retryable=True,
                )
            if not error.original_text or error.original_text not in payload.detection_content:
                raise SpecialistReviewError(
                    "Specialist original_text is missing from detection_content",
                    error_code="INVALID_MODEL_RESPONSE",
                    retryable=True,
                )
