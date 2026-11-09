import {
  Activity,
  BarChart3,
  CheckCircle2,
  Clock,
  Cpu,
  Database,
  FolderKanban,
  HeartPulse,
  Plus,
  RefreshCw,
  Rocket,
  Server,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { EndpointStatusBadge, StatusBadge } from "../../components/status/StatusBadge";
import { Callout, Card, EmptyState, StatCard, formatMs, formatRelative } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { api } from "../../services/api";
import type { ComponentStatus, WorkerStatus } from "../../types/health";
import { ProgressBar } from "../runs/RunsPage";
import { RunStatusBadge, profileSummary } from "../runs/runMeta";

export function Dashboard() {
  const readiness = useAsync(() => api.readiness(), []);
  const version = useAsync(() => api.version(), []);
  const endpoints = useAsync(() => api.endpoints.list(), []);
  const projects = useAsync(() => api.projects.list(), []);
  const runs = useAsync(() => api.runs.list({ limit: 5 }), []);
  const navigate = useNavigate();

  const refresh = () => {
    void readiness.reload();
    void version.reload();
    void endpoints.reload();
    void projects.reload();
    void runs.reload();
  };

  const eps = endpoints.data?.items ?? [];
  const enabled = eps.filter((e) => e.enabled).length;
  const tested = eps.filter((e) => e.last_test_status !== null);
  const healthy = tested.filter((e) => e.last_test_status === "ok").length;
  const ttfts = eps
    .map((e) => e.last_test_summary?.ttft_ms)
    .filter((v): v is number => typeof v === "number");
  const status = readiness.loading
    ? "checking"
    : readiness.data?.status === "ok"
      ? "healthy"
      : "degraded";

  return (
    <div className="page">
      <section className="hero" aria-label="Welcome">
        <div style={{ position: "relative", zIndex: 1 }}>
          <h1>Welcome to OpenLLM-Eval</h1>
          <p>Evaluate. Compare. Optimize. Make evidence-based decisions about your LLMs.</p>
        </div>
        <div className="hero-side">
          <span className={`status-pill ${status === "healthy" ? "" : status}`} role="status">
            <span className="dot" aria-hidden="true" />
            {status === "checking"
              ? "Checking system…"
              : status === "healthy"
                ? "System healthy"
                : "System degraded"}
          </span>
          <button
            type="button"
            className="btn btn-ghost-light btn-sm"
            onClick={refresh}
            aria-label="Refresh dashboard"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </section>

      {readiness.error && (
        <Callout tone="error" role="alert">
          <strong>API unreachable.</strong> {readiness.error.message}
        </Callout>
      )}

      <div className="grid grid-4">
        <StatCard
          label="Evaluations"
          value={runs.data?.total ?? "—"}
          icon={BarChart3}
          tone="violet"
          note={projects.data ? `${projects.data.total} project${projects.data.total === 1 ? "" : "s"}` : undefined}
        />
        <StatCard
          label="Model endpoints"
          value={endpoints.data?.total ?? "—"}
          icon={Server}
          tone="blue"
          note={endpoints.data ? `${enabled} enabled` : undefined}
        />
        <StatCard
          label="Healthy endpoints"
          value={endpoints.data ? `${healthy}/${tested.length}` : "—"}
          icon={CheckCircle2}
          tone="green"
          note="Passing their last connection test"
        />
        <StatCard
          label="Best test TTFT"
          value={ttfts.length ? formatMs(Math.min(...ttfts)) : "—"}
          icon={Clock}
          tone="orange"
          note={ttfts.length ? "Single test request, not a benchmark" : "Test a streaming endpoint"}
        />
      </div>

      <div className="grid grid-main">
        <Card
          title="Model endpoints"
          icon={Server}
          flush
          action={
            <Link to="/endpoints" className="btn btn-sm">
              View all
            </Link>
          }
        >
          {endpoints.loading && !endpoints.data ? (
            <p className="muted" style={{ padding: 20 }} role="status">
              Loading endpoints…
            </p>
          ) : eps.length === 0 ? (
            <EmptyState
              icon={Server}
              title="No endpoints yet"
              action={
                <Link to="/endpoints?new=1" className="btn btn-primary btn-sm">
                  <Plus size={16} /> Add endpoint
                </Link>
              }
            >
              Register a vLLM, llama.cpp, Ollama, or hosted OpenAI-compatible endpoint.
            </EmptyState>
          ) : (
            <div className="table-wrap" style={{ marginTop: 16 }}>
              <table className="table" aria-label="Model endpoints">
                <thead>
                  <tr>
                    <th scope="col">Endpoint</th>
                    <th scope="col">Hardware</th>
                    <th scope="col">Last test</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {eps.slice(0, 6).map((e) => (
                    <tr key={e.id}>
                      <td>
                        <div className="cell-main">{e.name}</div>
                        <div className="cell-sub mono">{e.model_identifier}</div>
                      </td>
                      <td>{String(e.deployment_metadata.gpu ?? "—")}</td>
                      <td>
                        {e.last_test_summary
                          ? `${formatMs(e.last_test_summary.latency_ms)}${
                              e.last_test_summary.ttft_ms != null
                                ? ` · TTFT ${formatMs(e.last_test_summary.ttft_ms)}`
                                : ""
                            }`
                          : "—"}
                      </td>
                      <td>
                        <EndpointStatusBadge status={e.last_test_status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <div className="cta-card">
          <Rocket size={34} className="cta-icon" aria-hidden="true" />
          <h2>Connect a model</h2>
          <p>Register an endpoint and verify connectivity, streaming, and time to first token.</p>
          <Link to="/endpoints?new=1" className="btn btn-white">
            <Plus size={16} /> Add endpoint
          </Link>
          <Link to="/projects" className="btn btn-ghost-light">
            <FolderKanban size={16} /> Manage projects
          </Link>
        </div>
      </div>

      <div className="grid grid-main">
        <Card
          title="Recent evaluations"
          icon={BarChart3}
          flush
          action={
            <Link to="/runs" className="btn btn-sm">
              View all
            </Link>
          }
        >
          {runs.loading && !runs.data ? (
            <p className="muted" style={{ padding: 20 }} role="status">
              Loading runs…
            </p>
          ) : !runs.data?.items.length ? (
            <EmptyState
              icon={Activity}
              title="No benchmark runs yet"
              action={
                <Link to="/new" className="btn btn-primary btn-sm">
                  <Plus size={16} /> New evaluation
                </Link>
              }
            >
              Run a performance evaluation to measure TTFT, latency, and throughput.
            </EmptyState>
          ) : (
            <div className="table-wrap" style={{ marginTop: 16 }}>
              <table className="table" aria-label="Recent evaluations">
                <thead>
                  <tr>
                    <th scope="col">Run</th>
                    <th scope="col">Status</th>
                    <th scope="col" style={{ minWidth: 120 }}>Progress</th>
                    <th scope="col">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.data.items.map((r) => (
                    <tr key={r.id} className="clickable" onClick={() => navigate(`/runs/${r.id}`)}>
                      <td>
                        <Link to={`/runs/${r.id}`} className="cell-main" onClick={(e) => e.stopPropagation()}>
                          {r.name}
                        </Link>
                        <div className="cell-sub">
                          {r.models.map((m) => m.name).join(", ")} · {profileSummary(r)}
                        </div>
                      </td>
                      <td>
                        <RunStatusBadge status={r.status} />
                      </td>
                      <td>
                        <ProgressBar run={r} />
                      </td>
                      <td className="muted">{formatRelative(r.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="System health" icon={HeartPulse}>
          {readiness.data ? (
            <div className="health-list" aria-label="System health">
              <HealthRow name="API" icon={Cpu} state="ok"
                detail={version.data ? `v${version.data.version}` : undefined} />
              <HealthRow name="Database" icon={Database} component={readiness.data.database} />
              <HealthRow name="Redis" icon={Activity} component={readiness.data.redis} />
              <HealthRow name="Worker" icon={Rocket} worker={readiness.data.worker} />
            </div>
          ) : readiness.loading ? (
            <p className="muted" role="status">Checking API…</p>
          ) : (
            <p className="muted">Health unavailable.</p>
          )}
          {readiness.data?.status === "degraded" && (
            <div style={{ marginTop: 12 }}>
              <Callout tone="warn">
                One or more required services are not ready. Benchmarks cannot run until they
                recover.
              </Callout>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

function HealthRow({
  name,
  icon: Icon,
  state,
  detail,
  component,
  worker,
}: {
  name: string;
  icon: typeof Cpu;
  state?: "ok";
  detail?: string;
  component?: ComponentStatus;
  worker?: WorkerStatus;
}) {
  const s = state ?? component?.status ?? worker?.status ?? "unknown";
  const text =
    detail ??
    component?.detail ??
    (component?.latency_ms != null ? `${component.latency_ms.toFixed(1)} ms` : undefined) ??
    worker?.detail ??
    (worker ? `${worker.active_workers} active` : undefined);
  return (
    <div className="health-row" data-testid={`health-${name.toLowerCase()}`}>
      <span className="name">
        <Icon size={16} aria-hidden="true" />
        {name}
      </span>
      <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
        {text && <span className="detail">{text}</span>}
        <StatusBadge state={s} />
      </span>
    </div>
  );
}
