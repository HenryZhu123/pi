from __future__ import annotations

import json

import httpx
import pytest

from exam_checker.domain.models import CheckerPayload
from exam_checker.integrations.specialist_reviewer import (
    OpenAICompatibleSpecialistReviewer,
)


class FakePrompts:
    def system_prompt(self, task_id: str) -> str:
        return f"PROMPT:{task_id}"


@pytest.mark.asyncio
async def test_specialist_reviewer_uses_input_payload_and_parses_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["messages"][0]["content"] == "PROMPT:typo_check"
        assert body["messages"][1]["content"].startswith("[INPUT_PAYLOAD]\n")
        assert body["enable_thinking"] is False
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "reason": "存在错别字",
                                    "has_error": True,
                                    "total_errors": 1,
                                    "errors": [
                                        {
                                            "error_type": "错别字",
                                            "position": "题干",
                                            "original_text": "计祘机",
                                            "anchor_text": "计祘机网络",
                                            "correction": "计算机",
                                            "description": "存在错别字。",
                                            "suggestion": "将计祘机改为计算机。",
                                            "severity": "严重",
                                        }
                                    ],
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reviewer = OpenAICompatibleSpecialistReviewer(
            "https://llm.example/v1",
            "secret",
            "qwen3.8-27b",
            FakePrompts(),
            client=client,
        )
        result, latency_ms = await reviewer.review(
            "typo_check",
            CheckerPayload(reference="【学科】计算机", detection_content="计祘机网络"),
        )

    assert result.has_error
    assert result.errors[0].correction == "计算机"
    assert latency_ms >= 0
