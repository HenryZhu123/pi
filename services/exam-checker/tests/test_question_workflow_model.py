from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from exam_checker.domain.models import QuestionView, ReviewError
from exam_checker.integrations.question_workflow_model import (
    OpenAICompatibleQuestionWorkflowModel,
)
from exam_checker.prompts.repositories import SinglePromptRepository


PROMPT_ROOT = Path(__file__).parents[1] / "prompts"


def make_question() -> QuestionView:
    return QuestionView(
        document_id="exam_1",
        question_id="question_1",
        question_number="1",
        question_type="single_choice",
        question_text="以下哪项错吴？",
        options=["A. 甲", "B. 乙"],
        answer_text="A",
        points=2,
        subject="计算机",
        paper_title="测试卷",
        section_titles=["选择题"],
        numbering_path=["1", "1"],
        numbering_description="第1大题-第1小题",
        reference="",
    )


@pytest.mark.asyncio
async def test_revision_prompt_passes_complete_question_and_errors_directly() -> None:
    question = make_question()
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.update(body)
        revised = question.model_copy(update={"question_text": "以下哪项错误？"})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": revised.model_dump_json()}}
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = OpenAICompatibleQuestionWorkflowModel(
        "https://example.test/v1",
        "key",
        "model",
        revision_prompts=SinglePromptRepository(
            PROMPT_ROOT / "question-revision-prompt"
        ),
        metadata_prompts=SinglePromptRepository(
            PROMPT_ROOT / "question-metadata-prompt"
        ),
        client=client,
    )
    error = ReviewError(
        error_type="错别字",
        original_text="错吴",
        correction="错误",
        suggestion="修正错别字",
    )

    revised = await model.revise(question, [error], "保持原题难度")

    user_prompt = captured["messages"][1]["content"]
    assert "【原题 JSON】" in user_prompt
    assert '"document_id": "exam_1"' in user_prompt
    assert "【审校修改意见】" in user_prompt
    assert '"error_type": "错别字"' in user_prompt
    assert "【用户补充修改意见】\n保持原题难度" in user_prompt
    assert "current_question" not in user_prompt
    assert revised.question_text == "以下哪项错误？"
    await client.aclose()
