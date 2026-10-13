from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

import httpx


MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = {".pdf"}


class MinerUError(RuntimeError):
    pass


class MinerULimitError(MinerUError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    markdown: str
    provider: str
    provider_task_id: str
    source_file_name: str


class MinerUAgentClient:
    """Client for MinerU's token-free, IP-rate-limited Agent API."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 300,
        poll_interval_seconds: float = 3,
        max_retries: int = 4,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.max_retries = max_retries
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=30),
            follow_redirects=True,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def parse_file(
        self,
        file_bytes: bytes,
        file_name: str,
        *,
        language: str = "ch",
        is_ocr: bool = True,
        enable_table: bool = True,
        enable_formula: bool = True,
    ) -> ExtractedDocument:
        safe_name = Path(file_name).name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
            raise MinerUError(
                f"MinerU upload must be a PDF, received {suffix or '(missing extension)'}"
            )
        if not file_bytes:
            raise MinerUError("Cannot submit an empty file")
        if len(file_bytes) > MAX_FILE_BYTES:
            raise MinerULimitError("MinerU Agent API accepts files up to 10 MB")

        payload = {
            "file_name": safe_name,
            "language": language,
            "enable_table": enable_table,
            "is_ocr": is_ocr,
            "enable_formula": enable_formula,
        }
        created = await self._request_json(
            "POST", f"{self.base_url}/parse/file", json=payload
        )
        data = self._response_data(created, "create upload")
        task_id = str(data.get("task_id") or "").strip()
        upload_url = str(data.get("file_url") or "").strip()
        if not task_id or not upload_url:
            raise MinerUError("MinerU create response is missing task_id or file_url")
        self._require_https_url(upload_url, "upload URL")

        upload = await self._request("PUT", upload_url, content=file_bytes)
        if upload.status_code not in {200, 201, 204}:
            raise MinerUError(
                f"MinerU signed upload failed with HTTP {upload.status_code}"
            )

        markdown_url = await self._poll_result(task_id)
        self._require_https_url(markdown_url, "Markdown URL")
        markdown_response = await self._request("GET", markdown_url)
        if not markdown_response.is_success:
            raise MinerUError(
                f"MinerU Markdown download failed with HTTP {markdown_response.status_code}"
            )
        markdown = markdown_response.text
        if not markdown.strip():
            raise MinerUError("MinerU returned empty Markdown")
        return ExtractedDocument(
            markdown=markdown,
            provider="mineru-agent",
            provider_task_id=task_id,
            source_file_name=safe_name,
        )

    async def _poll_result(self, task_id: str) -> str:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            payload = await self._request_json(
                "GET", f"{self.base_url}/parse/{task_id}"
            )
            data = self._response_data(payload, "poll")
            state = str(data.get("state") or "").strip().lower()
            if state == "done":
                markdown_url = str(data.get("markdown_url") or "").strip()
                if not markdown_url:
                    raise MinerUError("Completed MinerU task is missing markdown_url")
                return markdown_url
            if state == "failed":
                error_code = data.get("err_code")
                error_message = str(data.get("err_msg") or "unknown error")
                if error_code in {-30001, -30003}:
                    raise MinerULimitError(error_message)
                raise MinerUError(f"MinerU task failed ({error_code}): {error_message}")
            if state not in {"waiting-file", "uploading", "pending", "running"}:
                raise MinerUError(
                    f"MinerU returned unknown task state: {state or '(empty)'}"
                )
            await asyncio.sleep(self.poll_interval_seconds)
        raise MinerUError(
            f"MinerU task timed out after {self.timeout_seconds:g} seconds"
        )

    async def _request_json(
        self, method: str, url: str, **kwargs: object
    ) -> Mapping[str, object]:
        response = await self._request(method, url, **kwargs)
        if not response.is_success:
            raise MinerUError(f"MinerU request failed with HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise MinerUError("MinerU returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise MinerUError("MinerU returned a non-object JSON response")
        return payload

    async def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = await self._client.request(method, url, **kwargs)
                if response.status_code != 429 and response.status_code < 500:
                    return response
                if attempt == self.max_retries - 1:
                    return response
                retry_after = response.headers.get("Retry-After", "").strip()
                delay = (
                    float(retry_after)
                    if retry_after.replace(".", "", 1).isdigit()
                    else 2**attempt
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                delay = 2**attempt
            await asyncio.sleep(min(delay, 30))
        raise MinerUError(f"MinerU request failed: {last_error}") from last_error

    @staticmethod
    def _response_data(
        payload: Mapping[str, object], operation: str
    ) -> Mapping[str, object]:
        code = payload.get("code")
        if code != 0:
            raise MinerUError(
                f"MinerU {operation} failed ({code}): {payload.get('msg')}"
            )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise MinerUError(f"MinerU {operation} response is missing data")
        return data

    @staticmethod
    def _require_https_url(url: str, label: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise MinerUError(f"MinerU returned an invalid {label}")
