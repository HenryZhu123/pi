from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from typing import Literal, Protocol
from uuid import uuid4

from ..domain.models import (
    CheckerPayload,
    JevPrediction,
    QuestionReview,
    ReviewedQuestion,
    ReviewCheck,
    ReviewError,
    ReviewSummary,
    ReviewTaskId,
    ReviewTiming,
    RoutingDecision,
    SpecialistReviewResult,
)
from ..prompts.repositories import JevPromptRepository
from .persistence import QuestionReviewRepository


ALL_REVIEW_TASKS: tuple[ReviewTaskId, ...] = (
    "typo_check",
    "punctuation_check",
    "options_check",
    "mismatch_check",
    "missing_check",
    "ambiguity_check",
    "unsolvability_check",
    "answer_leak_check",
    "logic_check",
)


class JevRouter(Protocol):
    model: str

    async def predict(
        self, task_id: ReviewTaskId, payload: CheckerPayload
    ) -> JevPrediction: ...


class SpecialistReviewer(Protocol):
    model: str

    async def review(
        self, task_id: ReviewTaskId, payload: CheckerPayload
    ) -> tuple[SpecialistReviewResult, int, int]: ...


class QuestionReviewService:
    def __init__(
        self,
        *,
        router: JevRouter,
        reviewer: SpecialistReviewer,
        jev_prompts: JevPromptRepository,
        review_repository: QuestionReviewRepository | None = None,
    ) -> None:
        self.router = router
        self.reviewer = reviewer
        self.jev_prompts = jev_prompts
        self.review_repository = review_repository
        self.routed_tasks = frozenset(jev_prompts.task_ids)
        unexpected = self.routed_tasks - set(ALL_REVIEW_TASKS)
        if unexpected:
            raise ValueError(f"Unknown routed tasks: {sorted(unexpected)}")

    async def review_question(
        self,
        *,
        document_id: str,
        question_id: str,
        question_number: str,
        payload: CheckerPayload,
        question_version_id: str | None = None,
    ) -> ReviewedQuestion:
        started = time.perf_counter()
        tasks = [
            asyncio.create_task(self._run_task(task_id, payload))
            for task_id in ALL_REVIEW_TASKS
        ]
        outcomes = await asyncio.gather(*tasks)
        checks = [outcome[0] for outcome in outcomes]
        routing = {
            task_id: decision
            for task_id, (_, decision) in zip(ALL_REVIEW_TASKS, outcomes, strict=True)
            if decision is not None
        }
        errors = self._deduplicate_errors(
            error
            for check in checks
            if check.result is not None
            for error in check.result.errors
        )
        unresolved = [
            check.task_id for check in checks if check.execution_status == "FAILED"
        ]
        if unresolved:
            status = "UNRESOLVED"
        elif errors:
            status = "NEEDS_REVISION"
        else:
            status = "PASS"
        total_ms = round((time.perf_counter() - started) * 1000)
        routing_ms = max(
            (decision.latency_ms for decision in routing.values()), default=0
        )
        specialist_ms = max(
            (
                check.latency_ms
                for check in checks
                if check.execution_status != "SKIPPED_BY_ROUTER"
            ),
            default=0,
        )
        review = ReviewedQuestion(
            document_id=document_id,
            question_id=question_id,
            question_number=question_number,
            question_version_id=question_version_id,
            review_id=str(uuid4()),
            question=payload,
            review=QuestionReview(
                status=status,
                routing=routing,
                checks=checks,
                summary=ReviewSummary(
                    has_error=bool(errors),
                    total_errors=len(errors),
                    errors=errors,
                    unresolved_checks=unresolved,
                ),
                timing_ms=ReviewTiming(
                    routing=routing_ms,
                    specialist_checks=specialist_ms,
                    total=total_ms,
                ),
            ),
        )
        if self.review_repository is not None:
            await self.review_repository.save(review)
        return review

    async def _run_task(
        self, task_id: ReviewTaskId, payload: CheckerPayload
    ) -> tuple[ReviewCheck, RoutingDecision | None]:
        if task_id not in self.routed_tasks:
            return await self._run_specialist(task_id, payload, "direct"), None

        prompt = self.jev_prompts.get(task_id)
        route_started = time.perf_counter()
        try:
            prediction = await self.router.predict(task_id, payload)
            decision = RoutingDecision(
                decision=(
                    "SKIP" if prediction.p_skip >= prompt.skip_threshold else "RUN"
                ),
                p_run=prediction.p_run,
                p_skip=prediction.p_skip,
                confidence=prediction.confidence,
                skip_threshold=prompt.skip_threshold,
                model=prediction.model,
                latency_ms=prediction.latency_ms,
            )
        except Exception as exc:
            decision = RoutingDecision(
                decision="FALLBACK_RUN",
                skip_threshold=prompt.skip_threshold,
                model=self.router.model,
                latency_ms=round((time.perf_counter() - route_started) * 1000),
                error=str(exc),
            )
        if decision.decision == "SKIP":
            return (
                ReviewCheck(
                    task_id=task_id,
                    routing_mode="jev",
                    execution_status="SKIPPED_BY_ROUTER",
                    latency_ms=decision.latency_ms,
                    attempts=0,
                ),
                decision,
            )
        return await self._run_specialist(task_id, payload, "jev"), decision

    async def _run_specialist(
        self,
        task_id: ReviewTaskId,
        payload: CheckerPayload,
        routing_mode: Literal["jev", "direct"],
    ) -> ReviewCheck:
        started = time.perf_counter()
        try:
            result, latency_ms, attempts = await self.reviewer.review(task_id, payload)
            return ReviewCheck(
                task_id=task_id,
                routing_mode=routing_mode,
                model=self.reviewer.model,
                execution_status="COMPLETED",
                result=result,
                latency_ms=latency_ms,
                attempts=attempts,
            )
        except Exception as exc:
            return ReviewCheck(
                task_id=task_id,
                routing_mode=routing_mode,
                model=self.reviewer.model,
                execution_status="FAILED",
                latency_ms=round((time.perf_counter() - started) * 1000),
                attempts=max(1, getattr(exc, "attempts", 1)),
                error_code=getattr(exc, "error_code", "SPECIALIST_FAILED"),
                retryable=getattr(exc, "retryable", False),
                error=str(exc),
            )

    @staticmethod
    def _deduplicate_errors(errors: Iterable[ReviewError]) -> list[ReviewError]:
        unique: dict[tuple[str, str, str, str], ReviewError] = {}
        for error in errors:
            key = (
                error.error_type.strip(),
                error.original_text.strip(),
                error.position.strip(),
                error.correction.strip(),
            )
            unique.setdefault(key, error)
        return list(unique.values())
