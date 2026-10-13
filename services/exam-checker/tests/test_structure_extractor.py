from __future__ import annotations

import json

import httpx
import pytest

from exam_checker.parsing.structure_extractor import (
    OpenAICompatibleStructureExtractor,
    StructureExtractionError,
)


@pytest.mark.asyncio
async def test_structure_extractor_calls_openai_compatible_endpoint() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer secret"
        body = json.loads(request.content)
        if call_count == 1:
            content = json.dumps(
                {
                    "paper": {"title": "Test"},
                    "questions": [
                        {
                            "id": "1",
                            "title": "Section One",
                            "children": [{"id": "1", "content": ""}],
                        }
                    ],
                }
            )
        else:
            assert "Section One" in body["messages"][1]["content"]
            content = json.dumps(
                {
                    "id": "1",
                    "title": "Section One",
                    "children": [{"id": "1", "content": "Question"}],
                }
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": f"```json\n{content}\n```"}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        extractor = OpenAICompatibleStructureExtractor(
            "https://llm.example.test/v1",
            "secret",
            "model",
            client=client,
        )
        result = await extractor.extract("# Exam\nSection One\n1. Question")

    assert result["paper"] == {"title": "Test"}
    assert result["questions"][0]["children"][0]["content"] == "Question"
    assert call_count == 2


@pytest.mark.asyncio
async def test_structure_extractor_maps_provider_error() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(503, text="unavailable"))
    ) as client:
        extractor = OpenAICompatibleStructureExtractor(
            "https://llm.example.test/v1",
            "secret",
            "model",
            client=client,
        )
        with pytest.raises(StructureExtractionError, match="HTTP 503"):
            await extractor.extract("# Exam")
