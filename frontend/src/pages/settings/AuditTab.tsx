import { useState } from "react";

import { Callout } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { api } from "../../services/api";

const ACTIONS = [
  "login",
  "login_failed",
  "logout",
  "password_changed",
  "password_reset",
  "user_created",
  "user_updated",
  "user_deleted",
  "sessions_revoked",
  "token_created",
  "token_revoked",
  "auth_settings_updated",
  "report_downloaded",
];

function summarize(details: Record<string, unknown>): string {
  return Object.entries(details)
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
}

export function AuditTab() {
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [offset, setOffset] = useState(0);
  const events = useAsync(() => api.audit({ action: action || undefined, actor: actor || undefined, offset }), [action, actor, offset]);
  const total = events.data?.total ?? 0;

  return (
    <div className="form-grid" style={{ paddingTop: 8 }}>
      <div className="toolbar" style={{ padding: 0 }}>
        <select className="input" aria-label="Filter by action" value={action} style={{ maxWidth: 240 }}
          onChange={(e) => { setAction(e.target.value); setOffset(0); }}>
          <option value="">All actions</option>
          {ACTIONS.map((a) => <option key={a} value={a}>{a.replace(/_/g, " ")}</option>)}
        </select>
        <input className="input" aria-label="Filter by user" placeholder="User" value={actor} style={{ maxWidth: 200 }}
          onChange={(e) => { setActor(e.target.value); setOffset(0); }} />
      </div>
      {events.error && <Callout tone="error" role="alert">Could not load the audit log: {events.error.message}</Callout>}
      <div className="table-wrap">
        <table className="table" aria-label="Audit log">
          <thead>
            <tr><th scope="col">When</th><th scope="col">User</th><th scope="col">Action</th><th scope="col">Target</th><th scope="col">Address</th><th scope="col">Details</th></tr>
          </thead>
          <tbody>
            {(events.data?.items ?? []).map((e) => (
              <tr key={e.id}>
                <td style={{ whiteSpace: "nowrap" }}>{new Date(e.at).toLocaleString()}</td>
                <td>{e.actor_name ?? "—"}</td>
                <td><span className={`badge ${e.action.endsWith("failed") ? "badge-error" : "badge-info"}`}>{e.action.replace(/_/g, " ")}</span></td>
                <td>{e.target ?? "—"}</td>
                <td className="mono small">{e.ip ?? "—"}</td>
                <td className="small muted">{summarize(e.details)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="toolbar" style={{ padding: 0, justifyContent: "space-between" }}>
        <span className="small muted">{total ? `${offset + 1}–${Math.min(offset + 100, total)} of ${total}` : "No events"}</span>
        <div className="page-actions">
          <button type="button" className="btn btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Newer</button>
          <button type="button" className="btn btn-sm" disabled={offset + 100 >= total} onClick={() => setOffset(offset + 100)}>Older</button>
        </div>
      </div>
    </div>
  );
}
