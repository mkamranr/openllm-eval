import { Layers3 } from "lucide-react";

import { Callout, Card } from "../../components/ui";
import type { ComparisonResult } from "../../types/reports";
import { fmt, fmtMs, fmtPct } from "../runs/runMeta";

function show(value: unknown): string {
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function contextFormat(metric: string, value: number | null): string {
  if (value == null) return "—";
  if (metric.endsWith("_ms")) return fmtMs(value);
  if (metric.includes("tokens")) return fmt(value, 0);
  return fmtPct(value);
}

/** Quantization / serving variants: declared settings (unknown stays unknown) and key metrics. */
export function VariantsCard({
  result,
  formatValue,
}: {
  result: ComparisonResult;
  formatValue: (value: number | null, unit: string) => string;
}) {
  const v = result.variants;
  if (!v || result.subjects.length < 2) return null;
  const subjects = result.subjects;
  return (
    <Card title="Deployment variants" icon={Layers3}>
      <p className="small muted" style={{ marginTop: 0 }}>
        Quantization and serving settings as declared on each endpoint. Undeclared values are shown as unknown; nothing is
        inferred from model names.
      </p>
      {v.warnings.map((w) => (
        <Callout key={w.message} tone={w.level === "warning" ? "warn" : "info"}>{w.message}</Callout>
      ))}
      <div className="table-wrap" style={{ marginTop: 10 }}>
        <table className="table" aria-label="Declared variant settings">
          <thead>
            <tr>
              <th scope="col">Setting</th>
              {subjects.map((s) => <th key={s.id} scope="col">{s.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {v.fields.map((f) => (
              <tr key={f.key}>
                <td>
                  {f.label}
                  {f.differs && <span className="badge badge-info" style={{ marginLeft: 6 }}>differs</span>}
                </td>
                {subjects.map((s) => {
                  const value = f.values[s.id];
                  return value == null
                    ? <td key={s.id} className="muted"><em>unknown</em></td>
                    : <td key={s.id}>{show(value)}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {v.key_metrics.length > 0 && (
        <>
          <h3 style={{ margin: "16px 0 8px" }}>Quality and performance</h3>
          <div className="table-wrap">
            <table className="table" aria-label="Variant key metrics">
              <thead>
                <tr>
                  <th scope="col">Metric</th>
                  {subjects.map((s) => <th key={s.id} scope="col" className="num">{s.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {v.key_metrics.map((m) => (
                  <tr key={`${m.metric_name}.${m.aggregation}`}>
                    <td>
                      {m.label} {m.label === m.metric_name && <span className="small muted">{m.aggregation}</span>}
                      {m.better && <div className="small muted">{m.better} is better</div>}
                    </td>
                    {m.values.map((x) => (
                      <td key={x.subject_id} className="num" title={x.value == null ? x.unavailable_reason ?? "unavailable" : undefined}>
                        {formatValue(x.value, m.unit)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {v.context.length > 0 && (
        <>
          <h3 style={{ margin: "16px 0 8px" }}>Context window by requested length</h3>
          <div className="table-wrap">
            <table className="table" aria-label="Context window comparison">
              <thead>
                <tr>
                  <th scope="col" className="num">Requested tokens</th>
                  <th scope="col">Metric</th>
                  {subjects.map((s) => <th key={s.id} scope="col" className="num">{s.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {v.context.map((c) => (
                  <tr key={`${c.context_length}.${c.metric_name}.${c.aggregation}`}>
                    <td className="num">{c.context_length.toLocaleString()}</td>
                    <td>{c.label}</td>
                    {subjects.map((s) => {
                      const x = c.values[s.id];
                      return (
                        <td key={s.id} className="num" title={x?.value == null ? x?.unavailable_reason ?? "unavailable" : undefined}>
                          {contextFormat(c.metric_name, x?.value ?? null)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}
