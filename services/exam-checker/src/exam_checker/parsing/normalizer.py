from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..domain.models import (
    ExamDocument,
    PaperMetadata,
    QuestionNode,
    SectionNode,
    SourceMetadata,
)


class NormalizationError(ValueError):
    pass


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _points(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value)
        if match:
            return float(match.group(0))
    return None


def _string_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


@dataclass(slots=True)
class _BuildState:
    nodes: dict[str, SectionNode | QuestionNode] = field(default_factory=dict)
    section_count: int = 0
    question_count: int = 0

    def next_id(self, node_type: str) -> str:
        if node_type == "section":
            self.section_count += 1
            return f"section_{self.section_count}"
        self.question_count += 1
        return f"question_{self.question_count}"


class ExamDocumentNormalizer:
    """Convert the internship-style hierarchical tree into ExamDocument."""

    def normalize(
        self,
        raw_tree: Mapping[str, object],
        *,
        source_file_name: str,
        provider_task_id: str,
        markdown: str,
    ) -> ExamDocument:
        raw_questions = raw_tree.get("questions")
        if not isinstance(raw_questions, Sequence) or isinstance(
            raw_questions, (str, bytes, bytearray)
        ):
            raise NormalizationError("Extracted tree must contain a questions array")

        raw_paper = raw_tree.get("paper")
        paper_values = raw_paper if isinstance(raw_paper, Mapping) else {}
        paper = PaperMetadata(
            title=_text(paper_values.get("title")),
            subject=_text(
                paper_values.get("subject") or paper_values.get("subject_name")
            ),
            subject_code=_text(paper_values.get("subject_code")),
            language=_text(
                paper_values.get("language") or paper_values.get("paper_language")
            )
            or "zh",
        )
        state = _BuildState()
        root_ids: list[str] = []
        for index, item in enumerate(raw_questions, start=1):
            if not isinstance(item, Mapping):
                raise NormalizationError(f"questions[{index - 1}] must be an object")
            root_ids.append(
                self._normalize_node(
                    item,
                    state=state,
                    parent_id=None,
                    path=[],
                    inherited_type="",
                    fallback_source_id=str(index),
                )
            )
        if not state.nodes:
            raise NormalizationError("Extracted tree contains no nodes")
        return ExamDocument(
            document_id=f"exam_{uuid.uuid4().hex}",
            paper=paper,
            root_node_ids=root_ids,
            nodes=state.nodes,
            source=SourceMetadata(
                file_name=source_file_name,
                provider_task_id=provider_task_id,
                markdown_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
            ),
        )

    def _normalize_node(
        self,
        raw: Mapping[str, object],
        *,
        state: _BuildState,
        parent_id: str | None,
        path: list[str],
        inherited_type: str,
        fallback_source_id: str,
    ) -> str:
        source_id = _text(raw.get("id")) or fallback_source_id
        current_path = [*path, source_id]
        raw_children = raw.get("children")
        children = (
            list(raw_children)
            if isinstance(raw_children, Sequence)
            and not isinstance(raw_children, (str, bytes, bytearray))
            else []
        )
        has_content_field = "content" in raw
        # The internship parser treats a node with children as a group and a
        # content-bearing node as a question. A title-only terminal node is a
        # single-question fallback rather than an empty section.
        is_section = bool(children)
        if is_section:
            node_id = state.next_id("section")
            title = _text(raw.get("title") or raw.get("title_prefix"))
            section = SectionNode(
                id=node_id,
                parent_id=parent_id,
                source_id=source_id,
                title=title,
                points=_points(raw.get("points") or raw.get("score")),
                reference=_text(raw.get("reference") or raw.get("reading_passage")),
                numbering_description=_text(raw.get("numbering_description")),
            )
            state.nodes[node_id] = section
            for index, child in enumerate(children, start=1):
                if not isinstance(child, Mapping):
                    raise NormalizationError(
                        f"Child {index} of {node_id} must be an object"
                    )
                child_id = self._normalize_node(
                    child,
                    state=state,
                    parent_id=node_id,
                    path=current_path,
                    inherited_type=title or inherited_type,
                    fallback_source_id=str(index),
                )
                section.children.append(child_id)
            if not section.children:
                raise NormalizationError(f"Section node {node_id} contains no children")
            return node_id

        node_id = state.next_id("question")
        question_text = _text(raw.get("content") or raw.get("question_text"))
        if not has_content_field and not question_text:
            question_text = _text(raw.get("title") or raw.get("title_prefix"))
        if not question_text:
            raise NormalizationError(
                f"Question at path {'.'.join(current_path)} has empty content"
            )
        question = QuestionNode(
            id=node_id,
            parent_id=parent_id,
            source_id=source_id,
            question_number=source_id,
            question_type=_text(raw.get("question_type")) or inherited_type,
            question_text=question_text,
            options=_string_list(raw.get("options")),
            answer_text=_text(raw.get("answer") or raw.get("answer_text")),
            points=_points(raw.get("points") or raw.get("score")),
            reference=_text(raw.get("reference") or raw.get("reading_passage")),
            numbering_path=current_path,
            numbering_description=_text(raw.get("numbering_description")),
        )
        state.nodes[node_id] = question
        return node_id
