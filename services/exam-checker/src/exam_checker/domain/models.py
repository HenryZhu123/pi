from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaperMetadata(StrictModel):
    title: str = ""
    subject: str = ""
    subject_code: str = ""
    language: str = "zh"


class SourceMetadata(StrictModel):
    file_name: str
    media_type: str = "application/pdf"
    parser: str = "mineru-agent"
    provider_task_id: str
    markdown_sha256: str


class SectionNode(StrictModel):
    id: str
    type: Literal["section"] = "section"
    parent_id: str | None = None
    source_id: str = ""
    title: str = ""
    points: float | None = None
    reference: str = ""
    numbering_description: str = ""
    children: list[str] = Field(default_factory=list)


class QuestionNode(StrictModel):
    id: str
    type: Literal["question"] = "question"
    parent_id: str | None = None
    source_id: str = ""
    question_number: str
    question_type: str = ""
    question_text: str
    options: list[str] = Field(default_factory=list)
    answer_text: str = ""
    points: float | None = None
    reference: str = ""
    numbering_path: list[str] = Field(default_factory=list)
    numbering_description: str = ""


ExamNode = Annotated[SectionNode | QuestionNode, Field(discriminator="type")]


class ExamDocument(StrictModel):
    document_id: str
    paper: PaperMetadata
    root_node_ids: list[str]
    nodes: dict[str, ExamNode]
    source: SourceMetadata

    @model_validator(mode="after")
    def validate_graph(self) -> "ExamDocument":
        if not self.root_node_ids:
            raise ValueError("ExamDocument must contain at least one root node")
        if len(set(self.root_node_ids)) != len(self.root_node_ids):
            raise ValueError("root_node_ids must be unique")

        for node_id, node in self.nodes.items():
            if node.id != node_id:
                raise ValueError(
                    f"Node map key {node_id!r} does not match node.id {node.id!r}"
                )
            if node.parent_id is not None and node.parent_id not in self.nodes:
                raise ValueError(
                    f"Node {node_id!r} references missing parent {node.parent_id!r}"
                )
            if isinstance(node, SectionNode):
                if len(set(node.children)) != len(node.children):
                    raise ValueError(f"Section {node_id!r} contains duplicate children")
                for child_id in node.children:
                    child = self.nodes.get(child_id)
                    if child is None:
                        raise ValueError(
                            f"Section {node_id!r} references missing child {child_id!r}"
                        )
                    if child.parent_id != node_id:
                        raise ValueError(
                            f"Child {child_id!r} has inconsistent parent_id"
                        )

        for root_id in self.root_node_ids:
            root = self.nodes.get(root_id)
            if root is None:
                raise ValueError(f"Missing root node {root_id!r}")
            if root.parent_id is not None:
                raise ValueError(f"Root node {root_id!r} cannot have a parent")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError(f"Cycle detected at node {node_id!r}")
            if node_id in visited:
                return
            visiting.add(node_id)
            node = self.nodes[node_id]
            if isinstance(node, SectionNode):
                for child_id in node.children:
                    visit(child_id)
            visiting.remove(node_id)
            visited.add(node_id)

        for root_id in self.root_node_ids:
            visit(root_id)
        unreachable = set(self.nodes) - visited
        if unreachable:
            raise ValueError(
                f"Nodes are not reachable from a root: {sorted(unreachable)}"
            )
        return self


class QuestionView(StrictModel):
    document_id: str
    question_id: str
    question_number: str
    question_type: str
    question_text: str
    options: list[str]
    answer_text: str
    points: float | None
    subject: str
    paper_title: str
    section_titles: list[str]
    numbering_path: list[str]
    numbering_description: str
    reference: str


class ReviewError(StrictModel):
    error_type: str
    position: str = ""
    original_text: str = ""
    anchor_text: str = ""
    correction: str = ""
    description: str = ""
    suggestion: str = ""
    severity: str = ""


class QuestionChangeLog(StrictModel):
    user_instruction: str
    review_feedback: list[ReviewError]
    changes: str = Field(min_length=1)
    model: str


class QuestionRevisionResult(StrictModel):
    question: QuestionView
    changes: str = Field(min_length=1)


class StoredQuestionVersion(StrictModel):
    version_id: str
    document_id: str
    question_id: str
    version_number: int = Field(ge=1)
    parent_version_id: str | None = None
    revision_source: Literal["PARSED", "AI_EDIT", "USER_EDIT"]
    question: QuestionView
    change_log: QuestionChangeLog | None = None


class StoredQuestionBankEntry(StrictModel):
    record_id: str
    document_id: str
    question_id: str
    source_version_id: str | None = None
    question_json: dict[str, Any]


class QuestionBankSearchResult(StrictModel):
    questions: list[StoredQuestionBankEntry]


class CheckerPayload(StrictModel):
    reference: str
    detection_content: str


ReviewTaskId = Literal[
    "typo_check",
    "punctuation_check",
    "options_check",
    "mismatch_check",
    "missing_check",
    "ambiguity_check",
    "unsolvability_check",
    "answer_leak_check",
    "logic_check",
]


class QuestionStateReview(StrictModel):
    status: Literal["NOT_REVIEWED", "PASS", "NEEDS_REVISION", "UNRESOLVED"]
    review_id: str | None = None
    errors: list[ReviewError] = Field(default_factory=list)
    unresolved_checks: list[ReviewTaskId] = Field(default_factory=list)


class QuestionBankState(StrictModel):
    status: Literal["STORED", "OUTDATED", "NOT_STORED", "NOT_CONFIGURED"]
    record_id: str | None = None


class QuestionState(StrictModel):
    document_id: str
    question_id: str
    question_version_id: str
    version_number: int = Field(ge=1)
    revision_source: Literal["PARSED", "AI_EDIT", "USER_EDIT"]
    question: QuestionView
    change_log: QuestionChangeLog | None = None
    review: QuestionStateReview
    question_bank: QuestionBankState


class SpecialistReviewResult(StrictModel):
    think: str | None = None
    reason: str = ""
    has_error: bool
    total_errors: int = Field(ge=0)
    errors: list[ReviewError] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_error_count(self) -> "SpecialistReviewResult":
        if self.total_errors != len(self.errors):
            raise ValueError("total_errors must equal the number of errors")
        if self.has_error != bool(self.errors):
            raise ValueError("has_error must match whether errors is non-empty")
        return self


class JevPrediction(StrictModel):
    task_id: ReviewTaskId
    model: str
    choice: Literal["RUN", "SKIP"]
    p_run: float = Field(ge=0, le=1)
    p_skip: float = Field(ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    latency_ms: int = Field(ge=0)


class RoutingDecision(StrictModel):
    router: Literal["jev"] = "jev"
    decision: Literal["RUN", "SKIP", "FALLBACK_RUN"]
    p_run: float | None = Field(default=None, ge=0, le=1)
    p_skip: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    skip_threshold: float = Field(ge=0, le=1)
    model: str
    latency_ms: int = Field(ge=0)
    error: str | None = None


class ReviewCheck(StrictModel):
    task_id: ReviewTaskId
    routing_mode: Literal["jev", "direct"]
    model: str | None = None
    execution_status: Literal["SKIPPED_BY_ROUTER", "COMPLETED", "FAILED"]
    result: SpecialistReviewResult | None = None
    latency_ms: int = Field(ge=0)
    attempts: int = Field(default=0, ge=0)
    error_code: str | None = None
    retryable: bool | None = None
    error: str | None = None


class ReviewSummary(StrictModel):
    has_error: bool
    total_errors: int = Field(ge=0)
    errors: list[ReviewError] = Field(default_factory=list)
    unresolved_checks: list[ReviewTaskId] = Field(default_factory=list)


class ReviewTiming(StrictModel):
    routing: int = Field(ge=0)
    specialist_checks: int = Field(ge=0)
    total: int = Field(ge=0)


class QuestionReview(StrictModel):
    status: Literal["PASS", "NEEDS_REVISION", "UNRESOLVED"]
    routing: dict[ReviewTaskId, RoutingDecision] = Field(default_factory=dict)
    checks: list[ReviewCheck]
    summary: ReviewSummary
    timing_ms: ReviewTiming


class IngestionResult(StrictModel):
    status: Literal["STORED", "FAILED", "SKIPPED"]
    record_id: str | None = None
    error_code: str | None = None


class ReviewedQuestion(StrictModel):
    document_id: str
    question_id: str
    question_number: str
    question_version_id: str | None = None
    review_id: str | None = None
    question: CheckerPayload
    review: QuestionReview
    ingestion: IngestionResult | None = None


class ReviewedDocument(StrictModel):
    document_id: str
    status: Literal["PASS", "NEEDS_REVISION", "UNRESOLVED"]
    questions: list[ReviewedQuestion]


class QuestionMetadata(StrictModel):
    difficulty: Literal["easy", "medium", "hard"]
    knowledge_points: list[str] = Field(min_length=1, max_length=8)


class ReviseQuestionRequest(StrictModel):
    base_version_id: str
    question_json: dict[str, Any] | None = None
    user_instruction: str = ""
