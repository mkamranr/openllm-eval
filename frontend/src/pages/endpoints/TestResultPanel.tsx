import { Callout, Drawer, formatMs } from "../../components/ui";
import type { Endpoint, EndpointTestResult } from "../../types/api";

export function TestResultPanel({
  endpoint,
  result,
  error,
  running,
  onRetest,
  onClose,
}: {
  endpoint: Endpoint;
  result?: EndpointTestResult;
  error?: string;
  running: boolean;
  onRetest: () => void;
  onClose: () => void;
}) {
  const r = result?.response;
  const usage = r?.usage;
  return (
    <Drawer
      title={`Connection test: ${endpoint.name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
          <button type="button" className="btn btn-primary" onClick={onRetest} disabled={running}>
            {running ? "Testing…" : "Run again"}
          </button>
        </>
      }
    >
      <div className="form">
        {running && !result && (
          <p className="muted" role="status">
            Sending a test request to <code>{endpoint.base_url}</code>…
          </p>
        )}
        {error && (
          <Callout tone="error" role="alert">
            {error}
          </Callout>
        )}
        {result && r && (
          <>
            {result.ok ? (
              <Callout tone="ok" role="status">
                <strong>Endpoint responded successfully.</strong>{" "}
                {r.timing?.streamed ? "Streaming response." : "Non-streaming response."}
              </Callout>
            ) : (
              <Callout tone="error" role="alert">
                <strong>
                  Test failed:{" "}
                  {r.error?.type === "http_status" && r.error.status_code
                    ? `HTTP ${r.error.status_code}`
                    : r.error?.type.replace(/_/g, " ")}
                </strong>
                <div>{r.error?.message}</div>
                {r.error?.retryable && <div className="small">This error is usually transient.</div>}
              </Callout>
            )}

            <div className="metric-grid" aria-label="Test metrics">
              <Metric label="Latency" value={formatMs(r.timing?.latency_ms)} />
              <Metric
                label="Time to first token"
                value={r.timing?.ttft_ms != null ? formatMs(r.timing.ttft_ms) : "n/a"}
                title={r.timing?.streamed ? undefined : "Not observable without streaming"}
              />
              <Metric
                label="Tokens in / out"
                value={
                  usage && usage.source !== "unavailable"
                    ? `${usage.input_tokens ?? "?"} / ${usage.output_tokens ?? "?"}`
                    : "unavailable"
                }
              />
              <Metric label="HTTP status" value={r.http_status ?? "—"} />
            </div>

            {r.content && (
              <div className="field">
                <span className="label">Response</span>
                <div className="response-box">{r.content}</div>
              </div>
            )}

            <dl className="small" style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 16px", margin: 0 }}>
              <dt className="muted">Model reported by server</dt>
              <dd style={{ margin: 0 }}>
                <code>{r.model ?? "—"}</code>{" "}
                <span className="muted">(declared by the server, not verified)</span>
              </dd>
              <dt className="muted">/models check</dt>
              <dd style={{ margin: 0 }}>
                {result.models_check
                  ? result.models_check.ok
                    ? `OK. ${result.models_check.model_listed ? "Model is listed" : "Model NOT listed"} (${result.models_check.available_models.length} models)`
                    : `Failed: ${result.models_check.error?.message ?? "unknown"}`
                  : "skipped"}
              </dd>
              <dt className="muted">Token counts</dt>
              <dd style={{ margin: 0 }}>{usage?.source.replace(/_/g, " ")}</dd>
              <dt className="muted">Finish reason</dt>
              <dd style={{ margin: 0 }}>{r.finish_reason ?? "—"}</dd>
            </dl>

            {r.warnings.length > 0 && (
              <Callout tone="warn">
                {r.warnings.map((w) => (
                  <div key={w}>{w}</div>
                ))}
              </Callout>
            )}
          </>
        )}
      </div>
    </Drawer>
  );
}

function Metric({ label, value, title }: { label: string; value: React.ReactNode; title?: string }) {
  return (
    <div className="metric" title={title}>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
    </div>
  );
}
