from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..domain.models import ReviewTaskId


class PromptConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class JevTaskPrompt:
    task_id: ReviewTaskId
    instructions: str
    criteria: dict[str, str]
    skip_threshold: float


class JevPromptRepository:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.routing_rules = self._read_text("shared-routing-rules.txt")
        routing_rules_hash = hashlib.sha256(
            (self.root / "shared-routing-rules.txt").read_bytes()
        ).hexdigest()
        raw_baselines = self._read_json("locked-baselines.json")
        self._tasks: dict[ReviewTaskId, JevTaskPrompt] = {}
        for raw_task_id, raw_baseline in raw_baselines.items():
            if not isinstance(raw_task_id, str) or not isinstance(
                raw_baseline, Mapping
            ):
                raise PromptConfigurationError("Invalid locked baseline entry")
            if raw_baseline.get("status") != "locked":
                continue
            prompt_path = raw_baseline.get("prompt_path")
            threshold = raw_baseline.get("skip_threshold")
            if not isinstance(prompt_path, str) or not isinstance(
                threshold, (int, float)
            ):
                raise PromptConfigurationError(
                    f"Locked baseline {raw_task_id} is incomplete"
                )
            raw_prompt = self._read_json(prompt_path)
            task_id = raw_prompt.get("task_id")
            instructions = raw_prompt.get("instructions")
            criteria = raw_prompt.get("criteria")
            if (
                task_id != raw_task_id
                or not isinstance(instructions, str)
                or not isinstance(criteria, Mapping)
                or set(criteria) != {"RUN", "SKIP"}
                or not all(isinstance(value, str) for value in criteria.values())
            ):
                raise PromptConfigurationError(
                    f"Invalid Jev prompt for {raw_task_id}"
                )
            expected_hash = raw_baseline.get("prompt_sha256")
            actual_hash = hashlib.sha256(
                (self.root / prompt_path).read_bytes()
            ).hexdigest()
            if expected_hash and actual_hash != expected_hash:
                raise PromptConfigurationError(
                    f"Prompt hash mismatch for {raw_task_id}"
                )
            expected_rules_hash = raw_baseline.get(
                "shared_routing_rules_sha256"
            )
            if expected_rules_hash and routing_rules_hash != expected_rules_hash:
                raise PromptConfigurationError(
                    f"Routing rules hash mismatch for {raw_task_id}"
                )
            typed_task_id = task_id
            self._tasks[typed_task_id] = JevTaskPrompt(
                task_id=typed_task_id,
                instructions=instructions,
                criteria=dict(criteria),
                skip_threshold=float(threshold),
            )

    @property
    def task_ids(self) -> tuple[ReviewTaskId, ...]:
        return tuple(self._tasks)

    def get(self, task_id: ReviewTaskId) -> JevTaskPrompt:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise PromptConfigurationError(
                f"No locked Jev prompt for {task_id}"
            ) from exc

    def _read_text(self, relative_path: str) -> str:
        path = self.root / relative_path
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PromptConfigurationError(f"Cannot read {path}: {exc}") from exc

    def _read_json(self, relative_path: str) -> Mapping[str, object]:
        path = self.root / relative_path
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PromptConfigurationError(f"Cannot load {path}: {exc}") from exc
        if not isinstance(value, Mapping):
            raise PromptConfigurationError(f"{path} must contain a JSON object")
        return value


REVIEW_TASK_MODES: dict[ReviewTaskId, str] = {
    "typo_check": "disabled",
    "punctuation_check": "explicit",
    "options_check": "disabled",
    "mismatch_check": "disabled",
    "missing_check": "disabled",
    "ambiguity_check": "disabled",
    "unsolvability_check": "explicit",
    "answer_leak_check": "explicit",
    "logic_check": "explicit",
}


class ReviewPromptRepository:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @property
    def task_ids(self) -> tuple[ReviewTaskId, ...]:
        return tuple(REVIEW_TASK_MODES)

    def system_prompt(self, task_id: ReviewTaskId) -> str:
        mode = REVIEW_TASK_MODES[task_id]
        output_name = (
            "JSON约束_with_think.txt"
            if mode == "explicit"
            else "JSON约束_without_think.txt"
        )
        task = self._read_text(Path(mode) / "tasks" / f"{task_id}.txt")
        output = self._read_text(Path(mode) / output_name)
        return f"{task}\n\n{output}"

    def _read_text(self, relative_path: Path) -> str:
        path = self.root / relative_path
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PromptConfigurationError(f"Cannot read {path}: {exc}") from exc
        if not text:
            raise PromptConfigurationError(f"Prompt file is empty: {path}")
        return text


class SinglePromptRepository:
    def __init__(self, root: str | Path) -> None:
        self.path = Path(root) / "system.txt"

    def system_prompt(self) -> str:
        try:
            text = self.path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PromptConfigurationError(
                f"Cannot read {self.path}: {exc}"
            ) from exc
        if not text:
            raise PromptConfigurationError(f"Prompt file is empty: {self.path}")
        return text
