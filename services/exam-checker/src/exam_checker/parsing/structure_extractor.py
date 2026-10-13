from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from typing import Mapping, Protocol

import httpx


class StructureExtractionError(RuntimeError):
    pass


class StructureExtractor(Protocol):
    async def extract(self, markdown: str) -> Mapping[str, object]: ...


SKELETON_SYSTEM_PROMPT = """你是试卷结构提取专家。先从 MinerU Markdown 提取试卷的树状骨架。

最高优先级规则：
1. 不得纠正、改写或补充题目原文，包括明显的错别字、事实错误和格式错误。
2. 忽略卷头、考生须知和页码，但提取 paper 元数据。
3. 保留大题、中间材料分组、小题的层级关系。
4. 本阶段只创建骨架。叶节点 content 必须为空字符串，不提取题干、选项或答案。
5. 无法识别的题型和分值使用空字符串或 null，不得猜测。

只返回 JSON 对象，结构如下：
{
  "paper": {"title":"", "subject":"", "subject_code":"", "language":"zh"},
  "questions": [
    {
      "id":"1",
      "title":"大题标题",
      "points":20,
      "numbering_description":"第1大题-整体",
      "children":[
        {
          "id":"1",
          "content":"",
          "points":2,
          "numbering_description":"第1大题-第1小题"
        }
      ]
    }
  ]
}

非叶节点使用 children；叶节点使用空 content。中间阅读材料分组也必须使用 children。"""


FILL_SYSTEM_PROMPT = """你是试卷题目提取工具。根据给定骨架和对应大题原文填充内容。

规则：
1. 严格保持骨架中的 id、层级和 children，不新增、删除、移动或合并节点。
2. 不得纠正、改写或补充原文，保留错别字、公式和标点。
3. 多题共用的正文、材料或表格放在最近共同父节点的 reference。
4. 每个叶节点的 content 只放本题题干；选项放 options，答案放 answer。
5. 题号已由 id 表示，content 不重复题号前缀。
6. 原文没有答案、题型或分值时使用空字符串或 null，不得猜测。
7. 只返回填充后的当前根节点 JSON，不要返回 Markdown 或说明文字。"""


class OpenAICompatibleStructureExtractor:
    """Extract the legacy hierarchical tree with an OpenAI-compatible API.

    The tree contract follows the internship project's `questions/children`
    representation so its prompt behavior can be migrated independently from
    the new canonical ExamDocument model.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 180,
        max_parallel_sections: int = 4,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_parallel_sections = max(1, max_parallel_sections)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def extract(self, markdown: str) -> Mapping[str, object]:
        if not self.base_url or not self.api_key or not self.model:
            raise StructureExtractionError(
                "EXAM_CHECKER_LLM_BASE_URL, EXAM_CHECKER_LLM_API_KEY and "
                "EXAM_CHECKER_LLM_MODEL are required"
            )
        if not markdown.strip():
            raise StructureExtractionError(
                "Cannot extract structure from empty Markdown"
            )
        skeleton = await self._complete_json(
            SKELETON_SYSTEM_PROMPT,
            f"<document_begin>\n{markdown}\n<document_end>",
        )
        raw_questions = skeleton.get("questions")
        if not isinstance(raw_questions, Sequence) or isinstance(
            raw_questions, (str, bytes, bytearray)
        ):
            raise StructureExtractionError(
                "Skeleton response is missing questions array"
            )
        roots: list[Mapping[str, object]] = []
        for index, node in enumerate(raw_questions):
            if not isinstance(node, Mapping):
                raise StructureExtractionError(
                    f"Skeleton questions[{index}] must be an object"
                )
            roots.append(node)
        if not roots:
            raise StructureExtractionError("Skeleton extraction returned no questions")

        windows = self._locate_section_windows(markdown, roots)
        semaphore = asyncio.Semaphore(self.max_parallel_sections)

        async def fill(
            node: Mapping[str, object], section_text: str
        ) -> Mapping[str, object]:
            async with semaphore:
                user_prompt = (
                    "<skeleton_node>\n"
                    f"{json.dumps(node, ensure_ascii=False)}\n"
                    "</skeleton_node>\n"
                    "<section_content>\n"
                    f"{section_text}\n"
                    "</section_content>"
                )
                last_error: StructureExtractionError | None = None
                for _ in range(3):
                    try:
                        candidate = await self._complete_json(
                            FILL_SYSTEM_PROMPT, user_prompt
                        )
                        self._assert_same_shape(node, candidate)
                        return candidate
                    except StructureExtractionError as exc:
                        last_error = exc
                raise StructureExtractionError(
                    f"Section filling failed after three attempts: {last_error}"
                ) from last_error

        filled = await asyncio.gather(
            *(fill(node, windows[index]) for index, node in enumerate(roots))
        )
        return {
            "paper": skeleton.get("paper")
            if isinstance(skeleton.get("paper"), Mapping)
            else {},
            "questions": filled,
        }

    async def _complete_json(
        self, system_prompt: str, user_prompt: str
    ) -> Mapping[str, object]:
        try:
            response = await self._client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
        except httpx.HTTPError as exc:
            raise StructureExtractionError(f"LLM request failed: {exc}") from exc
        if not response.is_success:
            raise StructureExtractionError(
                f"LLM request failed with HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise StructureExtractionError("LLM response has an invalid shape") from exc
        if not isinstance(content, str):
            raise StructureExtractionError("LLM response content is not text")
        return self._parse_json(content)

    @staticmethod
    def _locate_section_windows(
        markdown: str, roots: list[Mapping[str, object]]
    ) -> list[str]:
        """Locate top-level sections by their verbatim titles.

        This keeps the internship flow's deterministic locate stage. If a
        title cannot be found, only that section falls back to the full text.
        """

        starts: list[int | None] = []
        cursor = 0
        for root in roots:
            title = root.get("title")
            if not isinstance(title, str) or not title.strip():
                starts.append(None)
                continue
            position = markdown.find(title.strip(), cursor)
            if position < 0:
                starts.append(None)
                continue
            starts.append(position)
            cursor = position + len(title.strip())

        windows: list[str] = []
        for index, start in enumerate(starts):
            if start is None:
                windows.append(markdown)
                continue
            following = next(
                (
                    candidate
                    for candidate in starts[index + 1 :]
                    if candidate is not None
                ),
                len(markdown),
            )
            section = markdown[start:following].strip()
            windows.append(section or markdown)
        return windows

    @classmethod
    def _assert_same_shape(
        cls,
        expected: Mapping[str, object],
        actual: Mapping[str, object],
        *,
        path: str = "root",
    ) -> None:
        expected_id = str(expected.get("id") or "")
        actual_id = str(actual.get("id") or "")
        if expected_id != actual_id:
            raise StructureExtractionError(
                f"Filled node changed id at {path}: {expected_id!r} -> {actual_id!r}"
            )
        expected_children = expected.get("children")
        actual_children = actual.get("children")
        if isinstance(expected_children, Sequence) and not isinstance(
            expected_children, (str, bytes, bytearray)
        ):
            if not isinstance(actual_children, Sequence) or isinstance(
                actual_children, (str, bytes, bytearray)
            ):
                raise StructureExtractionError(
                    f"Filled node removed children at {path}"
                )
            if len(expected_children) != len(actual_children):
                raise StructureExtractionError(
                    f"Filled node changed child count at {path}"
                )
            for index, (expected_child, actual_child) in enumerate(
                zip(expected_children, actual_children, strict=True)
            ):
                if not isinstance(expected_child, Mapping) or not isinstance(
                    actual_child, Mapping
                ):
                    raise StructureExtractionError(
                        f"Filled child at {path}.{index} must be an object"
                    )
                cls._assert_same_shape(
                    expected_child,
                    actual_child,
                    path=f"{path}.{expected_id or index}",
                )
            return
        content = actual.get("content")
        if not isinstance(content, str) or not content.strip():
            raise StructureExtractionError(f"Filled question is empty at {path}")

    @staticmethod
    def _parse_json(content: str) -> Mapping[str, object]:
        stripped = content.strip()
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            start = stripped.find("{")
            end = stripped.rfind("}")
            if start < 0 or end <= start:
                raise StructureExtractionError(
                    "LLM did not return a JSON object"
                ) from exc
            try:
                parsed = json.loads(stripped[start : end + 1])
            except json.JSONDecodeError as nested:
                raise StructureExtractionError(
                    f"LLM returned invalid JSON: {nested}"
                ) from nested
        if not isinstance(parsed, Mapping):
            raise StructureExtractionError("LLM JSON root must be an object")
        return parsed
