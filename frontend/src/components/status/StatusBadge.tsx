import type { ComponentState } from "../../types/health";

const LABELS: Record<ComponentState | "unknown", string> = {
  ok: "OK",
  error: "Error",
  unavailable: "Unavailable",
  unknown: "Unknown",
};

export function StatusBadge({ state }: { state: ComponentState | "unknown" }) {
  return (
    <span className={`badge badge-${state}`}>
      <span className="dot" aria-hidden="true" />
      {LABELS[state]}
    </span>
  );
}

export function EndpointStatusBadge({ status }: { status: "ok" | "failed" | null }) {
  if (status === "ok")
    return (
      <span className="badge badge-ok">
        <span className="dot" aria-hidden="true" />
        Healthy
      </span>
    );
  if (status === "failed")
    return (
      <span className="badge badge-error">
        <span className="dot" aria-hidden="true" />
        Failed
      </span>
    );
  return (
    <span className="badge badge-neutral">
      <span className="dot" aria-hidden="true" />
      Untested
    </span>
  );
}
