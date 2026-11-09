import { Activity, Plus, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Callout, Card, EmptyState, PageHeader, formatRelative } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { api } from "../../services/api";
import type { Run, RunStatus } from "../../types/runs";
import { RunStatusBadge, fmtDuration, isActive, isPending, profileSummary, progressOf } from "./runMeta";

const FILTERS: { key: string; label: string; statuses?: RunStatus[] }[] = [
  { key: "", label: "All runs" },
  { key: "active", label: "Active", statuses: ["created", "queued", "initializing", "running", "finalizing", "cancel_requested"] },
  { key: "completed", label: "Completed", statuses: ["completed"] },
  { key: "errors", label: "With errors", statuses: ["completed_with_errors", "failed"] },
  { key: "cancelled", label: "Cancelled", statuses: ["cancelled"] },
];

export function ProgressBar({ run }: { run: Run }) {
  const { done, planned, pct } = progressOf(run);
  const failed = run.progress?.failed ?? 0;
  const failPct = planned ? Math.min(100, (failed / planned) * 100) : 0;
  const label = planned ? `${done} of ${planned} requests` : `${done} requests`;
  if (pct == null) {
    return (
      <div className={`progress ${isActive(run.status) ? "indeterminate" : ""}`} role="progressbar" aria-label={label}>
        <span className="ok" />
      </div>
    );
  }
  return (
    <div className="progress" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={Math.round(pct)}>
      <span className="ok" style={{ width: `${Math.max(0, pct - failPct)}%` }} />
      <span className="fail" style={{ width: `${failPct}%` }} />
    </div>
  );
}

export function RunsPage() {
  const navigate = useNavigate();
  const [filter, setFilter] = useState("");
  const [search, setSearch] = useState("");
  const runs = useAsync(() => api.runs.list({ limit: 200 }), []);
  const items = useMemo(() => runs.data?.items ?? [], [runs.data]);
  const anyLive = items.some((r) => isActive(r.status) || isPending(r.status));
  const { reload } = runs;

  // Refresh while runs are queued or executing; the detail page has the live event stream.
  useEffect(() => {
    if (!anyLive) return;
    const t = window.setInterval(() => void reload(), 3000);
    return () => window.clearInterval(t);
  }, [anyLive, reload]);

  const shown = useMemo(() => {
    const statuses = FILTERS.find((f) => f.key === filter)?.statuses;
    const q = search.trim().toLowerCase();
    return items.filter(
      (r) =>
        (!statuses || statuses.includes(r.status)) &&
        (!q || [r.name, ...r.models.map((m) => m.name), ...r.datasets.map((d) => d.name)].some((v) => v.toLowerCase().includes(q))),
    );
  }, [items, filter, search]);

  return (
    <div className="page">
      <PageHeader
        icon={Activity}
        title="Benchmark Runs"
        description="Every run keeps an immutable snapshot of its configuration, every request it sent, and its metrics."
        actions={
          <Link to="/new" className="btn btn-primary">
            <Plus size={16} /> New evaluation
          </Link>
        }
      />
      {runs.error && <Callout tone="error" role="alert">Could not load runs: {runs.error.message}</Callout>}
      <Card flush>
        <div className="toolbar" style={{ padding: "16px 20px" }}>
          <div className="segmented" role="group" aria-label="Filter runs">
            {FILTERS.map((f) => (
              <button key={f.key} type="button" aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}>
                {f.label}
              </button>
            ))}
          </div>
          <div className="search">
            <Search size={16} aria-hidden="true" />
            <input className="input" type="search" placeholder="Search run, model, dataset" aria-label="Search runs"
              value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
        </div>
        {runs.loading && !runs.data ? (
          <p className="muted" style={{ padding: 20 }} role="status">Loading runs…</p>
        ) : shown.length === 0 ? (
          <EmptyState
            icon={Activity}
            title={items.length ? "No runs match" : "No benchmark runs yet"}
            action={!items.length && <Link to="/new" className="btn btn-primary btn-sm"><Plus size={16} /> New evaluation</Link>}
          >
            {items.length ? "Try another filter." : "Start an evaluation to measure TTFT, latency, and throughput."}
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table" aria-label="Runs">
              <thead>
                <tr>
                  <th scope="col">Run</th>
                  <th scope="col">Status</th>
                  <th scope="col">Models</th>
                  <th scope="col">Dataset</th>
                  <th scope="col" style={{ minWidth: 160 }}>Progress</th>
                  <th scope="col">Duration</th>
                  <th scope="col">Created</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => {
                  const { done, planned } = progressOf(r);
                  return (
                    <tr key={r.id} className="clickable" onClick={() => navigate(`/runs/${r.id}`)}>
                      <td>
                        <Link to={`/runs/${r.id}`} className="cell-main" onClick={(e) => e.stopPropagation()}>{r.name}</Link>
                        <div className="cell-sub">{profileSummary(r)}</div>
                      </td>
                      <td><RunStatusBadge status={r.status} /></td>
                      <td>
                        <div className="chips-inline">
                          {r.models.map((m) => <span key={m.id} className="tag">{m.name}</span>)}
                        </div>
                      </td>
                      <td>
                        {r.datasets.map((d) => d.name.replace(/^Sample: /, "")).join(", ")}
                        <div className="cell-sub">{r.load.case_count} cases</div>
                      </td>
                      <td>
                        <div className="progress-row">
                          <ProgressBar run={r} />
                          <div className="meta">
                            <span>{planned ? `${done}/${planned}` : done}</span>
                            {(r.progress?.failed ?? 0) > 0 && <span style={{ color: "var(--danger)" }}>{r.progress.failed} failed</span>}
                          </div>
                        </div>
                      </td>
                      <td className="muted">{fmtDuration(r.started_at, r.completed_at)}</td>
                      <td className="muted">{formatRelative(r.created_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
