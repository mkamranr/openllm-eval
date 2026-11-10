import { Callout } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { api } from "../../services/api";

/** Settings → Tools (admin): the sandboxed tool runner and the tools it declares (read-only). */
export function ToolsTab() {
  const status = useAsync(() => api.tools.status(), []);
  const s = status.data;

  return (
    <div className="form-grid" style={{ paddingTop: 8 }}>
      <p className="small muted" style={{ margin: 0 }}>
        Integration-mode runs execute tool calls in the separate tool-runner container. Tools are declared by an
        administrator in <span className="mono">tools/tools.yaml</span> on the server and cannot be added here.
      </p>
      {status.error && <Callout tone="error" role="alert">Could not load the tool runner status: {status.error.message}</Callout>}
      {s && !s.enabled && (
        <Callout tone="info">
          Tool execution is off. To enable it, set <span className="mono">TOOL_EXECUTION_ENABLED=true</span> in .env and
          deploy with <span className="mono">bash scripts/deploy.sh --tools</span>.
        </Callout>
      )}
      {s && s.enabled && !s.reachable && (
        <Callout tone="error" role="alert">The tool runner is not reachable: {s.detail ?? "unknown error"}</Callout>
      )}
      {s?.registry_error && (
        <Callout tone="error" role="alert">tools.yaml is invalid, so no tools are offered: {s.registry_error}</Callout>
      )}
      {s && s.reachable && (
        <>
          <dl className="kv">
            <dt>Status</dt>
            <dd><span className="badge badge-ok">Reachable</span></dd>
            <dt>Registry (SHA-256)</dt>
            <dd className="mono small">{s.registry_sha256 ?? "—"}</dd>
            <dt>Tools</dt>
            <dd>{s.tools.length}</dd>
          </dl>
          <div className="table-wrap">
            <table className="table" aria-label="Registered tools">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Kind</th>
                  <th>Version</th>
                  <th>Timeout</th>
                  <th>Description</th>
                  <th>Parameters</th>
                </tr>
              </thead>
              <tbody>
                {s.tools.length === 0 && (
                  <tr><td colSpan={6} className="muted">No tools are declared.</td></tr>
                )}
                {s.tools.map((t) => (
                  <tr key={t.name}>
                    <td className="mono">{t.name}</td>
                    <td>{t.kind}</td>
                    <td>{t.version}</td>
                    <td>{t.timeout_seconds != null ? `${t.timeout_seconds} s` : "—"}</td>
                    <td>{t.description || "—"}</td>
                    <td>
                      <details>
                        <summary className="small">Schema</summary>
                        <pre className="code-block small">{JSON.stringify(t.parameters, null, 2)}</pre>
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
