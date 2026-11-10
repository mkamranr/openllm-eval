export type ReportFormat = "html" | "json" | "csv" | "metrics_csv" | "markdown";

export interface Report {
  id: string;
  run_id: string;
  run_name: string | null;
  report_type: ReportFormat;
  storage_location: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  options: { redact_content?: boolean; redact_endpoints?: boolean };
  report_metadata: {
    report_version?: string;
    run_status?: string;
    results_total?: number;
    results_included?: number;
    results_truncated?: boolean;
    warnings?: number;
  };
  generated_at: string;
}

export interface ReportCreate {
  format: ReportFormat;
  redact_content: boolean;
  redact_endpoints: boolean;
}

export type Comparability = "equivalent_configuration" | "partially_controlled" | "not_directly_comparable";
export type Severity = "critical" | "major" | "minor" | "info";

export interface ComparisonSubject {
  id: string;
  run_id: string;
  run_name: string;
  run_status: string;
  run_completed_at: string | null;
  model_name: string;
  label: string;
  index: number;
  config: Record<string, unknown>;
}

export interface ConfigDifference {
  field: string;
  label: string;
  aspect: "performance" | "quality" | "both" | "subject";
  severity: Severity;
  values: Record<string, unknown>;
}

export interface ComparedValue {
  subject_id: string;
  value: number | null;
  sample_count: number | null;
  unavailable_reason: string | null;
  delta: number | null;
  relative_change: number | null;
  improved: boolean | null;
}

export interface ComparedMetric {
  metric_name: string;
  aggregation: string;
  unit: string;
  family: "performance" | "quality" | "tool" | "rag" | "hallucination" | "context" | "resources";
  evaluator: string | null;
  better: "higher" | "lower" | null;
  values: ComparedValue[];
}

export interface EvaluatorTransitions {
  pass_to_fail_total: number;
  pass_to_fail: string[];
  fail_to_pass_total: number;
  fail_to_pass: string[];
}

export interface Transition {
  subject_id: string;
  shared_cases: number;
  only_in_baseline: number;
  only_in_subject: number;
  datasets_identical: boolean;
  new_errors_total: number;
  new_errors: { case_identifier: string; error_type: string | null }[];
  fixed_errors_total: number;
  fixed_errors: { case_identifier: string; error_type: string | null }[];
  evaluators: Record<string, EvaluatorTransitions>;
}

export interface ComparisonResult {
  run_ids: string[];
  baseline_subject_id: string;
  subjects: ComparisonSubject[];
  comparability: {
    overall: Comparability;
    performance: Comparability;
    quality: Comparability;
    differences: ConfigDifference[];
  };
  metrics: ComparedMetric[];
  transitions: Transition[];
  /** Deployment variants (quantization); absent in comparisons saved before Milestone 7. */
  variants?: Variants;
  notes: string[];
}

export interface VariantField {
  key: string;
  label: string;
  group: "model" | "deployment";
  /** null = not declared (unknown). */
  values: Record<string, unknown>;
  differs: boolean;
  unknown_for: string[];
  all_known: boolean;
}

export interface Variants {
  fields: VariantField[];
  warnings: { level: "warning" | "info"; message: string }[];
  key_metrics: (ComparedMetric & { label: string })[];
  context_lengths: number[];
  context: {
    context_length: number;
    label: string;
    metric_name: string;
    aggregation: string;
    values: Record<string, { value: number | null; unavailable_reason: string | null }>;
  }[];
}

export interface SavedComparison {
  id: string;
  project_id: string | null;
  name: string;
  description: string | null;
  run_ids: string[];
  baseline_run_id: string | null;
  created_at: string;
  updated_at: string;
  result?: ComparisonResult | null;
  error?: string | null;
}
