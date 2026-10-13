# Exam Checker Service

The service provides document parsing and question-oriented review. It is intentionally not registered as a Pi tool yet.

Pipeline:

1. Accept PDF, DOC, or DOCX.
2. Convert Word documents to PDF with LibreOffice.
3. Extract Markdown through MinerU's token-free Agent API.
4. Ask an OpenAI-compatible model to extract the hierarchical exam tree.
5. Normalize the result into one `ExamDocument`.
6. Build a per-question checker payload.
7. Run Jev-gated and direct specialist checks concurrently.
8. Aggregate all nine error types into one question review result.

## Run

```bash
cd services/exam-checker
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
.venv/bin/exam-checker-service
```

Environment variables are not loaded from `.env` automatically. Export them in the shell or use your process manager.

## API

- `POST /v1/exam-checker/documents/parse`
- `GET /v1/exam-checker/documents/{document_id}`
- `GET /v1/exam-checker/documents/{document_id}/questions`
- `GET /v1/exam-checker/documents/{document_id}/questions/{question_id}`
- `GET /v1/exam-checker/documents/{document_id}/questions/{question_id}/checker-payload`
- `POST /v1/exam-checker/documents/{document_id}/questions/{question_id}/review`

The MinerU Agent API accepts files up to 10 MB and PDFs up to 20 pages. It is IP-rate-limited and returns only Markdown.

## Review scheduling

The review phase is question-oriented, not error-type-oriented:

1. Read one normalized question by `question_id` and build its checker payload.
2. Run Jev independently for `options_check`, `mismatch_check`, and `answer_leak_check`.
3. Skip a gated specialist only when `p_skip` reaches its locked threshold. Low-confidence or failed routing fails open to the specialist LLM.
4. Start all six direct specialist checks without waiting for Jev.
5. Aggregate every specialist result for the question into one JSON object and return it as the question result.
6. Review multiple questions concurrently. The global maximum concurrency is configuration-driven and will be fixed only after load testing.

Each specialist model call remains scoped to one question and one error type so that prompts, retries, metrics, and thresholds can be tuned independently. The scheduler may execute different questions concurrently, but it must not expose partial specialist outputs as the final question result.

Locked thresholds:

- `options_check`: `0.70`
- `mismatch_check`: `0.95`
- `answer_leak_check`: `0.99`

The response separates whole-question state from per-task execution:

```json
{
  "document_id": "exam_...",
  "question_id": "question_1",
  "question": {
    "reference": "【学科】计算机科学\n【题型】选择题",
    "detection_content": "题目原文"
  },
  "review": {
    "status": "PASS",
    "routing": {
      "options_check": {
        "router": "jev",
        "decision": "SKIP",
        "p_skip": 0.93,
        "skip_threshold": 0.7
      }
    },
    "checks": [
      {
        "task_id": "options_check",
        "routing_mode": "jev",
        "model": null,
        "execution_status": "SKIPPED_BY_ROUTER",
        "result": null
      }
    ],
    "summary": {
      "has_error": false,
      "total_errors": 0,
      "errors": [],
      "unresolved_checks": []
    }
  }
}
```

`PASS` requires every non-skipped check to complete. Any specialist failure produces `UNRESOLVED`; detected errors produce `NEEDS_REVISION`. Knowledge-point and difficulty annotation are not part of this phase.
