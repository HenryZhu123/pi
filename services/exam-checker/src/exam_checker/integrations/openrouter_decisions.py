from __future__ import annotations

import asyncio
import time

import httpx

from ..domain.models import CheckerPayload, JevPrediction, ReviewTaskId
from ..prompts.repositories import JevPromptRepository


class JevRouterError(RuntimeError):
    pass


class OpenRouterJevClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        prompts: JevPromptRepository,
        *,
        timeout_seconds: float = 30,
        max_concurrency: int = 8,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.prompts = prompts
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def predict(
        self, task_id: ReviewTaskId, payload: CheckerPayload
    ) -> JevPrediction:
        if not self.base_url or not self.api_key or not self.model:
            raise JevRouterError(
                "EXAM_CHECKER_JEV_BASE_URL, EXAM_CHECKER_JEV_API_KEY and "
                "EXAM_CHECKER_JEV_MODEL are required"
            )
        prompt = self.prompts.get(task_id)
        started = time.perf_counter()
        try:
            async with self._semaphore:
                response = await self._client.post(
                    f"{self.base_url}/decisions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "state": {
                            "routing_rules": self.prompts.routing_rules,
                            "input_payload": payload.model_dump(),
                        },
                        "questions": {
                            task_id: {
                                "type": "choice",
                                "instructions": prompt.instructions,
                                "criteria": prompt.criteria,
                            }
                        },
                    },
                )
        except httpx.HTTPError as exc:
            raise JevRouterError(f"Jev request failed: {exc}") from exc
        latency_ms = round((time.perf_counter() - started) * 1000)
        if not response.is_success:
            raise JevRouterError(
                f"Jev request failed with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        try:
            body = response.json()
            answer = body["answers"][task_id]
            probabilities = answer["probabilities"]
            choice = answer["choice"]
            p_run = probabilities["RUN"]
            p_skip = probabilities["SKIP"]
            resolved_model = body.get("model") or self.model
            confidence = answer.get("confidence")
        except (KeyError, TypeError, ValueError) as exc:
            raise JevRouterError("Jev response has an invalid shape") from exc
        if (
            choice not in {"RUN", "SKIP"}
            or not isinstance(p_run, (int, float))
            or not isinstance(p_skip, (int, float))
            or not isinstance(resolved_model, str)
            or (confidence is not None and not isinstance(confidence, (int, float)))
        ):
            raise JevRouterError("Jev response contains invalid decision values")
        if abs(float(p_run) + float(p_skip) - 1) > 0.02:
            raise JevRouterError("Jev RUN and SKIP probabilities do not sum to one")
        return JevPrediction(
            task_id=task_id,
            model=resolved_model,
            choice=choice,
            p_run=float(p_run),
            p_skip=float(p_skip),
            confidence=float(confidence) if confidence is not None else None,
            latency_ms=latency_ms,
        )
