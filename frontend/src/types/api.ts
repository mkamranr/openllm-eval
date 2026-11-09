export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Project {
  id: string;
  name: string;
  description: string | null;
  tags: string[];
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  endpoint_count: number;
  dataset_count: number;
}

export interface RequestConfiguration {
  request_timeout_seconds: number;
  connect_timeout_seconds: number;
  max_retries: number;
  retry_backoff_seconds: number;
  temperature: number | null;
  top_p: number | null;
  max_tokens: number | null;
  stream: boolean;
  stream_include_usage: boolean;
  verify_tls: boolean;
  ca_cert_path: string | null;
  headers: Record<string, string>;
  secret_headers: Record<string, string>;
}

export interface Endpoint {
  id: string;
  project_id: string | null;
  name: string;
  adapter_type: string;
  base_url: string;
  model_identifier: string;
  secret_reference: string | null;
  secret_configured: boolean | null;
  request_configuration: RequestConfiguration;
  model_metadata: Record<string, unknown>;
  deployment_metadata: Record<string, unknown>;
  tags: string[];
  enabled: boolean;
  last_test_status: "ok" | "failed" | null;
  last_tested_at: string | null;
  last_test_summary: {
    ok: boolean;
    error_type: string | null;
    error_message: string | null;
    latency_ms: number | null;
    ttft_ms: number | null;
    streamed: boolean | null;
  } | null;
  warnings: string[];
  created_at: string;
  updated_at: string;
}

export type EndpointInput = Omit<
  Endpoint,
  | "id"
  | "secret_configured"
  | "last_test_status"
  | "last_tested_at"
  | "last_test_summary"
  | "warnings"
  | "created_at"
  | "updated_at"
>;

export interface AdapterInfo {
  adapter_type: string;
  display_name: string;
  version: string;
}

export interface AdapterError {
  type: string;
  message: string;
  status_code: number | null;
  retryable: boolean;
}

export interface ModelResponse {
  request_id: string;
  response_id: string | null;
  model: string | null;
  content: string | null;
  reasoning_content: string | null;
  tool_calls: { name: string | null; arguments: Record<string, unknown> | null }[];
  finish_reason: string | null;
  usage: {
    input_tokens: number | null;
    output_tokens: number | null;
    total_tokens: number | null;
    source: "server_reported" | "tokenizer_estimated" | "unavailable";
  };
  http_status: number | null;
  timing: {
    streamed: boolean;
    started_at: string;
    first_content_at: string | null;
    ended_at: string;
    ttft_ms: number | null;
    latency_ms: number;
    generation_ms: number | null;
  } | null;
  error: AdapterError | null;
  warnings: string[];
}

export interface ConnectionCheck {
  ok: boolean;
  latency_ms: number | null;
  http_status: number | null;
  available_models: string[];
  model_listed: boolean | null;
  error: AdapterError | null;
}

export interface EndpointTestResult {
  endpoint_id: string;
  tested_at: string;
  ok: boolean;
  models_check: ConnectionCheck | null;
  response: ModelResponse;
  network_policy: { allow_private_networks: boolean; allowlist: string[]; blocked_hosts: string[] };
}

export interface ValidationDetail {
  loc?: (string | number)[];
  msg: string;
}

// ------------------------------------------------------------------ datasets (Milestone 2)

export type CaseType =
  | "chat"
  | "instruction"
  | "qa"
  | "structured_output"
  | "tool_call"
  | "rag"
  | "long_context"
  | "safety";
export type DatasetType = CaseType | "mixed";

export interface DatasetVersion {
  id: string;
  dataset_id: string;
  version_number: number;
  version_label: string | null;
  content_hash: string;
  record_count: number;
  schema_version: string;
  source_format: string;
  source_filename: string | null;
  type_counts: Record<string, number>;
  tag_counts: Record<string, number>;
  split_counts: Record<string, number>;
  warning_count: number;
  created_at: string;
  is_current: boolean;
}

export interface Dataset {
  id: string;
  project_id: string | null;
  name: string;
  description: string | null;
  dataset_type: DatasetType;
  tags: string[];
  source: string;
  sample_id: string | null;
  license: string | null;
  current_version: DatasetVersion | null;
  version_count: number;
  created_at: string;
  updated_at: string;
}

export interface Issue {
  line: number | null;
  case_id: string | null;
  field: string | null;
  message: string;
}

export interface ValidationResult {
  valid: boolean;
  format: "jsonl" | "csv";
  dataset_type: DatasetType;
  schema_version: string;
  rows: number;
  record_count: number;
  error_count: number;
  warning_count: number;
  errors: Issue[];
  warnings: Issue[];
  type_counts: Record<string, number>;
  tag_counts: Record<string, number>;
  split_counts: Record<string, number>;
  content_hash: string | null;
  preview: Record<string, unknown>[];
  limits: { max_upload_bytes: number; max_records: number; max_record_bytes: number };
}

export interface DatasetCase {
  position: number;
  case_identifier: string;
  case_type: CaseType;
  tags: string[];
  content: Record<string, unknown>;
  truncated: boolean;
}

export interface SampleDataset {
  sample_id: string;
  name: string;
  description: string;
  dataset_type: DatasetType;
  record_count: number;
  license: string;
  source: string;
  installed_dataset_id: string | null;
}

export interface ContentUpload {
  content: string;
  filename?: string | null;
  format?: "jsonl" | "csv" | null;
}
