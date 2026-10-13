from __future__ import annotations

from exam_checker.parsing.normalizer import ExamDocumentNormalizer
from exam_checker.services.question_service import QuestionService


def test_normalizes_tree_and_resolves_inherited_reference() -> None:
    raw = {
        "paper": {
            "title": "计算机网络期末考试",
            "subject": "计算机科学",
            "language": "zh",
        },
        "questions": [
            {
                "id": 1,
                "title": "材料选择题",
                "reference": "阅读以下网络拓扑描述。",
                "children": [
                    {
                        "id": 1,
                        "content": "路由器的主要作用是什么？",
                        "options": ["A. 转发分组", "B. 编译程序"],
                        "answer": "A",
                        "points": 2,
                    }
                ],
            }
        ],
    }
    document = ExamDocumentNormalizer().normalize(
        raw,
        source_file_name="network.pdf",
        provider_task_id="mineru-task",
        markdown="# exam",
    )

    question = QuestionService().get_question(document, "question_1")

    assert document.root_node_ids == ["section_1"]
    assert question.numbering_path == ["1", "1"]
    assert question.section_titles == ["材料选择题"]
    assert question.reference == "阅读以下网络拓扑描述。"
    assert question.options == ["A. 转发分组", "B. 编译程序"]


def test_checker_payload_is_derived_not_persisted() -> None:
    raw = {
        "paper": {"title": "测试卷", "subject": "计算机"},
        "questions": [
            {
                "id": 1,
                "title": "选择题",
                "children": [
                    {
                        "id": 3,
                        "content": "以下哪项正确？",
                        "options": ["A. 甲", "B. 乙"],
                    }
                ],
            }
        ],
    }
    document = ExamDocumentNormalizer().normalize(
        raw,
        source_file_name="exam.pdf",
        provider_task_id="task",
        markdown="text",
    )

    payload = QuestionService().to_checker_payload(document, "question_1")

    assert "【学科】计算机" in payload.reference
    assert "【试卷标题】测试卷" in payload.reference
    assert payload.detection_content == "以下哪项正确？\nA. 甲\nB. 乙"
