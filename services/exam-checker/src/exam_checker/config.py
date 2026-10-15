from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    value = float(raw) if raw else default
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    value = int(raw) if raw else default
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    host: str
    port: int
    database_url: str
    mineru_base_url: str
    mineru_timeout_seconds: float
    mineru_poll_interval_seconds: float
    mineru_max_retries: int
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: float
    review_llm_base_url: str
    review_llm_api_key: str
    review_llm_model: str
    review_llm_timeout_seconds: float
    review_llm_max_concurrency: int
    review_llm_max_attempts: int
    review_prompt_root: str
    jev_base_url: str
    jev_api_key: str
    jev_model: str
    jev_timeout_seconds: float
    jev_max_concurrency: int
    jev_prompt_root: str
    workflow_llm_base_url: str
    workflow_llm_api_key: str
    workflow_llm_model: str
    workflow_llm_timeout_seconds: float
    workflow_llm_max_concurrency: int
    revision_prompt_root: str
    metadata_prompt_root: str

    @classmethod
    def from_env(cls) -> "Settings":
        service_root = Path(__file__).resolve().parents[2]
        packaged_prompt_root = Path(__file__).resolve().parent / "assets" / "prompts"
        default_prompt_root = (
            packaged_prompt_root
            if packaged_prompt_root.is_dir()
            else service_root / "prompts"
        )
        llm_base_url = os.getenv("EXAM_CHECKER_LLM_BASE_URL", "").strip().rstrip("/")
        llm_api_key = os.getenv("EXAM_CHECKER_LLM_API_KEY", "").strip()
        llm_model = os.getenv("EXAM_CHECKER_LLM_MODEL", "").strip()
        return cls(
            host=os.getenv("EXAM_CHECKER_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_positive_int("EXAM_CHECKER_PORT", 8091),
            database_url=os.getenv("EXAM_CHECKER_DATABASE_URL", "").strip(),
            mineru_base_url=(
                os.getenv("MINERU_AGENT_BASE_URL", "https://mineru.net/api/v1/agent")
                .strip()
                .rstrip("/")
            ),
            mineru_timeout_seconds=_positive_float("MINERU_TIMEOUT_SECONDS", 300),
            mineru_poll_interval_seconds=_positive_float(
                "MINERU_POLL_INTERVAL_SECONDS", 3
            ),
            mineru_max_retries=_positive_int("MINERU_MAX_RETRIES", 4),
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            llm_timeout_seconds=_positive_float(
                "EXAM_CHECKER_LLM_TIMEOUT_SECONDS", 180
            ),
            review_llm_base_url=(
                os.getenv("EXAM_CHECKER_REVIEW_LLM_BASE_URL", llm_base_url)
                .strip()
                .rstrip("/")
            ),
            review_llm_api_key=os.getenv(
                "EXAM_CHECKER_REVIEW_LLM_API_KEY", llm_api_key
            ).strip(),
            review_llm_model=os.getenv(
                "EXAM_CHECKER_REVIEW_LLM_MODEL", llm_model
            ).strip(),
            review_llm_timeout_seconds=_positive_float(
                "EXAM_CHECKER_REVIEW_LLM_TIMEOUT_SECONDS", 180
            ),
            review_llm_max_concurrency=_positive_int(
                "EXAM_CHECKER_REVIEW_LLM_MAX_CONCURRENCY", 8
            ),
            review_llm_max_attempts=_positive_int(
                "EXAM_CHECKER_REVIEW_LLM_MAX_ATTEMPTS", 3
            ),
            review_prompt_root=os.getenv(
                "EXAM_CHECKER_REVIEW_PROMPT_ROOT",
                str(default_prompt_root / "reviewer-prompt" / "qwen38_27b"),
            ).strip(),
            jev_base_url=(
                os.getenv(
                    "EXAM_CHECKER_JEV_BASE_URL",
                    "https://openrouter.ai/api/alpha",
                )
                .strip()
                .rstrip("/")
            ),
            jev_api_key=os.getenv(
                "EXAM_CHECKER_JEV_API_KEY", os.getenv("OPENROUTER_API_KEY", "")
            ).strip(),
            jev_model=os.getenv("EXAM_CHECKER_JEV_MODEL", "typesafe/jev-1.13").strip(),
            jev_timeout_seconds=_positive_float("EXAM_CHECKER_JEV_TIMEOUT_SECONDS", 30),
            jev_max_concurrency=_positive_int("EXAM_CHECKER_JEV_MAX_CONCURRENCY", 8),
            jev_prompt_root=os.getenv(
                "EXAM_CHECKER_JEV_PROMPT_ROOT",
                str(default_prompt_root / "jev-prompt"),
            ).strip(),
            workflow_llm_base_url=(
                os.getenv("EXAM_CHECKER_WORKFLOW_LLM_BASE_URL", llm_base_url)
                .strip()
                .rstrip("/")
            ),
            workflow_llm_api_key=os.getenv(
                "EXAM_CHECKER_WORKFLOW_LLM_API_KEY", llm_api_key
            ).strip(),
            workflow_llm_model=os.getenv(
                "EXAM_CHECKER_WORKFLOW_LLM_MODEL", llm_model
            ).strip(),
            workflow_llm_timeout_seconds=_positive_float(
                "EXAM_CHECKER_WORKFLOW_LLM_TIMEOUT_SECONDS", 180
            ),
            workflow_llm_max_concurrency=_positive_int(
                "EXAM_CHECKER_WORKFLOW_LLM_MAX_CONCURRENCY", 8
            ),
            revision_prompt_root=os.getenv(
                "EXAM_CHECKER_REVISION_PROMPT_ROOT",
                str(default_prompt_root / "question-revision-prompt"),
            ).strip(),
            metadata_prompt_root=os.getenv(
                "EXAM_CHECKER_METADATA_PROMPT_ROOT",
                str(default_prompt_root / "question-metadata-prompt"),
            ).strip(),
        )
