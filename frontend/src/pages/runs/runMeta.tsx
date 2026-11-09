import type { LoadProfile, MetricValue, Run, RunStatus } from "../../types/runs";
import { ACTIVE_STATUSES, PENDING_STATUSES, TERMINAL_STATUSES } from "../../types/runs";

const STATUS: Record<RunStatus, { label: string; tone: string }> = {
  created: { label: "Created", tone: "neutral" },
  queued: { label: "Queued", tone: "neutral" },
  initializing: { label: "Starting", tone: "info" },
  running: { label: "Running", tone: "info" },
  finalizing: { label: "Finalizing", tone: "info" },
  cancel_requested: { label: "Cancelling", tone: "warn" },
  completed: { label: "Completed", tone: "ok" },
  completed_with_errors: { label: "Completed with errors", tone: "warn" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  failed: { label: "Failed", tone: "error" },
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const s = STATUS[status] ?? { label: status, tone: "neutral" };
  return (
    <span className={`badge badge-${s.tone}`}>
      <span className={`dot ${isActive(status) ? "pulse" : ""}`} aria-hidden="true" />
      {s.label}
    </span>
  );
}

export const isActive = (s: RunStatus) => ACTIVE_STATUSES.includes(s);
export const isPending = (s: RunStatus) => PENDING_STATUSES.includes(s);
export const isTerminal = (s: RunStatus) => TERMINAL_STATUSES.includes(s);

export const PROFILE_META: Record<LoadProfile, { label: string; description: string }> = {
  smoke: { label: "Smoke", description: "Up to 5 requests at low concurrency, to check that everything works." },
  sequential: { label: "Sequential", description: "One request at a time: clean per-request latency." },
  fixed_concurrency: { label: "Fixed concurrency", description: "Keep N requests in flight until all are done." },
  ramp: { label: "Ramp", description: "Step concurrency up (e.g. 1, 2, 4, 8) and measure each step." },
  sustained: { label: "Sustained", description: "Hold N requests in flight for a set duration." },
};

export function profileSummary(run: Run): string {
  const l = run.load;
  const label = PROFILE_META[l.profile]?.label ?? l.profile;
  if (l.profile === "ramp") return `${label} · ${l.steps.map((s) => s.concurrency).join(" → ")}`;
  if (l.profile === "sustained") return `${label} · ${l.concurrency} in flight · ${l.duration_seconds}s`;
  return `${label} · concurrency ${l.concurrency}`;
}

/** Number formatting that never turns a missing value into 0. */
export function fmt(value: number | null | undefined, digits = 1): string {
  if (value == null || Number.isNaN(value)) return "—";
  if (Math.abs(value) >= 1000) return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return value.toFixed(digits);
}

export function fmtMs(value: number | null | undefined): string {
  if (value == null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${value.toFixed(value < 10 ? 1 : 0)} ms`;
}

export function fmtPct(ratio: number | null | undefined): string {
  if (ratio == null) return "—";
  return `${(ratio * 100).toFixed(ratio > 0 && ratio < 0.001 ? 2 : 1)}%`;
}

export function fmtDuration(start: string | null, end: string | null): string {
  if (!start) return "—";
  const ms = (end ? new Date(end).getTime() : Date.now()) - new Date(start).getTime();
  if (ms < 0) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** Quick lookup over aggregate metrics: get(modelId, name, aggregation, step?). */
export function metricIndex(items: MetricValue[]) {
  const map = new Map<string, MetricValue>();
  for (const m of items) map.set(`${m.run_model_id}|${m.load_step ?? ""}|${m.metric_name}|${m.aggregation}`, m);
  return (modelId: string, name: string, aggregation: string, step: number | null = null) =>
    map.get(`${modelId}|${step ?? ""}|${name}|${aggregation}`);
}

/** Tooltip explaining an unavailable or defined metric. */
export function metricTitle(m: MetricValue | undefined): string | undefined {
  if (!m) return undefined;
  const parts = [];
  if (m.metric_value == null && m.metadata.unavailable_reason) parts.push(`Not available: ${m.metadata.unavailable_reason}`);
  if (m.metadata.definition) parts.push(String(m.metadata.definition));
  parts.push(`n = ${m.sample_count}`);
  return parts.join("\n");
}

export function progressOf(run: Run): { done: number; planned: number | null; pct: number | null } {
  const p = run.progress ?? {};
  const done = p.completed ?? 0;
  const planned = run.load.requests != null ? run.load.requests * run.models.length : null;
  const pct = isTerminal(run.status) && run.status === "completed" ? 100 : planned ? Math.min(100, (done / planned) * 100) : null;
  return { done, planned, pct };
}
