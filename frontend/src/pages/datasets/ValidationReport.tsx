import { CheckCircle2, XCircle } from "lucide-react";

import type { Issue, ValidationResult } from "../../types/api";
import { typeLabel } from "./datasetMeta";

function IssueTable({ issues, total, label }: { issues: Issue[]; total: number; label: string }) {
  return (
    <div className="field">
      <span className="label">
        {label} ({total}
        {total > issues.length ? `, first ${issues.length} shown` : ""})
      </span>
      <div className="table-wrap issue-list card-flush" style={{ border: "1px solid var(--border)", borderRadius: 10 }}>
        <table className="table" aria-label={label}>
          <thead>
            <tr>
              <th scope="col">Line</th>
              <th scope="col">Case</th>
              <th scope="col">Field</th>
              <th scope="col">Problem</th>
            </tr>
          </thead>
          <tbody>
            {issues.map((issue, i) => (
              <tr key={i}>
                <td className="mono">{issue.line ?? "—"}</td>
                <td className="mono">{issue.case_id ?? "—"}</td>
                <td className="mono">{issue.field ?? "—"}</td>
                <td>{issue.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function ValidationReport({ result }: { result: ValidationResult }) {
  return (
    <section className="form" aria-label="Validation report">
      <div className={`callout ${result.valid ? "callout-ok" : "callout-error"}`} role="status">
        {result.valid ? <CheckCircle2 size={18} aria-hidden="true" /> : <XCircle size={18} aria-hidden="true" />}
        <div>
          <strong>
            {result.valid
              ? `Valid: ${result.record_count} records ready to import.`
              : `${result.error_count} error${result.error_count === 1 ? "" : "s"} found. Fix them and validate again.`}
          </strong>
          <div className="small">
            {result.format.toUpperCase()} · {result.rows} rows read · {result.record_count} valid ·{" "}
            {result.warning_count} warning{result.warning_count === 1 ? "" : "s"}
            {Object.keys(result.type_counts).length > 0 &&
              ` · ${Object.entries(result.type_counts)
                .map(([t, n]) => `${typeLabel(t)}: ${n}`)
                .join(", ")}`}
          </div>
          {result.content_hash && (
            <div className="small hash" title={result.content_hash}>
              sha256 {result.content_hash}
            </div>
          )}
        </div>
      </div>
      {result.errors.length > 0 && <IssueTable issues={result.errors} total={result.error_count} label="Errors" />}
      {result.warnings.length > 0 && (
        <IssueTable issues={result.warnings} total={result.warning_count} label="Warnings" />
      )}
      {result.valid && result.preview.length > 0 && (
        <details>
          <summary className="small" style={{ cursor: "pointer", fontWeight: 600 }}>
            Preview first {result.preview.length} normalized records
          </summary>
          <pre className="code-block" style={{ marginTop: 8 }}>
            {result.preview.map((r) => JSON.stringify(r, null, 2)).join("\n\n")}
          </pre>
        </details>
      )}
    </section>
  );
}
