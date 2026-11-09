import { vi } from "vitest";

import type { Dataset, DatasetVersion, Endpoint, Project } from "../types/api";
import type { CaseResult, MetricValue, Profile, RunDetail, RunModel } from "../types/runs";

export function jsonResponse(body: unknown, status = 200): Response {
  if (status === 204) return new Response(null, { status });
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export type Handler = (
  method: string,
  path: string,
  body: unknown,
) => Response | Promise<Response> | undefined;

/** Stub global fetch. Handlers are tried in order; unmatched requests return 404. */
export function stubFetch(...handlers: Handler[]) {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const path = url.pathname.replace(/^\/api\/v1/, "") + url.search;
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, path, body });
    for (const h of handlers) {
      const r = await h(method, path, body);
      if (r) return r;
    }
    return jsonResponse({ error: { code: "not_found", message: `no stub for ${method} ${path}` } }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

export const page = <T>(items: T[]) => ({ items, total: items.length, limit: 500, offset: 0 });

export function makeEndpoint(overrides: Partial<Endpoint> = {}): Endpoint {
  return {
    id: "e1",
    name: "local-vllm",
    project_id: null,
    adapter_type: "openai_compatible",
    base_url: "http://vllm:8000/v1",
    model_identifier: "Qwen/Qwen2.5-7B-Instruct",
    secret_reference: null,
    secret_configured: null,
    request_configuration: {
      request_timeout_seconds: 120,
      connect_timeout_seconds: 10,
      max_retries: 1,
      retry_backoff_seconds: 1,
      temperature: null,
      top_p: null,
      max_tokens: null,
      stream: true,
      stream_include_usage: true,
      verify_tls: true,
      ca_cert_path: null,
      headers: {},
      secret_headers: {},
    },
    model_metadata: {},
    deployment_metadata: { gpu: "RTX 4090" },
    tags: ["local"],
    enabled: true,
    last_test_status: null,
    last_tested_at: null,
    last_test_summary: null,
    warnings: [],
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    ...overrides,
  } as Endpoint;
}

export function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: "p1",
    name: "Support bot",
    description: "Customer support assistant",
    tags: ["support"],
    archived_at: null,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    endpoint_count: 0,
    dataset_count: 0,
    ...overrides,
  };
}

export function makeVersion(overrides: Partial<DatasetVersion> = {}): DatasetVersion {
  return {
    id: "v1",
    dataset_id: "d1",
    version_number: 1,
    version_label: null,
    content_hash: "a".repeat(64),
    record_count: 12,
    schema_version: "1",
    source_format: "jsonl",
    source_filename: "cases.jsonl",
    type_counts: { qa: 12 },
    tag_counts: { basic: 7, web: 3 },
    split_counts: {},
    warning_count: 0,
    created_at: "2026-11-01T00:00:00Z",
    is_current: true,
    ...overrides,
  };
}

export function makeDataset(overrides: Partial<Dataset> = {}): Dataset {
  return {
    id: "d1",
    project_id: null,
    name: "Support QA",
    description: "Questions from the support team",
    dataset_type: "qa",
    tags: ["support"],
    source: "user",
    sample_id: null,
    license: null,
    current_version: makeVersion(),
    version_count: 1,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    ...overrides,
  };
}

export const ADAPTERS = [
  { adapter_type: "openai_compatible", display_name: "OpenAI-compatible", version: "1.0.0" },
];

export function makeProfile(overrides: Partial<Profile> = {}): Profile {
  return {
    id: "pf1",
    project_id: null,
    name: "Nightly quality",
    description: null,
    suites: ["performance", "answer_quality"],
    evaluation: { tool_mode: "simulated", token_f1_threshold: 0.7, allow_code_fence: true, embedding: null },
    generation: { temperature: 0, max_tokens: 128 },
    load: { profile: "fixed_concurrency", concurrency: 4, requests: 40, warmup_requests: 1, stream: true },
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    ...overrides,
  };
}

export function makeRunModel(overrides: Partial<RunModel> = {}): RunModel {
  return {
    id: "rm1",
    position: 0,
    endpoint_id: "e1",
    name: "local-vllm",
    model_identifier: "Qwen/Qwen2.5-7B-Instruct",
    adapter_type: "openai_compatible",
    base_url: "http://vllm:8000/v1",
    status: "completed",
    progress: { phase: "done", planned: 10, completed: 10, succeeded: 9, failed: 1, retries: 2, in_flight: 0 },
    started_at: "2026-11-01T00:00:00Z",
    completed_at: "2026-11-01T00:00:30Z",
    declared_model_metadata: {},
    hardware_metadata: {},
    ...overrides,
  };
}

export function makeRun(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    id: "r1",
    project_id: null,
    name: "Prod benchmark",
    description: null,
    status: "completed_with_errors",
    suites: ["performance"],
    framework_version: "0.0.1",
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:30Z",
    started_at: "2026-11-01T00:00:00Z",
    completed_at: "2026-11-01T00:00:30Z",
    cancel_requested_at: null,
    error_summary: { message: "1 measured request failed.", failed_requests: 1, errors: { http_error: 1 } },
    progress: { completed: 10, succeeded: 9, failed: 1, in_flight: 0, planned: 10 },
    pass_count: 1,
    retry_failed_requested: false,
    load: {
      profile: "fixed_concurrency",
      concurrency: 4,
      requests: 10,
      warmup_requests: 2,
      stream: true,
      request_timeout_seconds: null,
      max_retries: null,
      duration_seconds: null,
      steps: [{ step: null, concurrency: 4, start: 0, count: 10, duration_seconds: null }],
      case_count: 12,
      case_order: "Request i uses case i mod case count",
      warmup_policy: "excluded",
      endpoint_order: "Endpoints run one after another",
    },
    models: [makeRunModel()],
    datasets: [
      {
        dataset_id: "d1",
        dataset_version_id: "v1",
        name: "Support QA",
        dataset_type: "qa",
        version_number: 1,
        content_hash: "a".repeat(64),
        record_count: 12,
        available: true,
      },
    ],
    configuration_snapshot: {
      sampling: "greedy",
      generation: { temperature: 0, top_p: null, max_tokens: 256, seed: null },
      metrics_version: "1",
      prompt_builder_version: "1",
      endpoints: [{ name: "local-vllm", request_configuration: { request_timeout_seconds: 120, max_retries: 1 } }],
    },
    ...overrides,
  };
}

export function makeMetric(name: string, aggregation: string, value: number | null, overrides: Partial<MetricValue> = {}): MetricValue {
  return {
    run_model_id: "rm1",
    load_step: null,
    metric_name: name,
    aggregation,
    metric_value: value,
    metric_unit: "",
    sample_count: 9,
    metadata: {},
    ...overrides,
  };
}

export function makeResult(overrides: Partial<CaseResult> = {}): CaseResult {
  return {
    id: "res1",
    run_model_id: "rm1",
    dataset_case_id: null,
    case_identifier: "qa-001",
    sequence: 1,
    phase: "measured",
    request_index: 0,
    attempt_number: 1,
    pass_number: 1,
    load_step: null,
    is_final: true,
    is_warmup: false,
    status: "succeeded",
    error_type: null,
    error_message: null,
    http_status: null,
    latency_ms: 420,
    ttft_ms: 85,
    request_metadata: { concurrency: 4 },
    response_data: { content: "Hello there", finish_reason: "stop", model_reported: "qwen", stored: true },
    performance_metrics: { streamed: true, output_tokens: 12, input_tokens: 30, token_count_source: "server" },
    started_at: "2026-11-01T00:00:01Z",
    completed_at: "2026-11-01T00:00:02Z",
    truncated: false,
    evaluations: [],
    ...overrides,
  };
}
