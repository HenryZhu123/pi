from __future__ import annotations

from ..domain.models import (
    CheckerPayload,
    ExamDocument,
    QuestionNode,
    QuestionView,
    SectionNode,
)


class QuestionNotFoundError(KeyError):
    pass


class QuestionService:
    def list_questions(self, document: ExamDocument) -> list[QuestionView]:
        return [
            self.get_question(document, node_id)
            for node_id, node in document.nodes.items()
            if isinstance(node, QuestionNode)
        ]

    def get_question(self, document: ExamDocument, question_id: str) -> QuestionView:
        node = document.nodes.get(question_id)
        if not isinstance(node, QuestionNode):
            raise QuestionNotFoundError(question_id)

        references: list[str] = []
        section_titles: list[str] = []
        ancestors: list[SectionNode] = []
        parent_id = node.parent_id
        while parent_id is not None:
            parent = document.nodes[parent_id]
            if not isinstance(parent, SectionNode):
                raise ValueError(f"Question {question_id!r} has a non-section parent")
            ancestors.append(parent)
            parent_id = parent.parent_id
        for ancestor in reversed(ancestors):
            if ancestor.title:
                section_titles.append(ancestor.title)
            if ancestor.reference and ancestor.reference not in references:
                references.append(ancestor.reference)
        if node.reference and node.reference not in references:
            references.append(node.reference)

        return QuestionView(
            document_id=document.document_id,
            question_id=node.id,
            question_number=node.question_number,
            question_type=node.question_type,
            question_text=node.question_text,
            options=list(node.options),
            answer_text=node.answer_text,
            points=node.points,
            subject=document.paper.subject,
            paper_title=document.paper.title,
            section_titles=section_titles,
            numbering_path=list(node.numbering_path),
            numbering_description=node.numbering_description,
            reference="\n\n".join(references),
        )

    def to_checker_payload(
        self, document: ExamDocument, question_id: str
    ) -> CheckerPayload:
        question = self.get_question(document, question_id)
        return self.to_checker_payload_from_view(question)

    @staticmethod
    def to_checker_payload_from_view(question: QuestionView) -> CheckerPayload:
        reference_parts = [
            f"【学科】{question.subject}" if question.subject else "",
            f"【试卷标题】{question.paper_title}" if question.paper_title else "",
            f"【题型】{question.question_type}" if question.question_type else "",
            f"【所属部分】{' / '.join(question.section_titles)}"
            if question.section_titles
            else "",
            f"【共享材料】{question.reference}" if question.reference else "",
        ]
        detection_parts = [question.question_text, *question.options]
        return CheckerPayload(
            reference="\n".join(part for part in reference_parts if part),
            detection_content="\n".join(part for part in detection_parts if part),
        )
