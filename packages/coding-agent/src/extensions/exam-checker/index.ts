import { readFile } from "node:fs/promises";
import { basename, resolve } from "node:path";
import { Type } from "typebox";
import type { ExtensionAPI } from "../../core/extensions/types.ts";

const examCheckerParameters = Type.Object({
	operation: Type.Union(
		[Type.Literal("review_file"), Type.Literal("review_document"), Type.Literal("review_question")],
		{
			description:
				"Choose review_file for an original PDF/DOC/DOCX, review_document for every question in a parsed document, or review_question for one parsed question.",
		},
	),
	file_path: Type.Optional(
		Type.String({ description: "Original PDF, DOC, or DOCX path. Required only for review_file." }),
	),
	document_id: Type.Optional(
		Type.String({ description: "Parsed exam document ID. Required for review_document and review_question." }),
	),
	question_id: Type.Optional(Type.String({ description: "Question node ID. Required only for review_question." })),
});

export type ExamCheckerStatus = "PASS" | "NEEDS_REVISION" | "UNRESOLVED";

export interface CompactReviewError {
	error_type: string;
	position?: string;
	original_text?: string;
	correction?: string;
	description?: string;
	suggestion?: string;
	severity?: string;
}

export interface UnresolvedCheck {
	task_id: string;
	attempts: number;
	error_code: string;
	retryable: boolean;
}

export interface IngestionResult {
	status: "STORED" | "FAILED" | "SKIPPED";
	record_id?: string;
	error_code?: string;
}

export interface CompactExamCheckerResult {
	question_id: string;
	question_number: string;
	status: ExamCheckerStatus;
	errors: CompactReviewError[];
	unresolved_checks: UnresolvedCheck[];
	/** Reserved for a later storage phase. The current exam_checker tool never emits it. */
	ingestion?: IngestionResult;
}

export interface CompactDocumentReviewResult {
	document_id: string;
	status: ExamCheckerStatus;
	questions: CompactExamCheckerResult[];
}

type ExamCheckerErrorCode =
	| "EXAM_CHECKER_UNAVAILABLE"
	| "EXAM_FILE_UNAVAILABLE"
	| "INVALID_EXAM_CHECKER_INPUT"
	| "INVALID_EXAM_CHECKER_RESPONSE";

export interface ExamCheckerToolDetails {
	fullReview?: unknown;
	parsedDocument?: unknown;
	error?: {
		code: ExamCheckerErrorCode;
		status?: number;
	};
}

class ExamCheckerToolError extends Error {
	readonly code: ExamCheckerErrorCode;
	readonly status: number | undefined;

	constructor(code: ExamCheckerErrorCode, status?: number) {
		super(code);
		this.code = code;
		this.status = status;
	}
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(record: Record<string, unknown>, key: string): string {
	const value = record[key];
	if (typeof value !== "string" || value.length === 0) {
		throw new ExamCheckerToolError("INVALID_EXAM_CHECKER_RESPONSE");
	}
	return value;
}

function optionalString(record: Record<string, unknown>, key: string): string | undefined {
	const value = record[key];
	return typeof value === "string" && value.length > 0 ? value : undefined;
}

function reviewStatus(record: Record<string, unknown>): ExamCheckerStatus {
	const status = requiredString(record, "status");
	if (status !== "PASS" && status !== "NEEDS_REVISION" && status !== "UNRESOLVED") {
		throw new ExamCheckerToolError("INVALID_EXAM_CHECKER_RESPONSE");
	}
	return status;
}

function compactError(value: unknown): CompactReviewError {
	if (!isRecord(value)) throw new ExamCheckerToolError("INVALID_EXAM_CHECKER_RESPONSE");
	return {
		error_type: requiredString(value, "error_type"),
		...(optionalString(value, "position") ? { position: optionalString(value, "position") } : {}),
		...(optionalString(value, "original_text") ? { original_text: optionalString(value, "original_text") } : {}),
		...(optionalString(value, "correction") ? { correction: optionalString(value, "correction") } : {}),
		...(optionalString(value, "description") ? { description: optionalString(value, "description") } : {}),
		...(optionalString(value, "suggestion") ? { suggestion: optionalString(value, "suggestion") } : {}),
		...(optionalString(value, "severity") ? { severity: optionalString(value, "severity") } : {}),
	};
}

function compactUnresolvedCheck(value: unknown): UnresolvedCheck | undefined {
	if (!isRecord(value) || value.execution_status !== "FAILED") return undefined;
	return {
		task_id: requiredString(value, "task_id"),
		attempts: typeof value.attempts === "number" ? value.attempts : 1,
		error_code: optionalString(value, "error_code") ?? "SPECIALIST_FAILED",
		retryable: value.retryable === true,
	};
}

export function compactReviewResult(value: unknown): CompactExamCheckerResult {
	if (!isRecord(value) || !isRecord(value.review)) {
		throw new ExamCheckerToolError("INVALID_EXAM_CHECKER_RESPONSE");
	}
	const review = value.review;
	if (!isRecord(review.summary) || !Array.isArray(review.summary.errors) || !Array.isArray(review.checks)) {
		throw new ExamCheckerToolError("INVALID_EXAM_CHECKER_RESPONSE");
	}

	const unresolvedChecks = review.checks
		.map(compactUnresolvedCheck)
		.filter((check): check is UnresolvedCheck => check !== undefined);
	return {
		question_id: requiredString(value, "question_id"),
		question_number: requiredString(value, "question_number"),
		status: reviewStatus(review),
		errors: review.summary.errors.map(compactError),
		unresolved_checks: unresolvedChecks,
	};
}

export function compactDocumentReviewResult(value: unknown): CompactDocumentReviewResult {
	if (!isRecord(value) || !Array.isArray(value.questions)) {
		throw new ExamCheckerToolError("INVALID_EXAM_CHECKER_RESPONSE");
	}
	return {
		document_id: requiredString(value, "document_id"),
		status: reviewStatus(value),
		questions: value.questions.map(compactReviewResult),
	};
}

function serviceUrl(): string {
	return (process.env.EXAM_CHECKER_SERVICE_URL || "http://127.0.0.1:8091").replace(/\/$/, "");
}

async function requestJson(url: string, init: RequestInit): Promise<unknown> {
	let response: Response;
	try {
		response = await fetch(url, init);
	} catch {
		throw new ExamCheckerToolError("EXAM_CHECKER_UNAVAILABLE");
	}
	if (!response.ok) {
		throw new ExamCheckerToolError("EXAM_CHECKER_UNAVAILABLE", response.status);
	}
	try {
		return await response.json();
	} catch {
		throw new ExamCheckerToolError("INVALID_EXAM_CHECKER_RESPONSE");
	}
}

function requireOnly(value: string | undefined, forbidden: Array<string | undefined>): string {
	if (!value || forbidden.some((item) => item !== undefined)) {
		throw new ExamCheckerToolError("INVALID_EXAM_CHECKER_INPUT");
	}
	return value;
}

export default function examCheckerExtension(pi: ExtensionAPI): void {
	pi.registerTool<typeof examCheckerParameters, ExamCheckerToolDetails>({
		name: "exam_checker",
		label: "exam checker",
		description:
			"Run the exam-checker workflow for an original PDF/DOC/DOCX file, every question in a parsed exam, or one parsed question.",
		promptSnippet: "Parse and review exam files, parsed exams, or individual parsed questions",
		promptGuidelines: [
			"Use exam_checker with review_file when the user provides an original PDF, DOC, or DOCX file; use review_document for every question in an existing document_id; use review_question only when both document_id and question_id identify one question.",
			"Treat exam_checker.status as the sole review decision: PASS may continue to a later ingestion flow; NEEDS_REVISION must be returned to the user with the reported errors; UNRESOLVED means review is incomplete.",
			"For UNRESOLVED results, report unresolved_checks and never claim that the question or document passed.",
			"Do not override exam_checker.status or invent errors that are absent from its result.",
		],
		parameters: examCheckerParameters,
		executionMode: "parallel",
		async execute(_toolCallId, params, signal, _onUpdate, ctx) {
			try {
				if (params.operation === "review_question") {
					const documentId = requireOnly(params.document_id, [params.file_path]);
					if (!params.question_id) throw new ExamCheckerToolError("INVALID_EXAM_CHECKER_INPUT");
					const fullReview = await requestJson(
						`${serviceUrl()}/v1/exam-checker/documents/${encodeURIComponent(documentId)}/questions/${encodeURIComponent(params.question_id)}/review`,
						{ method: "POST", signal },
					);
					return {
						content: [{ type: "text", text: JSON.stringify(compactReviewResult(fullReview)) }],
						details: { fullReview },
					};
				}

				let documentId: string;
				let parsedDocument: unknown;
				if (params.operation === "review_file") {
					const filePath = requireOnly(params.file_path, [params.document_id, params.question_id]);
					const absolutePath = resolve(ctx.cwd, filePath);
					let bytes: Uint8Array;
					try {
						bytes = new Uint8Array(await readFile(absolutePath));
					} catch {
						throw new ExamCheckerToolError("EXAM_FILE_UNAVAILABLE");
					}
					const form = new FormData();
					form.append("file", new Blob([bytes]), basename(absolutePath));
					parsedDocument = await requestJson(`${serviceUrl()}/v1/exam-checker/documents/parse`, {
						method: "POST",
						body: form,
						signal,
					});
					if (!isRecord(parsedDocument)) {
						throw new ExamCheckerToolError("INVALID_EXAM_CHECKER_RESPONSE");
					}
					documentId = requiredString(parsedDocument, "document_id");
				} else {
					documentId = requireOnly(params.document_id, [params.file_path, params.question_id]);
				}

				const fullReview = await requestJson(
					`${serviceUrl()}/v1/exam-checker/documents/${encodeURIComponent(documentId)}/review`,
					{ method: "POST", signal },
				);
				return {
					content: [{ type: "text", text: JSON.stringify(compactDocumentReviewResult(fullReview)) }],
					details: parsedDocument === undefined ? { fullReview } : { fullReview, parsedDocument },
				};
			} catch (error) {
				const known =
					error instanceof ExamCheckerToolError
						? error
						: new ExamCheckerToolError("INVALID_EXAM_CHECKER_RESPONSE");
				return {
					content: [{ type: "text", text: JSON.stringify({ error: known.code }) }],
					details: { error: { code: known.code, ...(known.status ? { status: known.status } : {}) } },
					isError: true,
				};
			}
		},
	});
}
