export type ComponentState = "ok" | "error" | "unavailable";

export interface ComponentStatus {
  status: ComponentState;
  latency_ms?: number | null;
  detail?: string | null;
}

export interface WorkerStatus {
  status: ComponentState;
  active_workers: number;
  detail?: string | null;
}

export interface ReadinessResponse {
  status: "ok" | "degraded";
  database: ComponentStatus;
  redis: ComponentStatus;
  worker: WorkerStatus;
}

export interface VersionResponse {
  name: string;
  version: string;
  api_version: string;
  python_version: string;
}
