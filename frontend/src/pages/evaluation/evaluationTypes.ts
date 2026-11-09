import {
  Brain,
  Braces,
  FileSearch,
  Gauge,
  Layers3,
  MessageSquareText,
  ScrollText,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import type { DatasetType } from "../../types/api";

export interface EvaluationType {
  key: string;
  title: string;
  description: string;
  icon: LucideIcon;
  /** Milestone that implements it (spec §17). */
  milestone: number;
  /** Dataset types that feed it; empty = any dataset. */
  datasetTypes: DatasetType[];
}

export const EVALUATION_TYPES: EvaluationType[] = [
  {
    key: "performance",
    title: "Performance",
    description: "TTFT, latency percentiles, tokens/s and throughput under load.",
    icon: Gauge,
    milestone: 3,
    datasetTypes: [],
  },
  {
    key: "answer_quality",
    title: "Answer quality",
    description: "Compare answers with references using deterministic checks and judges.",
    icon: MessageSquareText,
    milestone: 4,
    datasetTypes: ["qa", "chat", "instruction"],
  },
  {
    key: "structured_output",
    title: "Structured output",
    description: "JSON validity and schema conformance.",
    icon: Braces,
    milestone: 4,
    datasetTypes: ["structured_output"],
  },
  {
    key: "tool_calling",
    title: "Tool-calling",
    description: "Tool selection, argument accuracy and schema validity.",
    icon: Wrench,
    milestone: 4,
    datasetTypes: ["tool_call"],
  },
  {
    key: "rag",
    title: "RAG evaluation",
    description: "Faithfulness, citation accuracy and abstention on supplied contexts.",
    icon: FileSearch,
    milestone: 5,
    datasetTypes: ["rag"],
  },
  {
    key: "hallucination",
    title: "Hallucination check",
    description: "Unsupported and contradicted claims relative to the evidence.",
    icon: Brain,
    milestone: 5,
    datasetTypes: ["rag", "qa"],
  },
  {
    key: "quantization",
    title: "Quantization",
    description: "Compare FP16, INT8, 4-bit variants of the same model.",
    icon: Layers3,
    milestone: 7,
    datasetTypes: [],
  },
  {
    key: "context_window",
    title: "Context window",
    description: "Needle retrieval and quality as context length grows.",
    icon: ScrollText,
    milestone: 7,
    datasetTypes: ["long_context"],
  },
];

export const evaluationType = (key: string) => EVALUATION_TYPES.find((t) => t.key === key);

/** Evaluation types the backend can run today (must match IMPLEMENTED_SUITES in schemas/run.py). */
export const IMPLEMENTED_TYPES = ["performance", "answer_quality", "structured_output", "tool_calling"];
