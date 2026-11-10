import type {
  AdapterInfo,
  ContentUpload,
  Dataset,
  DatasetCase,
  DatasetType,
  DatasetVersion,
  Endpoint,
  EndpointInput,
  EndpointTestResult,
  Page,
  Project,
  SampleDataset,
  ValidationDetail,
  ValidationResult,
} from "../types/api";
import type {
  ApiToken,
  ApiTokenCreated,
  AuditEvent,
  AuthSettings,
  DirectoryTestResult,
  KerberosSettingsInput,
  LdapSettingsInput,
  Me,
  Providers,
  Role,
  User,
  UserCreated,
} from "../types/auth";
import type { ToolsStatus } from "../types/tools";
import type { ReadinessResponse, VersionResponse } from "../types/health";
import type { ComparisonResult, Report, ReportCreate, SavedComparison } from "../types/reports";
import type {
  CaseResult,
  CaseResultDetail,
  EvaluatorInfo,
  Profile,
  ProfileInput,
  Run,
  RunCreate,
  RunDetail,
  RunMetrics,
  RunTelemetry,
  RunStatus,
  RunSummary,
} from "../types/runs";

/** Same-origin API base. The dev server and nginx both proxy /api to the backend. */
export const API_BASE = "/api/v1";

export interface ApiErrorBody {
  error: { code: string; message: string; request_id: string | null; details: unknown };
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get code(): string | undefined {
    return (this.body as ApiErrorBody | undefined)?.error?.code;
  }

  /** Field-level validation messages keyed by dotted path without the "body" prefix. */
  get fieldErrors(): Record<string, string> {
    const details = (this.body as ApiErrorBody | undefined)?.error?.details;
    const out: Record<string, string> = {};
    if (Array.isArray(details)) {
      for (const d of details as ValidationDetail[]) {
        const path = (d.loc ?? []).filter((p) => p !== "body").join(".");
        if (path && !out[path]) out[path] = d.msg.replace(/^Value error, /, "");
      }
    }
    return out;
  }
}

/** Sent with every change; cross-site pages cannot set it, so cookie sessions are CSRF-safe. */
export const CSRF_HEADER = "X-OpenLLM-CSRF";

/** Fired when the server says the session is gone (401) or a password change is due (403). */
export const AUTH_EVENT = "openllm:auth";
export type AuthEventDetail = "signed-out" | "password-change-required";

function announce(detail: AuthEventDetail) {
  window.dispatchEvent(new CustomEvent<AuthEventDetail>(AUTH_EVENT, { detail }));
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  okStatuses: number[] = [],
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  const method = (init.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") headers[CSRF_HEADER] = "1";
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "same-origin",
    headers: { ...headers, ...(init.headers as Record<string, string> | undefined) },
  });
  if (response.status === 204) return undefined as T;
  let body: unknown = undefined;
  try {
    body = await response.json();
  } catch {
    // Non-JSON body (e.g. proxy error page); handled below.
  }
  if (!response.ok && !okStatuses.includes(response.status)) {
    const message =
      (body as ApiErrorBody | undefined)?.error?.message ?? `HTTP ${response.status}`;
    const error = new ApiError(message, response.status, body);
    if (!path.startsWith("/auth/")) {
      if (response.status === 401 && error.code === "not_authenticated") announce("signed-out");
      if (response.status === 403 && error.code === "password_change_required") announce("password-change-required");
    }
    throw error;
  }
  if (body === undefined) {
    throw new ApiError("Invalid JSON response", response.status);
  }
  return body as T;
}

const json = (method: string, data?: unknown): RequestInit => ({
  method,
  body: data === undefined ? undefined : JSON.stringify(data),
});

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") q.set(key, String(value));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const api = {
  auth: {
    providers: () => request<Providers>("/auth/providers"),
    me: () => request<Me>("/auth/me"),
    login: (username: string, password: string) =>
      request<Me>("/auth/login", json("POST", { username, password })),
    /** Windows single sign-on: the browser answers the Negotiate challenge by itself. */
    negotiate: () => request<Me>("/auth/negotiate"),
    logout: () => request<void>("/auth/logout", { method: "POST" }),
    changePassword: (current_password: string, new_password: string) =>
      request<void>("/auth/change-password", json("POST", { current_password, new_password })),
    tokens: () => request<ApiToken[]>("/auth/tokens"),
    createToken: (name: string, expires_in_days: number | null) =>
      request<ApiTokenCreated>("/auth/tokens", json("POST", { name, expires_in_days })),
    revokeToken: (id: string) => request<void>(`/auth/tokens/${id}`, { method: "DELETE" }),
  },

  users: {
    list: (q?: string) => request<Page<User>>(`/users${query({ q, limit: 500 })}`),
    create: (data: { username: string; display_name?: string | null; email?: string | null; role: Role; password?: string | null; must_change_password?: boolean }) =>
      request<UserCreated>("/users", json("POST", data)),
    update: (id: string, data: Partial<{ display_name: string | null; email: string | null; role: Role; role_pinned: boolean; disabled: boolean }>) =>
      request<User>(`/users/${id}`, json("PATCH", data)),
    remove: (id: string) => request<void>(`/users/${id}`, { method: "DELETE" }),
    resetPassword: (id: string) =>
      request<{ temporary_password: string | null; must_change_password: boolean }>(`/users/${id}/reset-password`, json("POST", {})),
    signOutEverywhere: (id: string) => request<void>(`/users/${id}/sessions`, { method: "DELETE" }),
  },

  authSettings: {
    get: () => request<AuthSettings>("/settings/auth"),
    save: (ldap: LdapSettingsInput, kerberos: KerberosSettingsInput) =>
      request<AuthSettings>("/settings/auth", json("PUT", { ldap, kerberos })),
    testConnection: (ldap: LdapSettingsInput) =>
      request<DirectoryTestResult>("/settings/auth/test-connection", json("POST", { ldap })),
    testUser: (ldap: LdapSettingsInput, username: string, password: string | null) =>
      request<DirectoryTestResult>("/settings/auth/test-user", json("POST", { ldap, username, password })),
  },

  audit: (params: { actor?: string; action?: string; limit?: number; offset?: number } = {}) =>
    request<Page<AuditEvent>>(`/audit${query({ limit: 100, ...params })}`),

  /** Readiness returns 503 with a full body when degraded; treat that as data, not an error. */
  readiness: () => request<ReadinessResponse>("/health/ready", {}, [503]),
  version: () => request<VersionResponse>("/version"),

  adapters: () => request<AdapterInfo[]>("/adapters"),

  projects: {
    list: (params: { include_archived?: boolean; limit?: number } = {}) =>
      request<Page<Project>>(`/projects${query({ limit: 500, ...params })}`),
    create: (data: { name: string; description?: string | null; tags?: string[] }) =>
      request<Project>("/projects", json("POST", data)),
    update: (
      id: string,
      data: Partial<{ name: string; description: string | null; tags: string[]; archived: boolean }>,
    ) => request<Project>(`/projects/${id}`, json("PATCH", data)),
    remove: (id: string) => request<void>(`/projects/${id}`, { method: "DELETE" }),
  },

  endpoints: {
    list: (params: { project_id?: string; enabled?: boolean; limit?: number } = {}) =>
      request<Page<Endpoint>>(`/endpoints${query({ limit: 500, ...params })}`),
    get: (id: string) => request<Endpoint>(`/endpoints/${id}`),
    create: (data: EndpointInput) => request<Endpoint>("/endpoints", json("POST", data)),
    update: (id: string, data: Partial<EndpointInput>) =>
      request<Endpoint>(`/endpoints/${id}`, json("PATCH", data)),
    clone: (id: string, name?: string) =>
      request<Endpoint>(`/endpoints/${id}/clone`, json("POST", name ? { name } : {})),
    remove: (id: string) => request<void>(`/endpoints/${id}`, { method: "DELETE" }),
    test: (id: string, options: { prompt?: string; stream?: boolean | null } = {}) =>
      request<EndpointTestResult>(`/endpoints/${id}/test`, json("POST", options)),
  },

  datasets: {
    list: (params: { project_id?: string; dataset_type?: string; tag?: string; q?: string } = {}) =>
      request<Page<Dataset>>(`/datasets${query({ limit: 500, ...params })}`),
    get: (id: string) => request<Dataset>(`/datasets/${id}`),
    validate: (data: ContentUpload & { dataset_type: DatasetType }) =>
      request<ValidationResult>("/datasets/validate", json("POST", data)),
    create: (
      data: ContentUpload & {
        name: string;
        dataset_type: DatasetType;
        description?: string | null;
        project_id?: string | null;
        tags?: string[];
        version_label?: string | null;
      },
    ) => request<Dataset>("/datasets", json("POST", data)),
    update: (
      id: string,
      data: Partial<{
        name: string;
        description: string | null;
        project_id: string | null;
        tags: string[];
        current_version_id: string;
      }>,
    ) => request<Dataset>(`/datasets/${id}`, json("PATCH", data)),
    remove: (id: string) => request<void>(`/datasets/${id}`, { method: "DELETE" }),
    generate: (data: {
      name: string;
      description?: string | null;
      project_id?: string | null;
      tags?: string[];
      params: {
        context_lengths: number[];
        depths?: number[];
        needles_per_case?: number;
        repeats?: number;
        seed?: number;
      };
    }) => request<Dataset>("/datasets/generate", json("POST", { generator: "needle", ...data })),
    duplicate: (id: string, name?: string) =>
      request<Dataset>(`/datasets/${id}/duplicate`, json("POST", name ? { name } : {})),
    versions: (id: string) => request<DatasetVersion[]>(`/datasets/${id}/versions`),
    addVersion: (id: string, data: ContentUpload & { version_label?: string | null }) =>
      request<DatasetVersion>(`/datasets/${id}/versions`, json("POST", data)),
    preview: (
      id: string,
      params: {
        version_id?: string;
        tag?: string;
        case_type?: string;
        q?: string;
        limit?: number;
        offset?: number;
      } = {},
    ) => request<Page<DatasetCase>>(`/datasets/${id}/preview${query(params)}`),
    getCase: (id: string, caseId: string, versionId?: string) =>
      request<DatasetCase>(
        `/datasets/${id}/cases/${caseId.split("/").map(encodeURIComponent).join("/")}${query({ version_id: versionId })}`,
      ),
    exportUrl: (id: string, versionId?: string) =>
      `${API_BASE}/datasets/${id}/export${query({ version_id: versionId })}`,
    samples: () => request<SampleDataset[]>("/datasets/samples"),
    installSample: (sampleId: string) =>
      request<Dataset>(`/datasets/samples/${encodeURIComponent(sampleId)}/install`, { method: "POST" }),
  },

  runs: {
    list: (
      params: { project_id?: string; status?: RunStatus; q?: string; limit?: number; offset?: number } = {},
    ) => request<Page<Run>>(`/runs${query({ limit: 50, ...params })}`),
    summary: () => request<RunSummary>("/runs/summary"),
    get: (id: string) => request<RunDetail>(`/runs/${id}`),
    create: (data: RunCreate) => request<RunDetail>("/runs", json("POST", data)),
    cancel: (id: string) => request<RunDetail>(`/runs/${id}/cancel`, { method: "POST" }),
    retryFailed: (id: string) => request<RunDetail>(`/runs/${id}/retry-failed`, { method: "POST" }),
    remove: (id: string) => request<void>(`/runs/${id}`, { method: "DELETE" }),
    results: (
      id: string,
      params: {
        run_model_id?: string;
        status?: "succeeded" | "failed";
        phase?: "warmup" | "measured";
        final_only?: boolean;
        q?: string;
        evaluator?: string;
        evaluation?: "passed" | "failed" | "not_applicable" | "skipped" | "requires_review";
        limit?: number;
        offset?: number;
      } = {},
    ) => request<Page<CaseResult>>(`/runs/${id}/results${query(params)}`),
    result: (id: string, resultId: string) => request<CaseResultDetail>(`/runs/${id}/results/${resultId}`),
    metrics: (id: string) => request<RunMetrics>(`/runs/${id}/metrics`),
    telemetry: (id: string) => request<RunTelemetry>(`/runs/${id}/telemetry`),
    eventsUrl: (id: string) => `${API_BASE}/runs/${id}/events`,
    rerun: (id: string) => request<RunDetail>(`/runs/${id}/rerun`, { method: "POST" }),
  },

  reports: {
    list: (params: { run_id?: string; limit?: number; offset?: number } = {}) =>
      request<Page<Report>>(`/reports${query({ limit: 100, ...params })}`),
    forRun: (runId: string) => request<Page<Report>>(`/runs/${runId}/reports?limit=100`),
    create: (runId: string, data: ReportCreate) => request<Report>(`/runs/${runId}/reports`, json("POST", data)),
    remove: (id: string) => request<void>(`/reports/${id}`, { method: "DELETE" }),
    downloadUrl: (id: string, inline = false) => `${API_BASE}/reports/${id}/download${inline ? "?inline=true" : ""}`,
  },

  compare: (runIds: string[], baseline?: string) =>
    request<ComparisonResult>(`/compare${query({ run_ids: runIds.join(","), baseline })}`),
  compareExportUrl: (runIds: string[], format: "html" | "csv" | "json", baseline?: string) =>
    `${API_BASE}/compare/export${query({ run_ids: runIds.join(","), baseline, format })}`,

  comparisons: {
    list: () => request<Page<SavedComparison>>("/comparisons?limit=100"),
    get: (id: string) => request<SavedComparison>(`/comparisons/${id}`),
    create: (data: { name: string; description?: string | null; run_ids: string[]; baseline?: string | null }) =>
      request<SavedComparison>("/comparisons", json("POST", data)),
    remove: (id: string) => request<void>(`/comparisons/${id}`, { method: "DELETE" }),
  },

  evaluators: () => request<EvaluatorInfo[]>("/evaluators"),

  profiles: {
    list: () => request<Page<Profile>>("/profiles"),
    get: (id: string) => request<Profile>(`/profiles/${id}`),
    create: (data: ProfileInput) => request<Profile>("/profiles", json("POST", data)),
    update: (id: string, data: Partial<ProfileInput>) => request<Profile>(`/profiles/${id}`, json("PATCH", data)),
    remove: (id: string) => request<void>(`/profiles/${id}`, { method: "DELETE" }),
  },

  tools: {
    status: () => request<ToolsStatus>("/tools/status"),
  },
};
