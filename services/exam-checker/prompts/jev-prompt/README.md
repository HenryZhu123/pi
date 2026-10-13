# Jev error-type routing prompts

This directory contains the prompt material for the question-level Jev router.
The runtime loads only tasks marked `locked` in `locked-baselines.json`; all other
error types go directly to their specialist LLM. Each locked task is sent as an
independent Jev request so its failure, latency, threshold, and metrics remain
isolated.

The task boundaries are derived from the frozen Qwen3.8-27B production profile
`system-qwen38-27b-production-20260917` (version
`qwen38-27b-production-20260917-v3`). The source mode selected by that profile is:

- `disabled`: typo, ambiguity, options, mismatch, missing
- `explicit`: punctuation, unsolvability, answer leak, fact/logic

Do not replace these boundaries with the shorter generic prompts under
`backend/assets/prompt/detection/` without a new boundary review.

The router does not decide whether an error definitely exists. It decides whether
the corresponding specialist checker should run:

- `RUN`: there is a visible risk signal that falls within the corresponding
  Qwen specialist's detection scope.
- `SKIP`: the task-specific checks were completed, no relevant signal was found,
  and the available text is sufficient to exclude the specialist check.

Load `shared-routing-rules.txt` into the request state as `routing_rules`. Load each
task JSON without changing its `task_id`, `instructions`, or `criteria` fields.
Thresholds are deliberately not embedded in these prompts. They must be calibrated
per error type from evaluation data and applied by the caller to Jev's returned
`probabilities.SKIP` value.

## Threshold calibration priority

Jev is a recall-first pre-check router. Its primary objective is to avoid skipping
questions that actually contain the target error, because a false negative prevents
the specialist checker from seeing the question at all. Select thresholds in this
order:

1. maximize error-question recall and minimize FNR/FRR;
2. among thresholds with comparable recall, prefer higher F2 and a higher normal-
   question skip rate;
3. treat precision and normal-question false-positive rate as secondary efficiency
   constraints, not as reasons to accept materially lower recall.

A threshold may remain useful even when some normal questions are routed to the
specialist. For example, a 15% normal-question false-positive rate still means that
about 85% of normal questions bypass that specialist. Report both the normal-question
skip rate (`TN / all normal questions`) and the overall skip rate so the efficiency
gain is not confused with precision.

Calibrated prompt hashes and per-task thresholds that have been accepted as frozen
baselines are recorded in `locked-baselines.json`. A locked prompt must not be
edited in place. Recalibration requires a new evaluation result and an explicit
baseline update.

Current locked tasks are `options_check`, `mismatch_check`, and
`answer_leak_check`. Router failure always fails open to the corresponding
specialist LLM.

Recommended state fields:

```json
{
  "routing_rules": "<contents of shared-routing-rules.txt>",
  "reference": {
    "subject": "计算机科学",
    "question_type": "单项选择题",
    "paper_title": "期末考试"
  },
  "detection_content": "题干与选项文本"
}
```

`reference` is context only. The router must not report errors in it.

