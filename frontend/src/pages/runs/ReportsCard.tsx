import { Download, ExternalLink, FileText, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Callout, Card, Switch, formatBytes } from "../../components/ui";
import { api } from "../../services/api";
import type { Report, ReportFormat } from "../../types/reports";
import type { RunDetail } from "../../types/runs";
import { isTerminal } from "./runMeta";

export const REPORT_FORMATS: { value: ReportFormat; label: string; hint: string }[] = [
  { value: "html", label: "HTML report", hint: "Charts, methodology, configuration; works offline" },
  { value: "json", label: "JSON export", hint: "Everything in the report, machine-readable" },
  { value: "csv", label: "Results CSV", hint: "One row per recorded attempt" },
  { value: "metrics_csv", label: "Metrics CSV", hint: "One row per metric and model" },
  { value: "markdown", label: "Markdown summary", hint: "For pull requests and wikis" },
];

export const formatLabel = (f: string) => REPORT_FORMATS.find((x) => x.value === f)?.label ?? f;

export function ReportsCard({ run }: { run: RunDetail }) {
  const [reports, setReports] = useState<Report[]>();
  const [format, setFormat] = useState<ReportFormat>("html");
  const [redactContent, setRedactContent] = useState(false);
  const [redactEndpoints, setRedactEndpoints] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const finished = isTerminal(run.status);

  const load = useCallback(() => {
    api.reports
      .forRun(run.id)
      .then((p) => setReports(p.items))
      .catch((e: Error) => setError(e.message));
  }, [run.id]);
  useEffect(load, [load]);

  async function generate() {
    setBusy(true);
    setError(undefined);
    try {
      const report = await api.reports.create(run.id, {
        format,
        redact_content: redactContent,
        redact_endpoints: redactEndpoints,
      });
      setReports((prev) => [report, ...(prev ?? [])]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    try {
      await api.reports.remove(id);
      setReports((prev) => prev?.filter((r) => r.id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <Card title="Reports" icon={FileText}>
      {!finished ? (
        <p className="muted small" style={{ margin: 0 }}>Reports can be generated once the run has finished.</p>
      ) : (
        <div className="toolbar" style={{ flexWrap: "wrap", gap: 16, padding: "0 0 12px", alignItems: "flex-end" }}>
          <div className="field" style={{ minWidth: 220 }}>
            <label htmlFor="report-format">Format</label>
            <select id="report-format" className="input" value={format} onChange={(e) => setFormat(e.target.value as ReportFormat)}>
              {REPORT_FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
            </select>
            <span className="hint">{REPORT_FORMATS.find((f) => f.value === format)?.hint}</span>
          </div>
          <Switch label="Leave out generated text" checked={redactContent} onChange={setRedactContent} />
          <Switch label="Hide endpoint URLs" checked={redactEndpoints} onChange={setRedactEndpoints} />
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void generate()}>
            {busy ? "Generating…" : "Generate"}
          </button>
        </div>
      )}
      {error && <Callout tone="error" role="alert">{error}</Callout>}
      {reports && reports.length > 0 && (
        <div className="table-wrap">
          <table className="table" aria-label="Generated reports">
            <thead>
              <tr>
                <th scope="col">Report</th>
                <th scope="col">Generated</th>
                <th scope="col" className="num">Size</th>
                <th scope="col">Options</th>
                <th scope="col"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <ReportRow key={r.id} report={r} onDelete={() => void remove(r.id)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

export function ReportRow({ report: r, onDelete, showRun = false }: { report: Report; onDelete: () => void; showRun?: boolean }) {
  const redactions = [r.options.redact_content && "text left out", r.options.redact_endpoints && "URLs hidden"].filter(Boolean);
  return (
    <tr>
      <td>
        {formatLabel(r.report_type)}
        <div className="small muted mono">{r.filename}</div>
      </td>
      {showRun && <td>{r.run_name ? <Link to={`/runs/${r.run_id}`}>{r.run_name}</Link> : <span className="muted">deleted run</span>}</td>}
      <td className="small">{new Date(r.generated_at).toLocaleString()}</td>
      <td className="num small">{formatBytes(r.size_bytes)}</td>
      <td className="small">
        {redactions.length ? redactions.join(", ") : <span className="muted">full</span>}
        {r.report_metadata.results_truncated && <span className="badge badge-warn" style={{ marginLeft: 6 }}>truncated</span>}
      </td>
      <td>
        <div className="page-actions" style={{ justifyContent: "flex-end" }}>
          {r.report_type === "html" && (
            <a className="btn btn-sm" href={api.reports.downloadUrl(r.id, true)} target="_blank" rel="noopener noreferrer"
              aria-label={`Open ${r.filename}`}>
              <ExternalLink size={14} /> Open
            </a>
          )}
          <a className="btn btn-sm" href={api.reports.downloadUrl(r.id)} download={r.filename} aria-label={`Download ${r.filename}`}>
            <Download size={14} /> Download
          </a>
          <button type="button" className="icon-btn" onClick={onDelete} aria-label={`Delete ${r.filename}`}>
            <Trash2 size={16} />
          </button>
        </div>
      </td>
    </tr>
  );
}
