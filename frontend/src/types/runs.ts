// Benchmark runs (Milestone 3). Mirrors backend/app/schemas/run.py.

export type RunStatus =
  | "created"
  | "queued"
  | "initializing"
  | "running"
  | "finalizing"
  | "cancel_requested"
  | "completed"
  | "completed_with_errors"
  | "cancelled"
  | "failed";

export const ACTIVE_STATUSES: RunStatus[] = ["initializing", "running", "finalizing", "cancel_requested"];
export const PENDING_STATUSES: RunStatus[] = ["created", "queued"];
export const TERMINAL_STATUSES: RunStatus[] = ["completed", "completed_with_errors", "cancelled", "failed"];

export type LoadProfile = "smoke" | "sequential" | "fixed_concurrency" | "ramp" | "sustained";

export interface GenerationConfig {
  temperature: number | null;
  top_p: number | null;
  max_tokens: number | null;
  seed: number | null;
}

export interface LoadConfig {
  profile: LoadProfile;
  requests: number | null;
  concurrency: number;
  warmup_requests: number;
  stream: boolean;
  request_timeout_seconds: number | null;
  max_retries: number | null;
  ramp_concurrency: number[];
  requests_per_step: number | null;
  duration_seconds: number | null;
}

export interface LoadStep {
  step: number | null;
  concurrency: number;
  start: number;
  count: number | null;
  duration_seconds: number | null;
}

export interface ResolvedLoad {
  profile: LoadProfile;
  concurrency: number;
  requests: number | null;
  warmup_requests: number;
  stream: boolean;
  request_timeout_seconds: number | null;
  max_retries: number | null;
  duration_seconds: number | null;
  steps: LoadStep[];
  case_count: number;
  case_order: string;
  warmup_policy: string;
  endpoint_order: string;
}

export interface LiveStats {
  latency_ms_p50: number | null;
  latency_ms_p95: number | null;
  ttft_ms_p50: number | null;
  ttft_ms_p95: number | null;
  output_tokens_per_second_p50: number | null;
  request_throughput: number | null;
}

export interface ModelProgress {
  phase?: "pending" | "warmup" | "measured" | "judging" | "done";
  planned?: number | null;
  completed?: number;
  succeeded?: number;
  failed?: number;
  retries?: number;
  in_flight?: number;
  warmup_planned?: number;
  warmup_completed?: number;
  current_step?: number | null;
  current_concurrency?: number | null;
  elapsed_s?: number | null;
  judge_planned?: number;
  judge_completed?: number;
  live?: LiveStats;
}

export interface RunProgress {
  pass_number?: number;
  models_total?: number;
  models_done?: number;
  current_run_model_id?: string | null;
  phase?: string | null;
  planned?: number | null;
  completed?: number;
  succeeded?: number;
  failed?: number;
  in_flight?: number;
  updated_at?: string;
}

export interface RunModel {
  id: string;
  position: number;
  endpoint_id: string | null;
  name: string;
  model_identifier: string;
  adapter_type: string;
  base_url: string;
  status: "pending" | "running" | "completed" | "cancelled";
  progress: ModelProgress;
  started_at: string | null;
  completed_at: string | null;
  declared_model_metadata: Record<string, unknown>;
  hardware_metadata: Record<string, unknown>;
}

export interface RunDatasetRef {
  dataset_id: string | null;
  dataset_version_id: string | null;
  name: string;
  dataset_type: string;
  version_number: number;
  content_hash: string;
  record_count: number;
  available: boolean;
}

export interface ErrorSummary {
  message?: string;
  type?: string;
  failed_requests?: number;
  not_completed?: number;
  errors?: Record<string, number>;
}

export interface Run {
  id: string;
  project_id: string | null;
  name: string;
  description: string | null;
  status: RunStatus;
  suites: string[];
  framework_version: string;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  cancel_requested_at: string | null;
  error_summary: ErrorSummary | null;
  progress: RunProgress;
  pass_count: number;
  retry_failed_requested: boolean;
  load: ResolvedLoad;
  models: RunModel[];
  datasets: RunDatasetRef[];
}

export interface RunSnapshot extends Record<string, unknown> {
  sampling?: "greedy" | "stochastic" | "server_default";
  generation?: GenerationConfig;
  framework_version?: string;
  metrics_version?: string;
  prompt_builder_version?: string;
  evaluation?: EvaluationSnapshot;
  profile?: { id: string; name: string } | null;
}

export interface RunDetail extends Run {
  configuration_snapshot: RunSnapshot;
}

export interface RunCreate {
  name: string;
  description?: string | null;
  project_id?: string | null;
  endpoint_ids: string[];
  datasets: { dataset_id: string; version_id?: string }[];
  suites?: string[];
  generation?: Partial<GenerationConfig>;
  load?: Partial<LoadConfig>;
  evaluation?: Partial<EvaluationConfig>;
  profile_id?: string | null;
}

// ------------------------------------------------------------------- evaluation (Milestone 4)

export type ToolMode = "simulated" | "generation_only";

export interface EvaluationConfig {
  tool_mode: ToolMode;
  max_tool_rounds: number;
  token_f1_threshold: number;
  allow_code_fence: boolean;
  disabled_evaluators: string[];
  embedding: { endpoint_id: string; threshold: number } | null;
  faithfulness_threshold: number;
  support_threshold: number;
  judge: JudgeConfig | null;
}

/** Optional LLM judge for the RAG and hallucination suites (opt-in). */
export interface JudgeConfig {
  endpoint_id: string;
  temperature?: number;
  max_tokens?: number;
  concurrency?: number;
}

export type EvaluationStatus = "completed" | "failed" | "skipped" | "not_applicable" | "requires_review";

export interface EvaluationSummary {
  evaluator_name: string;
  suite: string;
  status: EvaluationStatus;
  score: number | null;
  passed: boolean | null;
  label: string | null;
}

export interface EvaluationResult extends EvaluationSummary {
  id: string;
  evaluator_version: string;
  rationale: string | null;
  evidence: Record<string, unknown>[];
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface EvaluatorInfo {
  name: string;
  version: string;
  suite: string;
  kind: string;
  title: string;
  description: string;
  case_types: string[];
  optional: boolean;
}

export interface EvaluationSnapshot {
  suites: string[];
  evaluators: { name: string; version: string; suite: string; kind: string }[];
  config: EvaluationConfig;
  framework_version?: string;
  metrics_version?: string;
  tool_execution?: string | null;
  embedding_endpoint?: { name: string; model_identifier: string } | null;
  judge_endpoint?: { name: string; model_identifier: string; judges_tested_model?: boolean } | null;
  judge_prompt_version?: string | null;
  evidence_method?: "lexical" | "judge" | null;
}

export interface Profile {
  id: string;
  project_id: string | null;
  name: string;
  description: string | null;
  suites: string[];
  evaluation: Partial<EvaluationConfig>;
  generation: Partial<GenerationConfig>;
  load: Partial<LoadConfig>;
  created_at: string;
  updated_at: string;
}

export interface ProfileInput {
  name: string;
  description?: string | null;
  project_id?: string | null;
  suites: string[];
  evaluation: Partial<EvaluationConfig>;
  generation: Partial<GenerationConfig>;
  load: Partial<LoadConfig>;
}

export interface SimulationRound {
  round: number;
  tool_results: { name: string | null; arguments: unknown; fixture_matched: boolean; valid: boolean; result: string }[];
  latency_ms: number | null;
  ok: boolean;
  error: string | null;
  next_tool_calls: (string | null)[];
}

export interface Simulation {
  mode: "simulated";
  executed: false;
  completed: boolean;
  rounds: SimulationRound[];
  final_content: string | null;
  stop_reason?: string;
  error_kind?: string;
}

export interface RunSummary {
  total: number;
  by_status: Partial<Record<RunStatus, number>>;
  queued: number;
  active: number;
}

export interface CaseResult {
  id: string;
  run_model_id: string;
  dataset_case_id: string | null;
  case_identifier: string;
  sequence: number;
  phase: "warmup" | "measured";
  request_index: number;
  attempt_number: number;
  pass_number: number;
  load_step: number | null;
  is_final: boolean;
  is_warmup: boolean;
  status: "succeeded" | "failed";
  error_type: string | null;
  error_message: string | null;
  http_status: number | null;
  latency_ms: number | null;
  ttft_ms: number | null;
  request_metadata: Record<string, unknown>;
  response_data: {
    content?: string | null;
    content_chars?: number | null;
    content_truncated?: boolean;
    reasoning_content?: string | null;
    tool_calls?: Record<string, unknown>[];
    finish_reason?: string | null;
    model_reported?: string | null;
    stored?: boolean;
    simulation?: Simulation;
  };
  performance_metrics: Record<string, number | string | boolean | null>;
  started_at: string;
  completed_at: string;
  truncated: boolean;
  evaluations: EvaluationSummary[];
}

export interface CaseResultDetail extends Omit<CaseResult, "evaluations"> {
  evaluations: EvaluationResult[];
}

export interface MetricValue {
  run_model_id: string | null;
  load_step: number | null;
  metric_name: string;
  aggregation: string;
  metric_value: number | null;
  metric_unit: string;
  sample_count: number;
  metadata: Record<string, unknown> & { unavailable_reason?: string; definition?: string; concurrency?: number };
}

export interface RunMetrics {
  run_id: string;
  live: boolean;
  metrics_version: string;
  items: MetricValue[];
}

/** Compact payload of case.completed / case.failed events. */
export interface CaseEvent {
  id: string;
  sequence: number;
  run_model_id: string;
  case_identifier: string;
  phase: "warmup" | "measured";
  request_index: number;
  attempt_number: number;
  is_final: boolean;
  status: "succeeded" | "failed";
  error_type: string | null;
  http_status: number | null;
  latency_ms: number | null;
  ttft_ms: number | null;
  output_tokens: number | null;
  completed_at: string;
}
