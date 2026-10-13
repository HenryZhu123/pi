import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExtensionAPI } from "../src/core/extensions/types.ts";
import examCheckerExtension, {
	compactDocumentReviewResult,
	compactReviewResult,
} from "../src/extensions/exam-checker/index.ts";

interface RegisteredExamCheckerTool {
	execute: (
		toolCallId: string,
		params: {
			operation: "review_file" | "review_document" | "review_question";
			file_path?: string;
			document_id?: string;
			question_id?: string;
		},
		signal: AbortSignal | undefined,
		onUpdate: undefined,
		ctx: { cwd: string },
	) => Promise<{ content: Array<{ type: string; text?: string }>; isError?: boolean }>;
}

function registeredTool(): RegisteredExamCheckerTool {
	let tool: unknown;
	examCheckerExtension({
		registerTool: (definition: unknown) => {
			tool = definition;
		},
	} as unknown as ExtensionAPI);
	return tool as RegisteredExamCheckerTool;
}

afterEach(() => {
	vi.restoreAllMocks();
});

function fullReview(status: "PASS" | "NEEDS_REVISION" | "UNRESOLVED") {
	return {
		document_id: "exam_1",
		question_id: "question_2",
		question_number: "2",
		question: { reference: "ignored", detection_content: "ignored" },
		review: {
			status,
			routing: { options_check: { p_skip: 0.99 } },
			checks: [] as unknown[],
			summary: { has_error: false, total_errors: 0, errors: [] as unknown[], unresolved_checks: [] },
			timing_ms: { total: 123 },
		},
	};
}

describe("compactReviewResult", () => {
	it("returns only model-visible decision fields for a passing question", () => {
		const result = compactReviewResult(fullReview("PASS"));

		expect(result).toEqual({
			question_id: "question_2",
			question_number: "2",
			status: "PASS",
			errors: [],
			unresolved_checks: [],
		});
		expect(result).not.toHaveProperty("ingestion");
		expect(result).not.toHaveProperty("document_id");
	});

	it("removes empty and audit-only fields from reported errors", () => {
		const input = fullReview("NEEDS_REVISION");
		input.review.summary = {
			has_error: true,
			total_errors: 1,
			errors: [
				{
					error_type: "选项错误",
					position: "选项 C",
					original_text: "C. TCP 是无连接协议",
					anchor_text: "must stay in details only",
					correction: "C. TCP 是面向连接的协议",
					description: "协议性质错误",
					suggestion: "修改选项 C",
					severity: "",
				},
			],
			unresolved_checks: [],
		};

		expect(compactReviewResult(input).errors).toEqual([
			{
				error_type: "选项错误",
				position: "选项 C",
				original_text: "C. TCP 是无连接协议",
				correction: "C. TCP 是面向连接的协议",
				description: "协议性质错误",
				suggestion: "修改选项 C",
			},
		]);
	});

	it("reports retry metadata for unresolved checks", () => {
		const input = fullReview("UNRESOLVED");
		input.review.checks = [
			{
				task_id: "logic_check",
				execution_status: "FAILED",
				attempts: 3,
				error_code: "RATE_LIMITED",
				retryable: true,
				error: "provider response omitted",
			},
		];

		expect(compactReviewResult(input).unresolved_checks).toEqual([
			{ task_id: "logic_check", attempts: 3, error_code: "RATE_LIMITED", retryable: true },
		]);
	});

	it("compacts every question in a document review", () => {
		const first = fullReview("PASS");
		const second = fullReview("NEEDS_REVISION");
		second.question_id = "question_3";
		second.question_number = "3";

		expect(
			compactDocumentReviewResult({
				document_id: "exam_1",
				status: "NEEDS_REVISION",
				questions: [first, second],
			}),
		).toEqual({
			document_id: "exam_1",
			status: "NEEDS_REVISION",
			questions: [
				{
					question_id: "question_2",
					question_number: "2",
					status: "PASS",
					errors: [],
					unresolved_checks: [],
				},
				{
					question_id: "question_3",
					question_number: "3",
					status: "NEEDS_REVISION",
					errors: [],
					unresolved_checks: [],
				},
			],
		});
	});

	it("routes review_document to the whole-document service endpoint", async () => {
		const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
			new Response(
				JSON.stringify({
					document_id: "exam_1",
					status: "PASS",
					questions: [fullReview("PASS")],
				}),
				{ status: 200, headers: { "content-type": "application/json" } },
			),
		);

		const result = await registeredTool().execute(
			"call_1",
			{ operation: "review_document", document_id: "exam_1" },
			undefined,
			undefined,
			{ cwd: "/workspace" },
		);

		expect(fetchMock).toHaveBeenCalledWith(
			"http://127.0.0.1:8091/v1/exam-checker/documents/exam_1/review",
			expect.objectContaining({ method: "POST" }),
		);
		expect(JSON.parse(result.content[0]?.text ?? "{}")).toMatchObject({
			document_id: "exam_1",
			status: "PASS",
		});
	});

	it("routes review_question to the single-question service endpoint", async () => {
		const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
			new Response(JSON.stringify(fullReview("PASS")), {
				status: 200,
				headers: { "content-type": "application/json" },
			}),
		);

		await registeredTool().execute(
			"call_question",
			{ operation: "review_question", document_id: "exam_1", question_id: "question_2" },
			undefined,
			undefined,
			{ cwd: "/workspace" },
		);

		expect(fetchMock).toHaveBeenCalledWith(
			"http://127.0.0.1:8091/v1/exam-checker/documents/exam_1/questions/question_2/review",
			expect.objectContaining({ method: "POST" }),
		);
	});

	it("parses review_file once and then reviews the parsed document", async () => {
		const directory = await mkdtemp(join(tmpdir(), "exam-checker-"));
		const filePath = join(directory, "exam.pdf");
		await writeFile(filePath, "test pdf bytes");
		try {
			const fetchMock = vi
				.spyOn(globalThis, "fetch")
				.mockResolvedValueOnce(
					new Response(JSON.stringify({ document_id: "exam_from_file" }), {
						status: 200,
						headers: { "content-type": "application/json" },
					}),
				)
				.mockResolvedValueOnce(
					new Response(
						JSON.stringify({
							document_id: "exam_from_file",
							status: "PASS",
							questions: [fullReview("PASS")],
						}),
						{ status: 200, headers: { "content-type": "application/json" } },
					),
				);

			await registeredTool().execute(
				"call_file",
				{ operation: "review_file", file_path: filePath },
				undefined,
				undefined,
				{ cwd: "/workspace" },
			);

			expect(fetchMock).toHaveBeenNthCalledWith(
				1,
				"http://127.0.0.1:8091/v1/exam-checker/documents/parse",
				expect.objectContaining({ method: "POST", body: expect.any(FormData) }),
			);
			expect(fetchMock).toHaveBeenNthCalledWith(
				2,
				"http://127.0.0.1:8091/v1/exam-checker/documents/exam_from_file/review",
				expect.objectContaining({ method: "POST" }),
			);
		} finally {
			await rm(directory, { recursive: true, force: true });
		}
	});

	it("rejects parameters that conflict with the selected operation", async () => {
		const fetchMock = vi.spyOn(globalThis, "fetch");

		const result = await registeredTool().execute(
			"call_2",
			{
				operation: "review_question",
				document_id: "exam_1",
				question_id: "question_1",
				file_path: "exam.pdf",
			},
			undefined,
			undefined,
			{ cwd: "/workspace" },
		);

		expect(fetchMock).not.toHaveBeenCalled();
		expect(result.isError).toBe(true);
		expect(JSON.parse(result.content[0]?.text ?? "{}")).toEqual({ error: "INVALID_EXAM_CHECKER_INPUT" });
	});
});
