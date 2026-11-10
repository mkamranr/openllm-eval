import { AlertTriangle, Copy, Pencil, Plus, Search, Server, Trash2, Zap } from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useCanEdit } from "../../auth/AuthContext";
import { EndpointStatusBadge } from "../../components/status/StatusBadge";
import {
  Callout,
  Card,
  ConfirmDialog,
  EmptyState,
  PageHeader,
  Switch,
  formatMs,
  formatRelative,
} from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { api } from "../../services/api";
import type { Endpoint, EndpointTestResult } from "../../types/api";
import { EndpointForm } from "./EndpointForm";
import { TestResultPanel } from "./TestResultPanel";

type Filter = "all" | "enabled" | "disabled";

export function EndpointsPage() {
  const canEdit = useCanEdit();
  const endpoints = useAsync(() => api.endpoints.list(), []);
  const projects = useAsync(() => api.projects.list(), []);
  const adapters = useAsync(() => api.adapters(), []);
  const [params, setParams] = useSearchParams();

  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<Endpoint | "new" | null>(
    params.get("new") === "1" ? "new" : null,
  );
  const [deleting, setDeleting] = useState<Endpoint | null>(null);
  const [testing, setTesting] = useState<{
    endpoint: Endpoint;
    result?: EndpointTestResult;
    error?: string;
    running: boolean;
  } | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string }>();

  const items = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (endpoints.data?.items ?? []).filter(
      (e) =>
        (filter === "all" || (filter === "enabled") === e.enabled) &&
        (!q ||
          [e.name, e.model_identifier, e.base_url, ...e.tags].some((v) =>
            v.toLowerCase().includes(q),
          )),
    );
  }, [endpoints.data, filter, search]);

  const projectName = (id: string | null) =>
    id ? (projects.data?.items.find((p) => p.id === id)?.name ?? "Unknown project") : "Global";

  const closeForm = () => {
    setEditing(null);
    if (params.has("new")) setParams({}, { replace: true });
  };

  async function run(action: () => Promise<unknown>, success: string) {
    try {
      await action();
      setNotice({ tone: "ok", text: success });
      await endpoints.reload();
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof Error ? err.message : String(err) });
    }
  }

  async function test(endpoint: Endpoint) {
    setTesting({ endpoint, running: true });
    try {
      const result = await api.endpoints.test(endpoint.id);
      setTesting({ endpoint, result, running: false });
    } catch (err) {
      setTesting({ endpoint, error: err instanceof Error ? err.message : String(err), running: false });
    }
    void endpoints.reload();
  }

  return (
    <div className="page">
      <PageHeader
        title="Models / Endpoints"
        description="Register OpenAI-compatible model servers (vLLM, llama.cpp, Ollama, hosted APIs) and verify connectivity."
        actions={
          canEdit && (
            <button type="button" className="btn btn-primary" onClick={() => setEditing("new")}>
              <Plus size={16} /> Add endpoint
            </button>
          )
        }
      />

      {notice && (
        <Callout tone={notice.tone} role={notice.tone === "error" ? "alert" : "status"}>
          {notice.text}
        </Callout>
      )}
      {endpoints.error && (
        <Callout tone="error" role="alert">
          Could not load endpoints: {endpoints.error.message}
        </Callout>
      )}

      <Card flush>
        <div className="toolbar">
          <div className="segmented" role="group" aria-label="Filter endpoints">
            {(["all", "enabled", "disabled"] as Filter[]).map((f) => (
              <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                {f[0]!.toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
          <div className="search">
            <Search size={16} aria-hidden="true" />
            <input
              className="input"
              type="search"
              placeholder="Search name, model, URL, tag"
              aria-label="Search endpoints"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>

        {endpoints.loading && !endpoints.data ? (
          <p className="muted" style={{ padding: 20 }} role="status">
            Loading endpoints…
          </p>
        ) : items.length === 0 ? (
          <EmptyState
            icon={Server}
            title={endpoints.data?.total ? "No endpoints match" : "No endpoints yet"}
            action={
              !endpoints.data?.total && (
                <button type="button" className="btn btn-primary btn-sm" onClick={() => setEditing("new")}>
                  <Plus size={16} /> Add your first endpoint
                </button>
              )
            }
          >
            {endpoints.data?.total
              ? "Try a different filter or search."
              : "Point OpenLLM-Eval at any OpenAI-compatible server. Want a demo? Start the mock server with docker compose --profile mock up -d and register http://mock-llm:9000/v1."}
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table className="table" aria-label="Endpoints">
              <thead>
                <tr>
                  <th scope="col">Endpoint</th>
                  <th scope="col">Base URL</th>
                  <th scope="col">Project</th>
                  <th scope="col">Status</th>
                  <th scope="col">Last test</th>
                  <th scope="col">Enabled</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <div className="cell-main" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        {e.name}
                        {e.warnings.length > 0 && (
                          <span title={e.warnings.join("\n")} aria-label={`Warnings: ${e.warnings.join("; ")}`}
                            style={{ color: "var(--warning)", display: "inline-flex" }}>
                            <AlertTriangle size={14} />
                          </span>
                        )}
                      </div>
                      <div className="cell-sub mono">{e.model_identifier}</div>
                      {e.tags.length > 0 && (
                        <div className="tags" style={{ marginTop: 4 }}>
                          {e.tags.map((t) => (
                            <span className="tag" key={t}>
                              {t}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="url-cell mono" title={e.base_url}>
                      {e.base_url}
                    </td>
                    <td>{projectName(e.project_id)}</td>
                    <td>
                      <EndpointStatusBadge status={e.last_test_status} />
                      {e.last_test_summary?.error_type && (
                        <div className="cell-sub">{e.last_test_summary.error_type.replace(/_/g, " ")}</div>
                      )}
                    </td>
                    <td>
                      {e.last_test_summary ? (
                        <>
                          <div>
                            {formatMs(e.last_test_summary.latency_ms)}
                            {e.last_test_summary.ttft_ms != null &&
                              ` · TTFT ${formatMs(e.last_test_summary.ttft_ms)}`}
                          </div>
                          <div className="cell-sub">{formatRelative(e.last_tested_at)}</div>
                        </>
                      ) : (
                        <span className="muted">Never tested</span>
                      )}
                    </td>
                    <td>
                      <Switch
                        hideLabel
                        label={`Enable ${e.name}`}
                        checked={e.enabled}
                        onChange={(enabled) =>
                          void run(
                            () => api.endpoints.update(e.id, { enabled }),
                            `${e.name} ${enabled ? "enabled" : "disabled"}.`,
                          )
                        }
                      />
                    </td>
                    <td>
                      <div className="actions">
                        <button type="button" className="btn btn-sm" onClick={() => void test(e)}
                          aria-label={`Test ${e.name}`}>
                          <Zap size={14} /> Test
                        </button>
                        <button type="button" className="icon-btn" onClick={() => setEditing(e)}
                          aria-label={`Edit ${e.name}`} title="Edit">
                          <Pencil size={16} />
                        </button>
                        <button type="button" className="icon-btn"
                          onClick={() => void run(() => api.endpoints.clone(e.id), `Cloned ${e.name}.`)}
                          aria-label={`Clone ${e.name}`} title="Clone">
                          <Copy size={16} />
                        </button>
                        <button type="button" className="icon-btn danger" onClick={() => setDeleting(e)}
                          aria-label={`Delete ${e.name}`} title="Delete">
                          <Trash2 size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {editing && (
        <EndpointForm
          endpoint={editing === "new" ? undefined : editing}
          projects={projects.data?.items ?? []}
          adapters={adapters.data ?? [{ adapter_type: "openai_compatible", display_name: "OpenAI-compatible", version: "" }]}
          onClose={closeForm}
          onSaved={(saved) => {
            closeForm();
            setNotice({ tone: "ok", text: `Saved ${saved.name}. Run a connection test to verify it.` });
            void endpoints.reload();
          }}
        />
      )}

      {testing && (
        <TestResultPanel
          endpoint={testing.endpoint}
          result={testing.result}
          error={testing.error}
          running={testing.running}
          onRetest={() => void test(testing.endpoint)}
          onClose={() => setTesting(null)}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title={`Delete ${deleting.name}?`}
          confirmLabel="Delete endpoint"
          onCancel={() => setDeleting(null)}
          onConfirm={() => {
            const target = deleting;
            setDeleting(null);
            void run(() => api.endpoints.remove(target.id), `Deleted ${target.name}.`);
          }}
        >
          This removes the endpoint configuration. Results of past runs keep their own snapshot.
        </ConfirmDialog>
      )}
    </div>
  );
}
