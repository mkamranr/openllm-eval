import { AlertTriangle, Download, GitCompare, Save, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Callout, Card, EmptyState, PageHeader, Switch } from "../../components/ui";
import { api } from "../../services/api";
import type {
  Comparability,
  ComparedMetric,
  ComparedValue,
  ComparisonResult,
  ComparisonSubject,
  SavedComparison,
  Severity,
} from "../../types/reports";
import type { Run } from "../../types/runs";
import { fmt, fmtMs, fmtPct, isTerminal } from "../runs/runMeta";
import { VariantsCard } from "./VariantsCard";

export const COMPARABILITY: Record<Comparability, { label: string; tone: string; text: string }> = {
  equivalent_configuration: {
    label: "Equivalent configuration",
    tone: "ok",
    text: "Datasets, versions, generation, and load settings match.",
  },
  partially_controlled: {
    label: "Partially controlled",
    tone: "warn",
    text: "Settings that affect results differ; read deltas with care.",
  },
  not_directly_comparable: {
    label: "Not directly comparable",
    tone: "error",
    text: "Datasets or evaluation methods differ, so the numbers measure different things.",
  },
};

const SEVERITY_TONE: Record<Severity, string> = { critical: "error", major: "warn", minor: "neutral", info: "neutral" };
const FAMILIES = ["all", "performance", "quality", "tool", "rag", "hallucination", "context", "resources"] as const;
type Family = (typeof FAMILIES)[number];
const COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];

export function formatValue(value: number | null, unit: string): string {
  if (value == null) return "—";
  if (unit === "ratio") return fmtPct(value);
  if (unit === "ms") return fmtMs(value);
  const digits = Number.isInteger(value) ? 0 : Math.abs(value) >= 100 ? 0 : 2;
  const suffix = ["count", "score", ""].includes(unit) ? "" : ` ${unit}`;
  return `${fmt(value, digits)}${suffix}`;
}

function formatDelta(v: ComparedValue, unit: string): string {
  if (v.delta == null || v.delta === 0) return "";
  const sign = v.delta > 0 ? "+" : "−";
  const abs = Math.abs(v.delta);
  const base = unit === "ratio" ? `${(abs * 100).toFixed(1)} pp` : formatValue(abs, unit);
  const rel = v.relative_change != null && unit !== "ratio" ? ` (${v.relative_change > 0 ? "+" : "−"}${Math.abs(v.relative_change * 100).toFixed(0)}%)` : "";
  return `${sign}${base}${rel}`;
}

function show(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function Key({ index }: { index: number }) {
  return <span className="legend-key" aria-hidden="true" style={{ background: index < COLORS.length ? COLORS[index] : "var(--muted)" }} />;
}

function RunPicker({ selected, onChange }: { selected: string[]; onChange: (ids: string[]) => void }) {
  const [runs, setRuns] = useState<Run[]>([]);
  useEffect(() => {
    api.runs.list({ limit: 200 }).then((p) => setRuns(p.items.filter((r) => isTerminal(r.status)))).catch(() => undefined);
  }, []);
  const byId = new Map(runs.map((r) => [r.id, r]));
  return (
    <div className="field">
      <label htmlFor="compare-add">Runs to compare</label>
      <div className="toolbar" style={{ flexWrap: "wrap", gap: 8, padding: 0 }}>
        {selected.map((id) => (
          <span key={id} className="chip">
            {byId.get(id)?.name ?? id.slice(0, 8)}
            <button type="button" aria-label={`Remove ${byId.get(id)?.name ?? id}`} onClick={() => onChange(selected.filter((x) => x !== id))}>
              <X size={12} />
            </button>
          </span>
        ))}
        <select id="compare-add" className="input" value="" style={{ maxWidth: 320 }} disabled={selected.length >= 10}
          onChange={(e) => e.target.value && onChange([...selected, e.target.value])}>
          <option value="">{selected.length ? "Add another run…" : "Add a finished run…"}</option>
          {runs.filter((r) => !selected.includes(r.id)).map((r) => (
            <option key={r.id} value={r.id}>
              {r.name} · {r.models.map((m) => m.name).join(", ")} · {new Date(r.created_at).toLocaleDateString()}
            </option>
          ))}
        </select>
      </div>
      <span className="hint">A run with several models compares its models; up to 10 runs.</span>
    </div>
  );
}

function DifferencesCard({ result }: { result: ComparisonResult }) {
  const [showInfo, setShowInfo] = useState(false);
  const { comparability: c, subjects } = result;
  const rows = c.differences.filter((d) => showInfo || d.severity !== "info");
  const infoCount = c.differences.filter((d) => d.severity === "info").length;
  return (
    <Card title="Comparability" icon={AlertTriangle}>
      <div className="toolbar" style={{ flexWrap: "wrap", gap: 12, padding: "0 0 12px" }}>
        {(["overall", "performance", "quality"] as const).map((aspect) => {
          const meta = COMPARABILITY[c[aspect]];
          return (
            <span key={aspect} className={`badge badge-${meta.tone}`} title={meta.text}>
              {aspect === "overall" ? "" : `${aspect[0]!.toUpperCase()}${aspect.slice(1)}: `}{meta.label}
            </span>
          );
        })}
      </div>
      <p className="small muted" style={{ marginTop: 0 }}>{COMPARABILITY[c.overall].text}</p>
      {rows.length > 0 ? (
        <div className="table-wrap">
          <table className="table" aria-label="Configuration differences">
            <thead>
              <tr>
                <th scope="col">Setting</th>
                <th scope="col">Severity</th>
                {subjects.map((s) => <th scope="col" key={s.id}><Key index={s.index} />{s.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.field}>
                  <td>{d.label}<div className="small muted">affects {d.aspect === "both" ? "performance and quality" : d.aspect === "subject" ? "nothing (expected)" : d.aspect}</div></td>
                  <td><span className={`badge badge-${SEVERITY_TONE[d.severity]}`}>{d.severity}</span></td>
                  {subjects.map((s) => (
                    <td key={s.id} className="small mono" style={{ wordBreak: "break-word" }}>{show(d.values[s.id])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="small" style={{ margin: 0 }}>No configuration differences{infoCount ? " besides the models themselves" : ""}.</p>
      )}
      {infoCount > 0 && (
        <div style={{ marginTop: 10 }}>
          <Switch label={`Show expected differences between models (${infoCount})`} checked={showInfo} onChange={setShowInfo} />
        </div>
      )}
    </Card>
  );
}

function MetricsCard({ result }: { result: ComparisonResult }) {
  const [family, setFamily] = useState<Family>("all");
  const [changedOnly, setChangedOnly] = useState(false);
  const families = new Set(result.metrics.map((m) => m.family));
  const rows = result.metrics.filter(
    (m) => (family === "all" || m.family === family) && (!changedOnly || m.values.some((v) => v.delta != null && v.delta !== 0)),
  );
  const base = result.baseline_subject_id;
  return (
    <Card title="Metrics" icon={GitCompare}>
      <div className="toolbar" style={{ flexWrap: "wrap", gap: 12, padding: "0 0 12px" }}>
        <div className="segmented" role="group" aria-label="Metric family">
          {FAMILIES.filter((f) => f === "all" || families.has(f)).map((f) => (
            <button key={f} type="button" aria-pressed={family === f} onClick={() => setFamily(f)}>
              {f === "all" ? "All" : f[0]!.toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
        <Switch label="Only metrics that changed" checked={changedOnly} onChange={setChangedOnly} />
      </div>
      <div className="table-wrap">
        <table className="table" aria-label="Compared metrics">
          <thead>
            <tr>
              <th scope="col">Metric</th>
              {result.subjects.map((s) => (
                <th scope="col" key={s.id} className="num">
                  <Key index={s.index} />{s.label}{s.id === base && <div className="small muted">baseline</div>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => <MetricRow key={`${m.metric_name}.${m.aggregation}`} m={m} />)}
          </tbody>
        </table>
      </div>
      <p className="small muted" style={{ margin: "10px 0 0" }}>
        Deltas are against the baseline; "better"/"worse" follow each metric's direction. No significance test is applied.
        "—" means not measured; hover for the reason.
      </p>
    </Card>
  );
}

function MetricRow({ m }: { m: ComparedMetric }) {
  return (
    <tr>
      <td>
        <span className="mono">{m.metric_name}</span> <span className="small muted">{m.aggregation}</span>
        {m.better && <div className="small muted">{m.better} is better</div>}
      </td>
      {m.values.map((v) => {
        const delta = formatDelta(v, m.unit);
        return (
          <td key={v.subject_id} className="num" title={v.value == null ? v.unavailable_reason ?? "unavailable" : `n=${v.sample_count ?? "?"}`}>
            {formatValue(v.value, m.unit)}
            {delta && (
              <div className="small" style={{ color: v.improved == null ? "var(--muted)" : v.improved ? "var(--success)" : "var(--danger)" }}>
                {delta}{v.improved != null && (v.improved ? " · better" : " · worse")}
              </div>
            )}
          </td>
        );
      })}
    </tr>
  );
}

function TransitionsCard({ result }: { result: ComparisonResult }) {
  if (result.transitions.length === 0) return null;
  const label = (id: string) => result.subjects.find((s) => s.id === id)?.label ?? id;
  return (
    <Card title="Per-case changes against the baseline" icon={AlertTriangle}>
      <div className="grid" style={{ gap: 16 }}>
        {result.transitions.map((t) => (
          <section key={t.subject_id} aria-label={`Changes: ${label(t.subject_id)}`}>
            <h3 style={{ margin: "0 0 6px" }}>{label(t.subject_id)}</h3>
            <p className="small muted" style={{ margin: "0 0 8px" }}>
              {t.shared_cases} shared cases · datasets {t.datasets_identical ? "identical" : "differ"}
              {t.only_in_subject + t.only_in_baseline > 0 && ` · ${t.only_in_baseline} only in baseline, ${t.only_in_subject} only here`}
            </p>
            <ul className="plain-list small">
              <li>
                <strong>{t.new_errors_total}</strong> new request errors
                {t.new_errors.length > 0 && <span className="muted mono"> ({t.new_errors.slice(0, 10).map((e) => e.case_identifier).join(", ")}{t.new_errors_total > 10 ? ", …" : ""})</span>}
                , <strong>{t.fixed_errors_total}</strong> fixed
              </li>
              {Object.entries(t.evaluators).map(([ev, x]) => (
                <li key={ev}>
                  <span className="mono">{ev}</span>: <strong style={{ color: x.pass_to_fail_total ? "var(--danger)" : undefined }}>{x.pass_to_fail_total}</strong> pass→fail
                  {x.pass_to_fail.length > 0 && <span className="muted mono"> ({x.pass_to_fail.slice(0, 10).join(", ")}{x.pass_to_fail_total > 10 ? ", …" : ""})</span>}
                  , <strong style={{ color: x.fail_to_pass_total ? "var(--success)" : undefined }}>{x.fail_to_pass_total}</strong> fail→pass
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Card>
  );
}

function SavedList({ items, onOpen, onDelete }: { items: SavedComparison[]; onOpen: (c: SavedComparison) => void; onDelete: (id: string) => void }) {
  if (items.length === 0) return null;
  return (
    <Card title="Saved comparisons" icon={Save}>
      <ul className="plain-list">
        {items.map((c) => (
          <li key={c.id} className="toolbar" style={{ justifyContent: "space-between", padding: "4px 0" }}>
            <button type="button" className="link-btn" onClick={() => onOpen(c)}>{c.name}</button>
            <span className="small muted">{c.run_ids.length} run{c.run_ids.length > 1 ? "s" : ""} · {new Date(c.created_at).toLocaleDateString()}</span>
            <button type="button" className="icon-btn" aria-label={`Delete comparison ${c.name}`} onClick={() => onDelete(c.id)}>
              <Trash2 size={16} />
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function ComparePage() {
  const [params, setParams] = useSearchParams();
  const runIds = useMemo(() => (params.get("runs") ?? "").split(",").filter(Boolean), [params]);
  const baseline = params.get("baseline") ?? undefined;
  const [result, setResult] = useState<ComparisonResult>();
  const [error, setError] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [saved, setSaved] = useState<SavedComparison[]>([]);
  const [name, setName] = useState("");
  const [notice, setNotice] = useState<string>();

  const loadSaved = useCallback(() => {
    api.comparisons.list().then((p) => setSaved(p.items)).catch(() => undefined);
  }, []);
  useEffect(loadSaved, [loadSaved]);

  useEffect(() => {
    if (runIds.length === 0) {
      setResult(undefined);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .compare(runIds, baseline)
      .then((r) => !cancelled && (setResult(r), setError(undefined)))
      .catch((e: Error) => !cancelled && (setError(e.message), setResult(undefined)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [runIds, baseline]);

  const update = (ids: string[], base?: string) => {
    const next = new URLSearchParams();
    if (ids.length) next.set("runs", ids.join(","));
    if (base && ids.length) next.set("baseline", base);
    setParams(next);
    setNotice(undefined);
  };

  async function save() {
    try {
      await api.comparisons.create({ name: name.trim(), run_ids: runIds, baseline: baseline ?? null });
      setName("");
      setNotice("Comparison saved.");
      loadSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function removeSaved(id: string) {
    try {
      await api.comparisons.remove(id);
      setSaved((prev) => prev.filter((c) => c.id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const subjects: ComparisonSubject[] = result?.subjects ?? [];
  return (
    <div className="page">
      <PageHeader icon={GitCompare} title="Compare Runs"
        description="Side-by-side metrics against a baseline, with every configuration difference flagged." />

      <Card>
        <div className="toolbar" style={{ flexWrap: "wrap", gap: 16, alignItems: "flex-start", padding: 0 }}>
          <div style={{ flex: "1 1 420px" }}>
            <RunPicker selected={runIds} onChange={(ids) => update(ids, ids.length ? baseline : undefined)} />
          </div>
          {subjects.length > 1 && (
            <div className="field" style={{ minWidth: 240 }}>
              <label htmlFor="compare-baseline">Baseline</label>
              <select id="compare-baseline" className="input" value={result?.baseline_subject_id ?? ""}
                onChange={(e) => update(runIds, e.target.value)}>
                {subjects.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
            </div>
          )}
        </div>
      </Card>

      {error && <Callout tone="error" role="alert">{error}</Callout>}
      {notice && <Callout tone="ok" role="status">{notice}</Callout>}

      {runIds.length === 0 ? (
        <EmptyState icon={GitCompare} title="Pick runs to compare">
          Add two or more finished runs, or one run with several models. You can also start from a run's page with "Compare".
        </EmptyState>
      ) : loading && !result ? (
        <p className="muted" role="status">Comparing…</p>
      ) : result ? (
        <>
          {subjects.length < 2 && (
            <Callout tone="info">Only one model selected: add another run to see differences.</Callout>
          )}
          <DifferencesCard result={result} />
          <VariantsCard result={result} formatValue={formatValue} />
          <MetricsCard result={result} />
          <TransitionsCard result={result} />
          <Card title="Export and save" icon={Download}>
            <div className="toolbar" style={{ flexWrap: "wrap", gap: 12, padding: 0 }}>
              {(["html", "csv", "json"] as const).map((f) => (
                <a key={f} className="btn btn-sm" href={api.compareExportUrl(runIds, f, baseline)} download>
                  <Download size={14} /> {f.toUpperCase()}
                </a>
              ))}
              <span style={{ flex: 1 }} />
              <input className="input" placeholder="Comparison name" aria-label="Comparison name" value={name}
                onChange={(e) => setName(e.target.value)} style={{ maxWidth: 260 }} />
              <button type="button" className="btn btn-primary btn-sm" disabled={!name.trim()} onClick={() => void save()}>
                <Save size={14} /> Save
              </button>
            </div>
            <ul className="plain-list small muted" style={{ marginTop: 10 }}>
              {result.notes.map((n) => <li key={n}>{n}</li>)}
            </ul>
            <p className="small" style={{ margin: "6px 0 0" }}>
              Runs: {[...new Map(subjects.map((s) => [s.run_id, s.run_name])).entries()].map(([id, runName], i) => (
                <span key={id}>{i > 0 && " · "}<Link to={`/runs/${id}`}>{runName}</Link></span>
              ))}
            </p>
          </Card>
        </>
      ) : null}

      <SavedList items={saved} onDelete={(id) => void removeSaved(id)}
        onOpen={(c) => update(c.run_ids, c.baseline_run_id ?? undefined)} />
    </div>
  );
}
