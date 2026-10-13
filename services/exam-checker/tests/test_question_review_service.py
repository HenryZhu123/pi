from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from exam_checker.domain.models import (
    CheckerPayload,
    JevPrediction,
    ReviewError,
    SpecialistReviewResult,
)
from exam_checker.prompts.repositories import JevPromptRepository
from exam_checker.services.question_review_service import QuestionReviewService


PROMPT_ROOT = Path(__file__).parents[1] / "prompts" / "jev-prompt"


class ControlledRouter:
    model = "typesafe/jev-1.13"

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started: set[str] = set()

    async def predict(self, task_id: str, payload: CheckerPayload) -> JevPrediction:
        self.started.add(task_id)
        await self.release.wait()
        p_skip = {
            "options_check": 0.9,
            "mismatch_check": 0.2,
            "answer_leak_check": 0.995,
        }[task_id]
        return JevPrediction(
            task_id=task_id,
            model=self.model,
            choice="SKIP" if p_skip >= 0.5 else "RUN",
            p_run=1 - p_skip,
            p_skip=p_skip,
            confidence=0.9,
            latency_ms=10,
        )


class RecordingReviewer:
    model = "qwen3.8-27b"

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.direct_started = asyncio.Event()

    async def review(
        self, task_id: str, payload: CheckerPayload
    ) -> tuple[SpecialistReviewResult, int, int]:
        self.calls.append(task_id)
        if task_id not in {"options_check", "mismatch_check", "answer_leak_check"}:
            self.direct_started.set()
        return (
            SpecialistReviewResult(
                reason="未发现问题",
                has_error=False,
                total_errors=0,
                errors=[],
            ),
            20,
            1,
        )


@pytest.mark.asyncio
async def test_direct_checks_start_while_jev_routes_and_only_run_selected_tasks() -> None:
    router = ControlledRouter()
    reviewer = RecordingReviewer()
    service = QuestionReviewService(
        router=router,
        reviewer=reviewer,
        jev_prompts=JevPromptRepository(PROMPT_ROOT),
    )
    pending = asyncio.create_task(
        service.review_question(
            document_id="exam_1",
            question_id="question_1",
            question_number="1",
            payload=CheckerPayload(
                reference="【题型】选择题", detection_content="题目\nA.甲\nB.乙"
            ),
        )
    )

    await asyncio.wait_for(reviewer.direct_started.wait(), timeout=1)
    assert not pending.done()
    assert len(router.started) == 3
    router.release.set()
    result = await pending

    assert set(reviewer.calls) == {
        "typo_check",
        "punctuation_check",
        "mismatch_check",
        "missing_check",
        "ambiguity_check",
        "unsolvability_check",
        "logic_check",
    }
    assert result.review.status == "PASS"
    assert result.review.routing["options_check"].decision == "SKIP"
    assert result.review.routing["mismatch_check"].decision == "RUN"
    assert result.review.routing["answer_leak_check"].decision == "SKIP"
    assert len(result.review.checks) == 9


class FailingRouter:
    model = "typesafe/jev-1.13"

    async def predict(self, task_id: str, payload: CheckerPayload) -> JevPrediction:
        raise RuntimeError("router unavailable")


@pytest.mark.asyncio
async def test_jev_failure_fails_open_to_every_specialist() -> None:
    reviewer = RecordingReviewer()
    service = QuestionReviewService(
        router=FailingRouter(),
        reviewer=reviewer,
        jev_prompts=JevPromptRepository(PROMPT_ROOT),
    )

    result = await service.review_question(
        document_id="exam_1",
        question_id="question_1",
        question_number="1",
        payload=CheckerPayload(reference="", detection_content="测试题"),
    )

    assert set(reviewer.calls) == {
        "typo_check",
        "punctuation_check",
        "options_check",
        "mismatch_check",
        "missing_check",
        "ambiguity_check",
        "unsolvability_check",
        "answer_leak_check",
        "logic_check",
    }
    assert all(
        decision.decision == "FALLBACK_RUN"
        for decision in result.review.routing.values()
    )
    assert result.review.status == "PASS"


class MixedReviewer:
    model = "qwen3.8-27b"

    async def review(
        self, task_id: str, payload: CheckerPayload
    ) -> tuple[SpecialistReviewResult, int, int]:
        if task_id == "logic_check":
            raise RuntimeError("provider timeout")
        if task_id == "typo_check":
            error = ReviewError(
                error_type="错别字",
                position="题干",
                original_text="计祘机",
                anchor_text="计祘机网络",
                correction="计算机",
                description="存在错别字。",
                suggestion="将计祘机改为计算机。",
                severity="严重",
            )
            return (
                SpecialistReviewResult(
                    reason="存在错别字",
                    has_error=True,
                    total_errors=1,
                    errors=[error],
                ),
                20,
                1,
            )
        return (
            SpecialistReviewResult(
                reason="未发现问题",
                has_error=False,
                total_errors=0,
                errors=[],
            ),
            20,
            1,
        )


@pytest.mark.asyncio
async def test_specialist_failure_makes_question_unresolved_without_losing_errors() -> None:
    service = QuestionReviewService(
        router=FailingRouter(),
        reviewer=MixedReviewer(),
        jev_prompts=JevPromptRepository(PROMPT_ROOT),
    )

    result = await service.review_question(
        document_id="exam_1",
        question_id="question_1",
        question_number="1",
        payload=CheckerPayload(reference="", detection_content="计祘机网络"),
    )

    assert result.review.status == "UNRESOLVED"
    assert result.review.summary.has_error
    assert result.review.summary.total_errors == 1
    assert result.review.summary.unresolved_checks == ["logic_check"]
    failed = next(
        check for check in result.review.checks if check.task_id == "logic_check"
    )
    assert failed.attempts == 1
    assert failed.error_code == "SPECIALIST_FAILED"
    assert failed.retryable is False
