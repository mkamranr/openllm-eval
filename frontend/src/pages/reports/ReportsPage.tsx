import { FileText } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Callout, Card, EmptyState, PageHeader } from "../../components/ui";
import { api } from "../../services/api";
import type { Report } from "../../types/reports";
import { ReportRow } from "../runs/ReportsCard";

const PAGE = 50;

export function ReportsPage() {
  const [items, setItems] = useState<Report[]>();
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string>();

  const load = useCallback(() => {
    api.reports
      .list({ limit: PAGE, offset })
      .then((p) => (setItems(p.items), setTotal(p.total), setError(undefined)))
      .catch((e: Error) => setError(e.message));
  }, [offset]);
  useEffect(load, [load]);

  async function remove(id: string) {
    try {
      await api.reports.remove(id);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="page">
      <PageHeader icon={FileText} title="Reports"
        description="Generated reports and exports. Each one is a fixed snapshot of its run at the time it was generated." />
      {error && <Callout tone="error" role="alert">{error}</Callout>}
      {!items ? (
        <p className="muted" role="status">Loading reports…</p>
      ) : items.length === 0 ? (
        <EmptyState icon={FileText} title="No reports yet"
          action={<Link className="btn" to="/runs">Go to runs</Link>}>
          Open a finished run and generate an HTML report, or a JSON, CSV, or Markdown export, from its Reports card.
        </EmptyState>
      ) : (
        <Card flush>
          <div className="table-wrap">
            <table className="table" aria-label="Reports">
              <thead>
                <tr>
                  <th scope="col">Report</th>
                  <th scope="col">Run</th>
                  <th scope="col">Generated</th>
                  <th scope="col" className="num">Size</th>
                  <th scope="col">Options</th>
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {items.map((r) => (
                  <ReportRow key={r.id} report={r} showRun onDelete={() => void remove(r.id)} />
                ))}
              </tbody>
            </table>
          </div>
          {total > PAGE && (
            <div className="toolbar" style={{ justifyContent: "space-between", padding: 12 }}>
              <span className="small muted">{offset + 1}–{offset + items.length} of {total}</span>
              <div className="page-actions">
                <button type="button" className="btn btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
                <button type="button" className="btn btn-sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Next</button>
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
