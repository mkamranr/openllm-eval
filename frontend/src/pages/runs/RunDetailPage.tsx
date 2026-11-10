import {
  Activity,
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock,
  Gauge,
  ListChecks,
  RotateCcw,
  Settings2,
  Square,
  Trash2,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  Callout,
  Card,
  ConfirmDialog,
  Drawer,
  EmptyState,
  PageHeader,
  StatCard,
  TabPanel,
  Tabs,
  formatRelative,
} from "../../components/ui";
import { api } from "../../services/api";
import type { Page } from "../../types/api";
import type {
  CaseEvent,
  CaseResult,
  CaseResultDetail,
  MetricValue,
  RunDetail,
  RunMetrics,
  RunModel,
} from "../../types/runs";
import {
  EvaluationChips,
  EvaluationList,
  EvidenceMetricsCard,
  QualityCard,
  SimulationView,
  SUITE_TITLES,
  ToolMetricsCard,
} from "./QualitySection";
import {
  PROFILE_META,
  RunStatusBadge,
  fmt,
  fmtDuration,
  fmtMs,
  fmtPct,
  isActive,
  isPending,
  isTerminal,
  metricIndex,
  metricTitle,
  progressOf,
} from "./runMeta";
import { ProgressBar } from "./RunsPage";

const PAGE_SIZE = 25;
const FEED_SIZE = 30;
const POLL_MS = 2000;
const LIVE_METRICS_MS = 5000;

type Get = ReturnType<typeof metricIndex>;

/** Live run state: Server-Sent Events when available, polling otherwise. */
function useLiveRun(runId: string, live: boolean, onRun: (r: Partial<RunDetail>) => void,
  onCase: (e: CaseEvent) => void, onEnd: () => void) {
  const handlers = useRef({ onRun, onCase, onEnd });
  handlers.current = { onRun, onCase, onEnd };
  useEffect(() => {
    if (!live) return;
    if (typeof EventSource === "undefined") {
      const t = window.setInterval(() => {
        api.runs.get(runId).then((r) => {
          handlers.current.onRun(r);
          if (isTerminal(r.status)) handlers.current.onEnd();
        }).catch(() => undefined);
      }, POLL_MS);
      return () => window.clearInterval(t);
    }
    const es = new EventSource(api.runs.eventsUrl(runId));
    const parse = (e: Event) => JSON.parse((e as MessageEvent<string>).data);
    es.addEventListener("run.progress", (e) => handlers.current.onRun(parse(e)));
    es.addEventListener("case.completed", (e) => handlers.current.onCase(parse(e)));
    es.addEventListener("case.failed", (e) => handlers.current.onCase(parse(e)));
    es.addEventListener("run.end", () => {
      es.close();
      handlers.current.onEnd();
    });
    return () => es.close();
  }, [runId, live]);
}

function Na({ m, children }: { m: MetricValue | undefined; children: string }) {
  return <span title={metricTitle(m)}>{children}</span>;
}

// ------------------------------------------------------------------------------- sections

function ModelProgressList({ run }: { run: RunDetail }) {
  const planned = run.load.requests;
  return (
    <div className="model-progress">
      {run.models.map((m) => {
        const p = m.progress ?? {};
        const done = p.completed ?? 0;
        const pct = planned ? Math.min(100, (done / planned) * 100) : null;
        const failPct = planned ? Math.min(100, ((p.failed ?? 0) / planned) * 100) : 0;
        const phase =
          p.phase === "warmup"
            ? `Warm-up ${p.warmup_completed ?? 0}/${p.warmup_planned ?? 0}`
            : p.phase === "judging"
              ? `Judging ${p.judge_completed ?? 0}/${p.judge_planned ?? 0}`
              : p.phase === "measured"
              ? `Measuring${p.current_concurrency ? ` · concurrency ${p.current_concurrency}` : ""}${p.current_step != null ? ` · step ${p.current_step + 1}` : ""}`
              : m.status === "pending"
                ? "Waiting"
                : m.status === "completed"
                  ? "Done"
                  : m.status;
        return (
          <div key={m.id} className="progress-row" data-testid={`model-${m.name}`}>
            <div className="head">
              <div>
                <strong>{m.name}</strong> <span className="small muted mono">{m.model_identifier}</span>
              </div>
              <span className="small muted">{phase}</span>
            </div>
            <div className="progress" role="progressbar" aria-label={`${m.name} progress`}
              aria-valuenow={pct != null ? Math.round(pct) : undefined} aria-valuemin={0} aria-valuemax={100}>
              <span className="ok" style={{ width: `${pct != null ? Math.max(0, pct - failPct) : 0}%` }} />
              <span className="fail" style={{ width: `${failPct}%` }} />
            </div>
            <div className="meta">
              <span>
                {done}{planned ? ` / ${planned}` : ""} requests · {p.succeeded ?? 0} ok
                {(p.failed ?? 0) > 0 && <span style={{ color: "var(--danger)" }}> · {p.failed} failed</span>}
                {(p.retries ?? 0) > 0 && <> · {p.retries} retries</>}
                {(p.in_flight ?? 0) > 0 && <> · {p.in_flight} in flight</>}
              </span>
              {p.live && (p.live.ttft_ms_p50 != null || p.live.latency_ms_p50 != null) && (
                <span>
                  TTFT p50 {fmtMs(p.live.ttft_ms_p50)} · latency p50 {fmtMs(p.live.latency_ms_p50)}
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const COLUMNS: { label: string; name: string; agg: string; kind: "ms" | "pct" | "num" | "rate" }[] = [
  { label: "TTFT p50", name: "ttft_ms", agg: "p50", kind: "ms" },
  { label: "TTFT p95", name: "ttft_ms", agg: "p95", kind: "ms" },
  { label: "Latency p50", name: "latency_ms", agg: "p50", kind: "ms" },
  { label: "Latency p95", name: "latency_ms", agg: "p95", kind: "ms" },
  { label: "Latency p99", name: "latency_ms", agg: "p99", kind: "ms" },
  { label: "ITL p50", name: "inter_token_latency_ms", agg: "p50", kind: "ms" },
  { label: "Tokens/s p50", name: "output_tokens_per_second", agg: "p50", kind: "num" },
  { label: "Req/s", name: "request_throughput", agg: "value", kind: "rate" },
  { label: "Output tok/s", name: "output_token_throughput", agg: "value", kind: "num" },
];

function cell(m: MetricValue | undefined, kind: string) {
  const v = m?.metric_value;
  const text = kind === "ms" ? fmtMs(v) : kind === "pct" ? fmtPct(v) : kind === "rate" ? fmt(v, 2) : fmt(v, 1);
  return (
    <td className={`num ${v == null ? "na" : ""}`} key={`${m?.metric_name}-${m?.aggregation}`}>
      <Na m={m}>{text}</Na>
    </td>
  );
}

function MetricsTable({ run, get, step = null, label }: { run: RunDetail; get: Get; step?: number | null; label: string }) {
  return (
    <div className="table-wrap">
      <table className="table" aria-label={label}>
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col" className="num">OK / requests</th>
            <th scope="col" className="num">Error rate</th>
            {COLUMNS.map((c) => <th key={c.label} scope="col" className="num">{c.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {run.models.map((m) => {
            const err = get(m.id, "error_rate", "ratio", step);
            return (
              <tr key={m.id}>
                <td>
                  <div className="cell-main">{m.name}</div>
                  <div className="cell-sub mono">{m.model_identifier}</div>
                </td>
                <td className="num">
                  {fmt(get(m.id, "requests", "succeeded", step)?.metric_value, 0)} / {fmt(get(m.id, "requests", "attempted", step)?.metric_value, 0)}
                </td>
                <td className={`num ${err?.metric_value ? "" : "na"}`} style={err?.metric_value ? { color: "var(--danger)" } : undefined}>
                  <Na m={err}>{fmtPct(err?.metric_value)}</Na>
                </td>
                {COLUMNS.map((c) => cell(get(m.id, c.name, c.agg, step), c.kind))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RampTable({ run, get, model }: { run: RunDetail; get: Get; model: RunModel }) {
  return (
    <div className="table-wrap">
      <table className="table" aria-label={`Ramp steps for ${model.name}`}>
        <thead>
          <tr>
            <th scope="col">Step</th>
            <th scope="col" className="num">Concurrency</th>
            <th scope="col" className="num">OK / requests</th>
            <th scope="col" className="num">Error rate</th>
            <th scope="col" className="num">TTFT p50</th>
            <th scope="col" className="num">Latency p95</th>
            <th scope="col" className="num">Req/s</th>
            <th scope="col" className="num">Output tok/s</th>
          </tr>
        </thead>
        <tbody>
          {run.load.steps.map((s) => {
            const step = s.step;
            return (
              <tr key={step ?? 0}>
                <td>{(step ?? 0) + 1}</td>
                <td className="num">{s.concurrency}</td>
                <td className="num">
                  {fmt(get(model.id, "requests", "succeeded", step)?.metric_value, 0)} / {fmt(get(model.id, "requests", "attempted", step)?.metric_value, 0)}
                </td>
                {cell(get(model.id, "error_rate", "ratio", step), "pct")}
                {cell(get(model.id, "ttft_ms", "p50", step), "ms")}
                {cell(get(model.id, "latency_ms", "p95", step), "ms")}
                {cell(get(model.id, "request_throughput", "value", step), "rate")}
                {cell(get(model.id, "output_token_throughput", "value", step), "num")}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function LiveFeed({ events, models }: { events: CaseEvent[]; models: RunModel[] }) {
  const name = (id: string) => models.find((m) => m.id === id)?.name ?? "";
  if (!events.length) return <p className="muted small" style={{ margin: 0 }}>Waiting for the first responses…</p>;
  return (
    <ul className="live-feed" aria-label="Recent requests">
      {events.map((e) => (
        <li key={e.id}>
          <span className="mono small">#{e.request_index}{e.phase === "warmup" ? " w" : ""}</span>
          <span className="small" style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {e.case_identifier} <span className="muted hide-sm">· {name(e.run_model_id)}</span>
          </span>
          <span className={`small ${e.status === "failed" ? "fail" : ""}`}>
            {e.status === "failed" ? `${e.error_type ?? "failed"}${e.http_status ? ` ${e.http_status}` : ""}` : fmtMs(e.latency_ms)}
          </span>
          <span className="small muted hide-sm">{e.ttft_ms != null ? `TTFT ${fmtMs(e.ttft_ms)}` : ""}</span>
        </li>
      ))}
    </ul>
  );
}

type ResultTab = "all" | "failed" | "quality" | "warmup";

function ResultsBrowser({ run, version }: { run: RunDetail; version: number }) {
  const [tab, setTab] = useState<ResultTab>("all");
  const [model, setModel] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page<CaseResult>>();
  const [error, setError] = useState<string>();
  const [open, setOpen] = useState<CaseResult | CaseResultDetail | null>(null);
  const quality = (run.configuration_snapshot.evaluation?.suites.length ?? 0) > 0;

  useEffect(() => setOffset(0), [tab, model, q]);
  useEffect(() => {
    let cancelled = false;
    api.runs
      .results(run.id, {
        status: tab === "failed" ? "failed" : undefined,
        phase: tab === "warmup" ? "warmup" : tab === "quality" ? "measured" : undefined,
        evaluation: tab === "quality" ? "failed" : undefined,
        run_model_id: model || undefined,
        q: q.trim() || undefined,
        limit: PAGE_SIZE,
        offset,
      })
      .then((p) => !cancelled && (setPage(p), setError(undefined)))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [run.id, tab, model, q, offset, version]);

  async function openResult(r: CaseResult) {
    setOpen(r);
    if (r.truncated || r.evaluations.length > 0) {
      try {
        setOpen(await api.runs.result(run.id, r.id));
      } catch {
        // keep the preview
      }
    }
  }

  const modelName = (id: string) => run.models.find((m) => m.id === id)?.name ?? "";
  return (
    <>
      <Tabs label="Result views" value={tab} onChange={setTab} items={[
        { key: "all", label: "All requests" },
        { key: "failed", label: "Failures" },
        ...(quality ? [{ key: "quality" as const, label: "Quality failures" }] : []),
        { key: "warmup", label: "Warm-up" },
      ]} />
      <TabPanel id={tab}>
        <div className="toolbar" style={{ padding: "0 0 12px" }}>
          {run.models.length > 1 && (
            <select className="input" aria-label="Filter by model" value={model} onChange={(e) => setModel(e.target.value)} style={{ maxWidth: 260 }}>
              <option value="">All models</option>
              {run.models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          )}
          <input className="input" type="search" placeholder="Case id or error type" aria-label="Search results"
            value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 280 }} />
        </div>
        {error && <Callout tone="error" role="alert">{error}</Callout>}
        {!page ? (
          <p className="muted" role="status">Loading results…</p>
        ) : page.items.length === 0 ? (
          <EmptyState icon={ListChecks}
            title={tab === "failed" ? "No failed requests" : tab === "quality" ? "No failed quality checks" : "No results yet"}>
            {tab === "failed"
              ? "Every recorded attempt succeeded."
              : tab === "quality"
                ? "Every applicable evaluator passed on the evaluated requests."
                : "Results appear here as requests complete."}
          </EmptyState>
        ) : (
          <>
            <div className="table-wrap">
              <table className="table" aria-label="Request results">
                <thead>
                  <tr>
                    <th scope="col" className="num">#</th>
                    {run.models.length > 1 && <th scope="col">Model</th>}
                    <th scope="col">Case</th>
                    <th scope="col">Attempt</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="num">Latency</th>
                    <th scope="col" className="num">TTFT</th>
                    <th scope="col" className="num">Out tokens</th>
                    {quality && <th scope="col">Checks</th>}
                    <th scope="col">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((r) => (
                    <tr key={r.id} className="clickable" onClick={() => void openResult(r)}>
                      <td className="num mono">{r.request_index}</td>
                      {run.models.length > 1 && <td>{modelName(r.run_model_id)}</td>}
                      <td>
                        <button type="button" className="link-btn" onClick={(e) => { e.stopPropagation(); void openResult(r); }}>
                          {r.case_identifier}
                        </button>
                        {r.is_warmup && <span className="badge badge-neutral" style={{ marginLeft: 6 }}>warm-up</span>}
                      </td>
                      <td className="small">
                        {r.attempt_number}
                        {r.attempt_number > 1 && <span className="muted"> (retry)</span>}
                        {!r.is_final && <span className="muted"> · retried</span>}
                      </td>
                      <td>
                        <span className={`badge badge-${r.status === "succeeded" ? "ok" : "error"}`}>{r.status}</span>
                      </td>
                      <td className="num">{fmtMs(r.latency_ms)}</td>
                      <td className={`num ${r.ttft_ms == null ? "na" : ""}`}>{fmtMs(r.ttft_ms)}</td>
                      <td className="num">{fmt(r.performance_metrics.output_tokens as number | null, 0)}</td>
                      {quality && <td><EvaluationChips evaluations={r.evaluations} /></td>}
                      <td className="small" style={{ color: "var(--danger)" }}>
                        {r.error_type ? `${r.error_type}${r.http_status ? ` (HTTP ${r.http_status})` : ""}` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="toolbar" style={{ justifyContent: "space-between", paddingTop: 12 }}>
              <span className="small muted">
                {offset + 1}–{offset + page.items.length} of {page.total}
              </span>
              <div className="page-actions">
                <button type="button" className="btn btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
                <button type="button" className="btn btn-sm" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
              </div>
            </div>
          </>
        )}
      </TabPanel>
      {open && (
        <Drawer title={`Request ${open.request_index} · ${open.case_identifier}`} onClose={() => setOpen(null)}>
          <ResultDetail result={open} modelName={modelName(open.run_model_id)} />
        </Drawer>
      )}
    </>
  );
}

function ResultDetail({ result: r, modelName }: { result: CaseResult | CaseResultDetail; modelName: string }) {
  const pm = r.performance_metrics;
  const rd = r.response_data;
  return (
    <div className="form">
      {r.error_type && (
        <Callout tone="error">
          <strong>{r.error_type}</strong>{r.http_status ? ` (HTTP ${r.http_status})` : ""}: {r.error_message}
          {r.request_metadata.retryable_error ? " · retryable" : ""}
        </Callout>
      )}
      <dl className="kv">
        <dt>Model</dt><dd>{modelName}</dd>
        <dt>Phase</dt><dd>{r.phase === "warmup" ? "Warm-up (excluded from metrics)" : "Measured"}</dd>
        <dt>Attempt</dt><dd>{r.attempt_number}{r.is_final ? " (final)" : " (retried)"} · pass {r.pass_number}</dd>
        {r.load_step != null && <><dt>Ramp step</dt><dd>{r.load_step + 1}</dd></>}
        <dt>Concurrency</dt><dd>{String(r.request_metadata.concurrency ?? "—")}</dd>
        <dt>Latency</dt><dd>{fmtMs(r.latency_ms)}</dd>
        <dt>TTFT</dt><dd>{r.ttft_ms != null ? fmtMs(r.ttft_ms) : pm.streamed ? "—" : "Not observable (non-streaming)"}</dd>
        <dt>Generation</dt><dd>{fmtMs(pm.generation_ms as number | null)}</dd>
        <dt>Inter-chunk latency</dt><dd>{fmtMs(pm.itl_mean_ms as number | null)}</dd>
        <dt>Tokens in / out</dt>
        <dd>{fmt(pm.input_tokens as number | null, 0)} / {fmt(pm.output_tokens as number | null, 0)} <span className="muted">({String(pm.token_count_source)})</span></dd>
        <dt>Output tokens/s</dt><dd>{fmt(pm.output_tokens_per_second as number | null, 1)}</dd>
        <dt>Finish reason</dt><dd>{rd.finish_reason ?? "—"}</dd>
        <dt>Model reported</dt><dd className="mono">{rd.model_reported ?? "—"}</dd>
        <dt>Started</dt><dd>{new Date(r.started_at).toLocaleString()}</dd>
      </dl>
      <div>
        <h3 style={{ margin: "8px 0" }}>Response</h3>
        {rd.stored === false ? (
          <p className="muted small">Response text is not stored (RUN_STORE_RESPONSES=false).</p>
        ) : rd.content ? (
          <pre className="code-block pre-wrap">{rd.content}{rd.content_truncated ? "\n… (truncated)" : ""}</pre>
        ) : rd.tool_calls && rd.tool_calls.length ? (
          <pre className="code-block pre-wrap">{JSON.stringify(rd.tool_calls, null, 2)}</pre>
        ) : (
          <p className="muted small">No content.</p>
        )}
      </div>
      {rd.simulation && <SimulationView simulation={rd.simulation} />}
      {r.evaluations.length > 0 && "id" in r.evaluations[0]! && (
        <EvaluationList evaluations={(r as CaseResultDetail).evaluations} />
      )}
      <details>
        <summary className="small">Request details</summary>
        <pre className="code-block pre-wrap">{JSON.stringify(r.request_metadata, null, 2)}</pre>
      </details>
    </div>
  );
}

function ConfigurationCard({ run }: { run: RunDetail }) {
  const snap = run.configuration_snapshot;
  const gen = snap.generation;
  const l = run.load;
  const endpoints = (snap.endpoints as { name: string; request_configuration: { request_timeout_seconds: number; max_retries: number } }[] | undefined) ?? [];
  return (
    <Card title="Configuration & methodology" icon={Settings2}>
      <dl className="kv">
        <dt>Datasets</dt>
        <dd>
          {run.datasets.map((d) => (
            <div key={d.content_hash + d.name}>
              {d.dataset_id && d.available ? <Link to={`/datasets/${d.dataset_id}`}>{d.name}</Link> : d.name} v{d.version_number}{" "}
              <span className="hash" title={d.content_hash}>{d.content_hash.slice(0, 12)}</span> · {d.record_count} cases
              {!d.available && <span className="badge badge-warn" style={{ marginLeft: 6 }}>deleted</span>}
            </div>
          ))}
        </dd>
        <dt>Load profile</dt><dd>{PROFILE_META[l.profile]?.label ?? l.profile}</dd>
        <dt>Measured requests</dt><dd>{l.requests != null ? `${l.requests} per model` : `as many as fit in ${l.duration_seconds}s`}</dd>
        <dt>Concurrency</dt><dd>{l.profile === "ramp" ? l.steps.map((s) => `${s.concurrency} (${s.count} req)`).join(" → ") : l.concurrency}</dd>
        <dt>Warm-up</dt><dd>{l.warmup_requests ? `${l.warmup_requests} per model, excluded from metrics` : "None"}</dd>
        <dt>Streaming</dt><dd>{l.stream ? "Yes (TTFT measured)" : "No (TTFT not observable)"}</dd>
        <dt>Timeout / retries</dt>
        <dd>
          {endpoints.map((e) => (
            <div key={e.name}>{e.name}: {e.request_configuration.request_timeout_seconds}s · {e.request_configuration.max_retries} retr{e.request_configuration.max_retries === 1 ? "y" : "ies"}</div>
          ))}
        </dd>
        <dt>Generation</dt>
        <dd>
          temperature {gen?.temperature ?? "endpoint default"} · max tokens {gen?.max_tokens ?? "endpoint default"}
          {gen?.top_p != null && ` · top_p ${gen.top_p}`}{gen?.seed != null && ` · seed ${gen.seed}`}
        </dd>
        <dt>Sampling</dt>
        <dd>
          {snap.sampling === "greedy" ? "Greedy (temperature 0); servers may still vary slightly" : snap.sampling === "stochastic" ? "Stochastic" : "Server default (unknown)"}
        </dd>
        {snap.profile && <><dt>Profile</dt><dd>{snap.profile.name} <span className="muted small">(settings copied at creation)</span></dd></>}
        {snap.evaluation && snap.evaluation.suites.length > 0 && (
          <>
            <dt>Evaluation</dt>
            <dd>
              {snap.evaluation.suites.map((x) => SUITE_TITLES[x] ?? x).join(", ")} ·{" "}
              {snap.evaluation.evaluators.length} evaluators
              {snap.evaluation.tool_execution && <div className="small">Tools: {snap.evaluation.tool_execution}</div>}
              {snap.evaluation.embedding_endpoint && (
                <div className="small">Embeddings: {snap.evaluation.embedding_endpoint.name} ({snap.evaluation.embedding_endpoint.model_identifier})</div>
              )}
              <div className="small muted">
                {snap.evaluation.evaluators.map((e) => `${e.name} v${e.version}`).join(", ")}
              </div>
            </dd>
          </>
        )}
        <dt>Execution</dt><dd className="small">{l.endpoint_order}. {l.case_order}.</dd>
        <dt>Versions</dt>
        <dd className="small">
          framework {run.framework_version} · metrics v{snap.metrics_version} · prompts v{snap.prompt_builder_version}
          {snap.evaluation?.framework_version && ` · evaluation v${snap.evaluation.framework_version}`}
        </dd>
        {run.pass_count > 1 && <><dt>Passes</dt><dd>{run.pass_count} (resumed or retried; throughput needs one contiguous pass)</dd></>}
      </dl>
      <details style={{ marginTop: 14 }}>
        <summary className="small">Full configuration snapshot (JSON)</summary>
        <pre className="code-block pre-wrap">{JSON.stringify(snap, null, 2)}</pre>
      </details>
    </Card>
  );
}

// ---------------------------------------------------------------------------------- page

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const [run, setRun] = useState<RunDetail>();
  const [loadError, setLoadError] = useState<string>();
  const [metrics, setMetrics] = useState<RunMetrics>();
  const [feed, setFeed] = useState<CaseEvent[]>([]);
  const [resultsVersion, setResultsVersion] = useState(0);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string }>();
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const loadMetrics = useCallback(() => {
    api.runs.metrics(runId).then(setMetrics).catch(() => undefined);
  }, [runId]);

  useEffect(() => {
    setRun(undefined);
    setFeed([]);
    api.runs.get(runId).then(setRun).catch((e: Error) => setLoadError(e.message));
    loadMetrics();
  }, [runId, loadMetrics]);

  const live = run ? !isTerminal(run.status) : false;
  useLiveRun(
    runId,
    live,
    (r) => setRun((prev) => (prev ? { ...prev, ...r, configuration_snapshot: prev.configuration_snapshot } : prev)),
    (e) => setFeed((prev) => [e, ...prev.filter((x) => x.id !== e.id)].slice(0, FEED_SIZE)),
    () => {
      api.runs.get(runId).then(setRun).catch(() => undefined);
      loadMetrics();
      setResultsVersion((v) => v + 1);
    },
  );

  // Live metrics and results while executing.
  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(() => {
      loadMetrics();
      setResultsVersion((v) => v + 1);
    }, LIVE_METRICS_MS);
    return () => window.clearInterval(t);
  }, [live, loadMetrics]);

  const get = useMemo(() => metricIndex(metrics?.items ?? []), [metrics]);

  async function act(fn: () => Promise<RunDetail>, ok: string) {
    setBusy(true);
    try {
      const updated = await fn();
      setRun(updated);
      setNotice({ tone: "ok", text: ok });
      setFeed([]);
      loadMetrics();
      setResultsVersion((v) => v + 1);
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await api.runs.remove(runId);
      navigate("/runs");
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof Error ? err.message : String(err) });
      setConfirmDelete(false);
      setBusy(false);
    }
  }

  if (loadError) {
    return (
      <div className="page">
        <PageHeader icon={Activity} title="Run" back={{ to: "/runs", label: "Runs" }} />
        <Callout tone="error" role="alert">Could not load the run: {loadError}</Callout>
      </div>
    );
  }
  if (!run) return <div className="page"><p className="muted" role="status">Loading run…</p></div>;

  const { done, planned, pct } = progressOf(run);
  const failed = run.progress?.failed ?? 0;
  const succeeded = run.progress?.succeeded ?? 0;
  const canCancel = isActive(run.status) || isPending(run.status);
  const canRetry = ["completed_with_errors", "cancelled", "failed"].includes(run.status);
  const isRamp = run.load.steps.length > 1;

  // Headline values: the best model per metric, named in the note.
  const best = (name: string, agg: string, lowerIsBetter: boolean) => {
    let pick: { value: number; model: string } | undefined;
    for (const m of run.models) {
      const v = get(m.id, name, agg)?.metric_value;
      if (v == null) continue;
      if (!pick || (lowerIsBetter ? v < pick.value : v > pick.value)) pick = { value: v, model: m.name };
    }
    return pick;
  };
  const ttft = best("ttft_ms", "p50", true);
  const thr = best("output_token_throughput", "value", false) ?? best("request_throughput", "value", false);
  const thrIsTokens = best("output_token_throughput", "value", false) !== undefined;
  const multi = run.models.length > 1;

  return (
    <div className="page">
      <PageHeader
        icon={Activity}
        back={{ to: "/runs", label: "Runs" }}
        title={<span className="title-row">{run.name} <RunStatusBadge status={run.status} /></span>}
        description={run.description ?? `Created ${formatRelative(run.created_at)} · ${run.models.length} model${multi ? "s" : ""} · ${run.datasets.map((d) => d.name).join(", ")}`}
        actions={
          <>
            {canCancel && (
              <button type="button" className="btn" disabled={busy || run.status === "cancel_requested"}
                onClick={() => void act(() => api.runs.cancel(runId), "Cancellation requested.")}>
                <Square size={16} /> {run.status === "cancel_requested" ? "Cancelling…" : "Cancel run"}
              </button>
            )}
            {canRetry && (
              <button type="button" className="btn" disabled={busy}
                onClick={() => void act(() => api.runs.retryFailed(runId), "Queued: failed and unfinished requests will be retried.")}>
                <RotateCcw size={16} /> Retry failed
              </button>
            )}
            {!canCancel && (
              <button type="button" className="btn btn-danger-ghost" disabled={busy} onClick={() => setConfirmDelete(true)}
                aria-label={`Delete ${run.name}`}>
                <Trash2 size={16} /> Delete
              </button>
            )}
          </>
        }
      />

      {notice && <Callout tone={notice.tone} role={notice.tone === "error" ? "alert" : "status"}>{notice.text}</Callout>}
      {isPending(run.status) && (
        <Callout tone="info">
          Queued: waiting for a worker to pick it up. If it stays here, check that the worker is running (Dashboard → System health).
        </Callout>
      )}
      {run.error_summary?.message && (
        <Callout tone={run.status === "failed" ? "error" : "warn"} role={run.status === "failed" ? "alert" : undefined}>
          <AlertTriangle size={16} aria-hidden="true" style={{ verticalAlign: "-3px" }} /> {run.error_summary.message}
          {run.error_summary.errors && Object.keys(run.error_summary.errors).length > 0 && (
            <> Errors by type: {Object.entries(run.error_summary.errors).map(([k, v]) => `${k} ${v}`).join(", ")}.</>
          )}
        </Callout>
      )}

      <div className="grid grid-4">
        <StatCard
          label="Requests"
          value={planned ? `${done}/${planned}` : done}
          icon={ListChecks}
          tone="violet"
          note={pct != null ? `${Math.round(pct)}% · ${fmtDuration(run.started_at, run.completed_at)}` : fmtDuration(run.started_at, run.completed_at)}
        />
        <StatCard
          label="Succeeded"
          value={succeeded}
          icon={failed ? AlertTriangle : CheckCircle2}
          tone={failed ? "orange" : "green"}
          note={failed ? `${failed} failed (${fmtPct(done ? failed / done : null)})` : "No failures"}
        />
        <StatCard
          label="TTFT p50"
          value={ttft ? fmtMs(ttft.value) : "—"}
          icon={Zap}
          tone="blue"
          note={ttft ? (multi ? `Best: ${ttft.model}` : "Median time to first token") : run.load.stream ? "Waiting for data" : "Not measured without streaming"}
        />
        <StatCard
          label={thrIsTokens ? "Output throughput" : "Request throughput"}
          value={thr ? fmt(thr.value, thrIsTokens ? 0 : 2) : "—"}
          unit={thr ? (thrIsTokens ? "tok/s" : "req/s") : undefined}
          icon={Gauge}
          tone="orange"
          note={thr ? (multi ? `Best: ${thr.model}` : `At concurrency ${run.load.concurrency}`) : "Computed from the measurement window"}
        />
      </div>

      <div className="grid grid-main">
        <Card title="Progress" icon={Clock} action={live && <span className="badge badge-info"><span className="dot pulse" aria-hidden="true" /> Live</span>}>
          <div className="progress-row" style={{ marginBottom: 18 }}>
            <ProgressBar run={run} />
            <div className="meta">
              <span>{planned ? `${done} of ${planned} measured requests` : `${done} measured requests`}</span>
              <span>{run.progress?.in_flight ? `${run.progress.in_flight} in flight` : ""}</span>
            </div>
          </div>
          <ModelProgressList run={run} />
        </Card>
        <Card title={live ? "Live requests" : "Run timeline"} icon={Activity}>
          {live ? (
            <LiveFeed events={feed} models={run.models} />
          ) : (
            <dl className="kv">
              <dt>Created</dt><dd>{new Date(run.created_at).toLocaleString()}</dd>
              <dt>Started</dt><dd>{run.started_at ? new Date(run.started_at).toLocaleString() : "—"}</dd>
              <dt>Finished</dt><dd>{run.completed_at ? new Date(run.completed_at).toLocaleString() : "—"}</dd>
              <dt>Duration</dt><dd>{fmtDuration(run.started_at, run.completed_at)}</dd>
              {run.cancel_requested_at && <><dt>Cancel requested</dt><dd>{new Date(run.cancel_requested_at).toLocaleString()}</dd></>}
            </dl>
          )}
        </Card>
      </div>

      <Card title="Performance metrics" icon={BarChart3}
        action={metrics?.live && <span className="small muted">Live: computed from results so far</span>}>
        {!metrics ? (
          <p className="muted" role="status">Loading metrics…</p>
        ) : (
          <>
            <MetricsTable run={run} get={get} label="Metrics by model" />
            <p className="small muted" style={{ margin: "10px 0 0" }}>
              Latency, TTFT, and token rates use successful measured requests; warm-up requests are excluded. Error rate counts a
              request as failed only after its last retry. Throughput covers the measurement window, including queueing at the
              configured concurrency. "—" means not measurable; hover for the reason.
            </p>
            {isRamp && run.models.map((m) => (
              <div key={m.id} style={{ marginTop: 18 }}>
                <h3 style={{ margin: "0 0 8px" }}>Ramp steps · {m.name}</h3>
                <RampTable run={run} get={get} model={m} />
              </div>
            ))}
          </>
        )}
      </Card>

      {metrics && <QualityCard run={run} get={get} />}
      {metrics && <ToolMetricsCard run={run} get={get} />}
      {metrics && <EvidenceMetricsCard run={run} get={get} />}

      <Card title="Results" icon={ListChecks}>
        <ResultsBrowser run={run} version={resultsVersion} />
      </Card>

      <ConfigurationCard run={run} />

      {confirmDelete && (
        <ConfirmDialog title={`Delete ${run.name}?`} confirmLabel="Delete run" busy={busy}
          onConfirm={() => void remove()} onCancel={() => setConfirmDelete(false)}>
          This permanently deletes the run, its {done} recorded requests, and its metrics.
        </ConfirmDialog>
      )}
    </div>
  );
}
