import {
  BookOpenCheck,
  Braces,
  FileSearch,
  Layers,
  ListChecks,
  MessagesSquare,
  ScrollText,
  ShieldAlert,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import type { DatasetType } from "../../types/api";

export const DATASET_TYPE_META: Record<DatasetType, { label: string; icon: LucideIcon; hint: string }> = {
  qa: { label: "Q&A (reference answer)", icon: BookOpenCheck, hint: "input + reference_answer" },
  chat: { label: "General chat", icon: MessagesSquare, hint: "input or messages" },
  instruction: { label: "Instruction following", icon: ListChecks, hint: "input + optional constraints" },
  structured_output: { label: "Structured output", icon: Braces, hint: "input + json_schema" },
  tool_call: { label: "Tool calling", icon: Wrench, hint: "input + tools + expected_tool_calls" },
  rag: { label: "RAG evaluation", icon: FileSearch, hint: "input + retrieved_contexts" },
  long_context: { label: "Long-context retrieval", icon: ScrollText, hint: "input + context + expected_answer" },
  safety: { label: "Safety / refusal", icon: ShieldAlert, hint: "input + expected_behavior" },
  mixed: { label: "Mixed", icon: Layers, hint: "every record sets its own type" },
};

export const DATASET_TYPES = Object.keys(DATASET_TYPE_META) as DatasetType[];

export const typeLabel = (t: string) => DATASET_TYPE_META[t as DatasetType]?.label ?? t;

export const shortHash = (hash: string) => `${hash.slice(0, 12)}…`;

/** First line of a case's prompt, for tables. */
export function promptSnippet(content: Record<string, unknown>, max = 110): string {
  let text = "";
  if (typeof content.input === "string") text = content.input;
  else if (Array.isArray(content.messages)) {
    const users = (content.messages as { role: string; content: string }[]).filter((m) => m.role === "user");
    text = users[users.length - 1]?.content ?? "";
  }
  text = text.replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max)}…` : text;
}
