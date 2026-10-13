import type { InlineExtension } from "../core/extensions/types.ts";
import examCheckerExtension from "./exam-checker/index.ts";
import llamaExtension from "./llama/index.ts";

export const builtInExtensions: InlineExtension[] = [
	{ name: "exam-checker", factory: examCheckerExtension },
	{ name: "llama.cpp", factory: llamaExtension, hidden: true },
];
