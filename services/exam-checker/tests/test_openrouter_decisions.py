from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from exam_checker.domain.models import CheckerPayload
from exam_checker.integrations.openrouter_decisions import OpenRouterJevClient
from exam_checker.prompts.repositories import JevPromptRepository


PROMPT_ROOT = Path(__file__).parents[1] / "prompts" / "jev-prompt"


@pytest.mark.asyncio
async def test_jev_client_sends_locked_task_and_parses_probabilities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/alpha/decisions"
        assert request.headers["Authorization"] == "Bearer secret"
        body = json.loads(request.content)
        assert list(body["questions"]) == ["answer_leak_check"]
        assert body["state"]["input_payload"]["detection_content"] == "题目（A）"
        return httpx.Response(
            200,
            json={
                "model": "typesafe/jev-1.13-20260917",
                "answers": {
                    "answer_leak_check": {
                        "type": "choice",
                        "choice": "RUN",
                        "probabilities": {"RUN": 0.97, "SKIP": 0.03},
                        "confidence": 0.94,
                    }
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = OpenRouterJevClient(
            "https://openrouter.example/api/alpha",
            "secret",
            "typesafe/jev-1.13",
            JevPromptRepository(PROMPT_ROOT),
            client=client,
        )
        prediction = await router.predict(
            "answer_leak_check",
            CheckerPayload(reference="【题型】选择题", detection_content="题目（A）"),
        )

    assert prediction.choice == "RUN"
    assert prediction.p_run == 0.97
    assert prediction.p_skip == 0.03


def test_locked_jev_tasks_include_answer_leak() -> None:
    repository = JevPromptRepository(PROMPT_ROOT)

    assert set(repository.task_ids) == {
        "options_check",
        "mismatch_check",
        "answer_leak_check",
    }
    assert repository.get("answer_leak_check").skip_threshold == 0.99
