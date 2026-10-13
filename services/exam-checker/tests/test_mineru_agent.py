from __future__ import annotations

import json

import httpx
import pytest

from exam_checker.integrations.mineru_agent import MinerUAgentClient, MinerULimitError


@pytest.mark.asyncio
async def test_mineru_signed_upload_poll_and_markdown_download() -> None:
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        if request.url.path.endswith("/parse/file"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "data": {
                        "task_id": "task-1",
                        "file_url": "https://upload.example.test/file.pdf",
                    },
                },
            )
        if request.method == "PUT":
            return httpx.Response(200)
        if request.url.path.endswith("/parse/task-1"):
            poll_count += 1
            state = "running" if poll_count == 1 else "done"
            data = {"task_id": "task-1", "state": state}
            if state == "done":
                data["markdown_url"] = "https://cdn.example.test/full.md"
            return httpx.Response(200, json={"code": 0, "msg": "ok", "data": data})
        if request.url.path.endswith("/full.md"):
            return httpx.Response(200, text="# Exam\n1. Question")
        return httpx.Response(404, content=json.dumps({"error": "unexpected"}))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = MinerUAgentClient(
            "https://mineru.example.test/api/v1/agent",
            poll_interval_seconds=0.001,
            client=http_client,
        )
        result = await client.parse_file(b"%PDF-fake", "exam.pdf")

    assert result.provider_task_id == "task-1"
    assert result.markdown.startswith("# Exam")
    assert poll_count == 2


@pytest.mark.asyncio
async def test_mineru_maps_page_limit_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/parse/file"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "msg": "ok",
                    "data": {
                        "task_id": "task-2",
                        "file_url": "https://upload.example.test/file.pdf",
                    },
                },
            )
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "ok",
                "data": {
                    "task_id": "task-2",
                    "state": "failed",
                    "err_code": -30003,
                    "err_msg": "page limit",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = MinerUAgentClient(
            "https://mineru.example.test/api/v1/agent",
            poll_interval_seconds=0.001,
            client=http_client,
        )
        with pytest.raises(MinerULimitError, match="page limit"):
            await client.parse_file(b"%PDF-fake", "exam.pdf")
