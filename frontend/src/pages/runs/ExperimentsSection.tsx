import { Activity, ScrollText } from "lucide-react";

import { Card } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { api } from "../../services/api";
import type { MetricValue, RunDetail, RunTelemetry } from "../../types/runs";
import { fmt, fmtMs, fmtPct, metricTitle } from "./runMeta";

type Get = (modelId: string, name: string, aggregation: string) => MetricValue | undefined;

/** Context-window metrics, keyed by model, then requested context length. */
export function contextIndex(items: MetricValue[]) {
  const map = new Map<string, Map<number, Map<string, MetricValue>>>();
  for (const m of items) {
    if (m.context_length == null || !m.run_model_id) continue;
    const byLength = map.get(m.run_model_id) ?? new Map<number, Map<string, MetricValue>>();
    const cell = byLength.get(m.context_length) ?? new Map<string, MetricValue>();
    cell.set(`${m.metric_name}|${m.aggregation}`, m);
    byLength.set(m.context_length, cell);
    map.set(m.run_model_id, byLength);
  }
  return map;
}

function Cell({ m, format }: { m: MetricValue | undefined; format: (v: number | null) => string }) {
  return (
    <td className="num" title={metricTitle(m)}>
      {m ? format(m.metric_value) : "—"}
    </td>
  );
}

const COLUMNS: [string, string, string, (v: number | null) => string][] = [
  ["Requests", "context.requests", "count", (v) => fmt(v, 0)],
  ["Success", "context.success_rate", "ratio", fmtPct],
  ["Overflow", "context.overflow_rate", "ratio", fmtPct],
  ["Retrieval", "context.retrieval_accuracy", "pass_rate", fmtPct],
  ["Early", "context.retrieval_accuracy", "early", fmtPct],
  ["Middle", "context.retrieval_accuracy", "middle", fmtPct],
  ["Late", "context.retrieval_accuracy", "late", fmtPct],
  ["TTFT p50", "context.ttft_ms", "p50", fmtMs],
  ["Est. tokens", "context.estimated_input_tokens", "mean", (v) => fmt(v, 0)],
  ["Server tokens", "context.input_tokens", "mean", (v) => fmt(v, 0)],
  ["Truncation?", "context.truncation_suspected", "ratio", fmtPct],
];

export function ContextCard({ run, items }: { run: RunDetail; items: MetricValue[] }) {
  const index = contextIndex(items);
  if (index.size === 0) return null;
  return (
    <Card title="Context window" icon={ScrollText}>
      <p className="small muted" style={{ marginTop: 0 }}>
        Grouped by each case's <strong>requested</strong> context length. Estimated tokens use a fixed heuristic (4
        characters per token); server tokens are what the model server reported, since tokenizers differ. Succeeding at a
        length doesn't prove the model uses that context, so check retrieval too. "—" means not measurable; hover for the
        reason.
      </p>
      {run.models.map((model) => {
        const byLength = index.get(model.id);
        if (!byLength) return null;
        const lengths = [...byLength.keys()].sort((a, b) => a - b);
        return (
          <div key={model.id} style={{ marginTop: 12 }}>
            {run.models.length > 1 && <h3 style={{ margin: "0 0 8px" }}>{model.name}</h3>}
            <div className="table-wrap">
              <table className="table" aria-label={`Context window results for ${model.name}`}>
                <thead>
                  <tr>
                    <th scope="col">Requested tokens</th>
                    {COLUMNS.map(([h]) => <th key={h} scope="col" className="num">{h}</th>)}
                    <th scope="col">Token count source</th>
                  </tr>
                </thead>
                <tbody>
                  {lengths.map((n) => {
                    const cell = byLength.get(n)!;
                    const sources = (cell.get("context.input_tokens|mean")?.metadata.token_count_source ?? {}) as Record<
                      string,
                      number
                    >;
                    return (
                      <tr key={n}>
                        <td className="num">{n.toLocaleString()}</td>
                        {COLUMNS.map(([h, name, agg, format]) => (
                          <Cell key={h} m={cell.get(`${name}|${agg}`)} format={format} />
                        ))}
                        <td className="small">
                          {Object.entries(sources).map(([k, v]) => `${k.replace("_", " ")}: ${v}`).join(", ") || "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </Card>
  );
}

const RESOURCE_ROWS: [string, string, string, (v: number | null) => string][] = [
  ["GPU memory peak", "gpu.memory_used_mb", "max", (v) => (v == null ? "—" : `${fmt(v, 0)} MB`)],
  ["GPU memory total", "gpu.memory_total_mb", "max", (v) => (v == null ? "—" : `${fmt(v, 0)} MB`)],
  ["GPU utilization (mean)", "gpu.utilization_pct", "mean", (v) => (v == null ? "—" : `${fmt(v)}%`)],
  ["GPU power (mean)", "gpu.power_w", "mean", (v) => (v == null ? "—" : `${fmt(v)} W`)],
  ["GPU temperature (max)", "gpu.temperature_c", "max", (v) => (v == null ? "—" : `${fmt(v, 0)} °C`)],
  ["KV cache peak", "gpu.kv_cache_usage_pct", "max", (v) => (v == null ? "—" : `${fmt(v)}%`)],
];

function Sparkline({ points, label }: { points: [number, number][]; label: string }) {
  if (points.length < 2) return null;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs) || 1];
  const [y0, y1] = [0, Math.max(...ys) || 1];
  const w = 220;
  const h = 40;
  const path = points
    .map(([x, y], i) => `${i ? "L" : "M"}${(((x - x0) / (x1 - x0 || 1)) * w).toFixed(1)},${(h - ((y - y0) / (y1 - y0)) * h).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={label} className="sparkline">
      <path d={path} fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" />
    </svg>
  );
}

export function TelemetryCard({ run, get }: { run: RunDetail; get: Get }) {
  const configured = run.models.filter((m) => get(m.id, "telemetry.samples", "count"));
  const telemetry = useAsync<RunTelemetry | null>(
    () => (configured.length ? api.runs.telemetry(run.id) : Promise.resolve(null)),
    [run.id, configured.length, run.status],
  );
  if (configured.length === 0) return null;
  return (
    <Card title="Resource telemetry" icon={Activity}>
      <p className="small muted" style={{ marginTop: 0 }}>
        Scraped from the exporter configured on the endpoint while the model was measured. Telemetry is best effort: a
        failing exporter leaves these values unavailable and never fails the run.
      </p>
      <div className="table-wrap">
        <table className="table" aria-label="Resource telemetry">
          <thead>
            <tr>
              <th scope="col">Metric</th>
              {configured.map((m) => <th key={m.id} scope="col" className="num">{m.name}</th>)}
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Samples</td>
              {configured.map((m) => {
                const s = get(m.id, "telemetry.samples", "count");
                const failures = s?.metadata.failures as number | undefined;
                const lastError = s?.metadata.last_error as string | undefined;
                return (
                  <td key={m.id} className="num" title={lastError ? `Last error: ${lastError}` : undefined}>
                    {fmt(s?.metric_value, 0)}
                    {failures ? <span className="small muted"> ({failures} failed)</span> : null}
                  </td>
                );
              })}
            </tr>
            {RESOURCE_ROWS.map(([label, name, agg, format]) => (
              <tr key={label}>
                <td>{label}</td>
                {configured.map((m) => <Cell key={m.id} m={get(m.id, name, agg)} format={format} />)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {telemetry.data && telemetry.data.items.length > 1 && (
        <div style={{ display: "flex", gap: 24, flexWrap: "wrap", marginTop: 12 }}>
          {configured.map((m) => {
            const points = telemetry.data!.items
              .filter((s) => s.run_model_id === m.id && typeof s.values.gpu_memory_used_mb === "number")
              .map((s) => [s.offset_seconds, s.values.gpu_memory_used_mb as number] as [number, number]);
            if (points.length < 2) return null;
            return (
              <figure key={m.id} style={{ margin: 0 }}>
                <figcaption className="small muted">{m.name}: GPU memory over the measured phase</figcaption>
                <Sparkline points={points} label={`GPU memory used over time for ${m.name}`} />
              </figure>
            );
          })}
        </div>
      )}
    </Card>
  );
}
