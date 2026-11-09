import {
  ArrowLeft,
  ArrowRight,
  Check,
  Database,
  Download,
  Info,
  PlayCircle,
  Plus,
  RotateCcw,
  Save,
  Server,
  X,
} from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { EndpointStatusBadge } from "../../components/status/StatusBadge";
import { Callout, Card, EmptyState, PageHeader, TabPanel, Tabs } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { usePersistentState } from "../../hooks/usePersistentState";
import { ApiError, api } from "../../services/api";
import type { Dataset, Endpoint, SampleDataset } from "../../types/api";
import { DATASET_TYPE_META, typeLabel } from "../datasets/datasetMeta";
import { EVALUATION_TYPES, IMPLEMENTED_TYPES, evaluationType } from "./evaluationTypes";
import {
  DEFAULT_PERFORMANCE,
  PerformanceSettingsForm,
  describeLoad,
  fromRunConfig,
  toRunConfig,
  validatePerformance,
  type PerformanceSettings,
} from "./performanceSettings";
import {
  DEFAULT_QUALITY,
  QUALITY_TYPES,
  QualitySettingsForm,
  fromEvaluationConfig,
  toEvaluationConfig,
  validateQuality,
  type QualitySettings,
} from "./qualitySettings";

export const DRAFT_KEY = "openllm-eval.new-evaluation.v1";

export interface EvaluationDraft {
  step: number;
  name: string;
  description: string;
  projectId: string;
  endpointIds: string[];
  evaluationTypes: string[];
  datasetIds: string[];
  performance: PerformanceSettings;
  quality: QualitySettings;
  profileId: string;
}

export const EMPTY_DRAFT: EvaluationDraft = {
  step: 0,
  name: "",
  description: "",
  projectId: "",
  endpointIds: [],
  evaluationTypes: [],
  datasetIds: [],
  performance: DEFAULT_PERFORMANCE,
  quality: DEFAULT_QUALITY,
  profileId: "",
};

/** Drafts saved by older versions lack newer fields; fill them from the defaults. */
export function normalizeDraft(d: Partial<EvaluationDraft> | null | undefined): EvaluationDraft {
  return {
    ...EMPTY_DRAFT,
    ...d,
    performance: { ...DEFAULT_PERFORMANCE, ...(d?.performance ?? {}) },
    quality: { ...DEFAULT_QUALITY, ...(d?.quality ?? {}) },
  };
}

const STEPS = ["Basic Setup", "Evaluation Types", "Datasets", "Review & Run"];

const toggle = (list: string[], value: string) =>
  list.includes(value) ? list.filter((v) => v !== value) : [...list, value];

function Stepper({ step, maxReachable, onGo }: { step: number; maxReachable: number; onGo: (s: number) => void }) {
  return (
    <ol className="stepper" aria-label="Progress">
      {STEPS.map((label, i) => (
        <li key={label} className={i < step ? "done" : i === step ? "current" : ""}>
          <button type="button" onClick={() => onGo(i)} disabled={i > maxReachable}
            aria-current={i === step ? "step" : undefined}>
            <span className="step-dot">{i < step ? <Check size={16} /> : i + 1}</span>
            <span className="label">{label}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

function StepTitle({ n, children }: { n: number; children: string }) {
  return (
    <h2 className="step-title">
      <span className="num" aria-hidden="true">{n}</span>
      {children}
    </h2>
  );
}

// ------------------------------------------------------------------ step 1

function BasicSetup({
  draft,
  update,
  endpoints,
}: {
  draft: EvaluationDraft;
  update: (patch: Partial<EvaluationDraft>) => void;
  endpoints: Endpoint[];
}) {
  const projects = useAsync(() => api.projects.list(), []);
  const [adding, setAdding] = useState("");
  const selected = draft.endpointIds
    .map((id) => endpoints.find((e) => e.id === id))
    .filter((e): e is Endpoint => Boolean(e));
  const available = endpoints.filter((e) => e.enabled && !draft.endpointIds.includes(e.id));

  return (
    <div className="form">
      <StepTitle n={1}>Basic Setup</StepTitle>
      <div className="field">
        <span className="label" id="models-label">Select models *</span>
        {selected.length > 0 ? (
          <div className="chips" aria-labelledby="models-label">
            {selected.map((e) => (
              <span className="chip" key={e.id}>
                <Server size={14} aria-hidden="true" /> {e.name}
                <span className="small" style={{ fontWeight: 500, opacity: 0.8 }}>{e.model_identifier}</span>
                <button type="button" aria-label={`Remove ${e.name}`}
                  onClick={() => update({ endpointIds: draft.endpointIds.filter((id) => id !== e.id) })}>
                  <X size={14} />
                </button>
              </span>
            ))}
          </div>
        ) : (
          <span className="hint">Pick one or more registered endpoints to evaluate side by side.</span>
        )}
        {endpoints.length === 0 ? (
          <Callout tone="info">
            No endpoints registered yet. <Link to="/endpoints?new=1">Add a model endpoint</Link> first.
          </Callout>
        ) : (
          <div className="page-actions" style={{ marginTop: 4 }}>
            <select className="input" aria-label="Add model" value={adding} onChange={(e) => setAdding(e.target.value)}
              style={{ maxWidth: 380 }} disabled={available.length === 0}>
              <option value="">{available.length ? "Choose an endpoint…" : "All enabled endpoints added"}</option>
              {available.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name} ({e.model_identifier}){e.last_test_status === "failed" ? " · last test failed" : ""}
                </option>
              ))}
            </select>
            <button type="button" className="btn" disabled={!adding}
              onClick={() => { update({ endpointIds: [...draft.endpointIds, adding] }); setAdding(""); }}>
              <Plus size={16} /> Add model
            </button>
          </div>
        )}
      </div>
      <div className="form-row">
        <div className="field">
          <label htmlFor="ev-name">Evaluation name *</label>
          <input id="ev-name" className="input" value={draft.name} placeholder="Production model benchmark"
            onChange={(e) => update({ name: e.target.value })} />
        </div>
        <div className="field">
          <label htmlFor="ev-project">Project</label>
          <select id="ev-project" className="input" value={draft.projectId}
            onChange={(e) => update({ projectId: e.target.value })}>
            <option value="">No project</option>
            {(projects.data?.items ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      </div>
      <div className="field">
        <label htmlFor="ev-description">Description (optional)</label>
        <textarea id="ev-description" className="input" rows={3} value={draft.description}
          placeholder="Compare performance across models for production use."
          onChange={(e) => update({ description: e.target.value })} />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ step 2

function TypesStep({
  draft,
  update,
  endpoints,
}: {
  draft: EvaluationDraft;
  update: (p: Partial<EvaluationDraft>) => void;
  endpoints: Endpoint[];
}) {
  const runnable = draft.evaluationTypes.some((t) => IMPLEMENTED_TYPES.includes(t));
  const qualityTypes = draft.evaluationTypes.filter((t) => QUALITY_TYPES.includes(t));
  const profiles = useAsync(() => api.profiles.list(), []);
  const list = profiles.data?.items ?? [];

  function applyProfile(id: string) {
    const p = list.find((x) => x.id === id);
    if (!p) {
      update({ profileId: "" });
      return;
    }
    update({
      profileId: p.id,
      evaluationTypes: [...new Set([...p.suites, ...draft.evaluationTypes.filter((t) => !IMPLEMENTED_TYPES.includes(t))])],
      performance: fromRunConfig(p.load, p.generation),
      quality: fromEvaluationConfig(p.evaluation),
    });
  }

  return (
    <div className="form">
      <StepTitle n={2}>Select Evaluation Types</StepTitle>
      {list.length > 0 && (
        <div className="field" style={{ maxWidth: 420 }}>
          <label htmlFor="ev-profile">Start from a profile</label>
          <select id="ev-profile" className="input" value={draft.profileId} onChange={(e) => applyProfile(e.target.value)}>
            <option value="">No profile</option>
            {list.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <span className="hint">Fills the types and settings below; you can still change them.</span>
        </div>
      )}
      <div className="select-grid" role="group" aria-label="Evaluation types">
        {EVALUATION_TYPES.map((t) => {
          const on = draft.evaluationTypes.includes(t.key);
          return (
            <button key={t.key} type="button" className="select-card" aria-pressed={on}
              onClick={() => update({ evaluationTypes: toggle(draft.evaluationTypes, t.key) })}>
              <span className="sc-check" aria-hidden="true"><Check size={14} /></span>
              <span className="sc-icon" aria-hidden="true"><t.icon size={18} /></span>
              <span className="sc-title">{t.title}</span>
              <span className="sc-desc">{t.description}</span>
              <span className="sc-meta">
                {IMPLEMENTED_TYPES.includes(t.key)
                  ? <span className="badge badge-ok">Available</span>
                  : <span className="badge badge-neutral">Milestone {t.milestone}</span>}
              </span>
            </button>
          );
        })}
      </div>
      <p className="small muted" style={{ margin: 0 }}>
        Types marked Available run today; performance metrics are recorded on every run. Other badges show the milestone
        that implements them; you can select them now and they are kept in your draft.
      </p>
      {qualityTypes.length > 0 && (
        <QualitySettingsForm value={draft.quality} types={qualityTypes} endpoints={endpoints}
          onChange={(patch) => update({ quality: { ...draft.quality, ...patch } })} />
      )}
      {runnable && (
        <PerformanceSettingsForm
          value={draft.performance}
          errors={validatePerformance(draft.performance)}
          onChange={(patch) => update({ performance: { ...draft.performance, ...patch } })}
        />
      )}
    </div>
  );
}

// ------------------------------------------------------------------ step 3

function DatasetsStep({
  draft,
  update,
  datasets,
  samples,
  onInstalled,
}: {
  draft: EvaluationDraft;
  update: (p: Partial<EvaluationDraft>) => void;
  datasets: Dataset[];
  samples: SampleDataset[];
  onInstalled: (dataset: Dataset) => void;
}) {
  const [tab, setTab] = useState<"builtin" | "mine" | "upload">("builtin");
  const [error, setError] = useState<string>();
  const [installing, setInstalling] = useState<string | null>(null);
  const wanted = new Set(
    draft.evaluationTypes.flatMap((k) => evaluationType(k)?.datasetTypes ?? []),
  );
  const suggested = (type: string) => wanted.has(type as never);
  const userDatasets = datasets.filter((d) => d.source !== "sample");

  async function install(s: SampleDataset) {
    setInstalling(s.sample_id);
    setError(undefined);
    try {
      const d = await api.datasets.installSample(s.sample_id);
      onInstalled(d);
      update({ datasetIds: [...draft.datasetIds, d.id] });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling(null);
    }
  }

  const datasetCard = (d: Dataset) => {
    const on = draft.datasetIds.includes(d.id);
    const Icon = DATASET_TYPE_META[d.dataset_type]?.icon ?? Database;
    return (
      <button key={d.id} type="button" className="select-card" aria-pressed={on} disabled={!d.current_version}
        onClick={() => update({ datasetIds: toggle(draft.datasetIds, d.id) })}>
        <span className="sc-check" aria-hidden="true"><Check size={14} /></span>
        <span className="sc-icon" aria-hidden="true"><Icon size={18} /></span>
        <span className="sc-title">{d.name.replace(/^Sample: /, "")}</span>
        <span className="sc-desc">{typeLabel(d.dataset_type)}</span>
        <span className="sc-meta">
          {d.current_version ? `${d.current_version.record_count} cases · v${d.current_version.version_number}` : "no versions"}
          {suggested(d.dataset_type) && <span className="badge badge-violet">Suggested</span>}
        </span>
      </button>
    );
  };

  return (
    <div className="form">
      <StepTitle n={3}>Select Datasets</StepTitle>
      <Tabs label="Dataset sources" value={tab} onChange={setTab} items={[
        { key: "builtin", label: "Built-in datasets", count: samples.length },
        { key: "mine", label: "My datasets", count: userDatasets.length },
        { key: "upload", label: "Upload files" },
      ]} />
      {error && <Callout tone="error" role="alert">{error}</Callout>}
      {tab === "builtin" && (
        <TabPanel id="builtin">
          <div className="select-grid">
            {samples.map((s) => {
              const installed = s.installed_dataset_id ? datasets.find((d) => d.id === s.installed_dataset_id) : undefined;
              if (installed) return datasetCard(installed);
              const Icon = DATASET_TYPE_META[s.dataset_type]?.icon ?? Database;
              return (
                <div key={s.sample_id} className="select-card" style={{ cursor: "default" }}>
                  <span className="sc-icon" aria-hidden="true"><Icon size={18} /></span>
                  <span className="sc-title">{s.name.replace(/^Sample: /, "")}</span>
                  <span className="sc-desc">{typeLabel(s.dataset_type)} · {s.record_count} cases</span>
                  <span className="sc-meta">
                    {s.license}
                    {suggested(s.dataset_type) && <span className="badge badge-violet">Suggested</span>}
                  </span>
                  <button type="button" className="btn btn-sm" disabled={installing !== null}
                    onClick={() => void install(s)} aria-label={`Install and select ${s.name}`}>
                    <Download size={14} /> {installing === s.sample_id ? "Installing…" : "Install & select"}
                  </button>
                </div>
              );
            })}
          </div>
        </TabPanel>
      )}
      {tab === "mine" && (
        <TabPanel id="mine">
          {userDatasets.length ? (
            <div className="select-grid">{userDatasets.map(datasetCard)}</div>
          ) : (
            <EmptyState icon={Database} title="No imported datasets">Use the Upload files tab to import JSONL or CSV.</EmptyState>
          )}
        </TabPanel>
      )}
      {tab === "upload" && (
        <TabPanel id="upload">
          <Callout tone="info">
            Import JSONL or CSV on the Datasets page, where you can validate it first. Your evaluation draft is saved and
            will be here when you come back.
          </Callout>
          <div style={{ marginTop: 12 }}>
            <Link to="/datasets?tab=import" className="btn btn-primary">Go to dataset import</Link>
          </div>
        </TabPanel>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ step 4

/** Save the current types and settings as a reusable evaluation profile. */
function SaveProfile({ draft }: { draft: EvaluationDraft }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const suites = draft.evaluationTypes.filter((t) => IMPLEMENTED_TYPES.includes(t));
  if (!suites.length) return null;

  async function save() {
    setBusy(true);
    setMessage(null);
    try {
      const p = await api.profiles.create({
        name: name.trim(),
        project_id: draft.projectId || null,
        suites,
        evaluation: toEvaluationConfig(draft.quality),
        ...toRunConfig(draft.performance),
      });
      setMessage({ ok: true, text: `Saved profile "${p.name}".` });
      setName("");
    } catch (e) {
      setMessage({ ok: false, text: e instanceof ApiError ? e.message : "Could not save the profile." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-label="Save as profile" style={{ boxShadow: "none" }}>
      <div className="form-row" style={{ alignItems: "flex-end" }}>
        <div className="field" style={{ flex: 1 }}>
          <label htmlFor="profile-name">Save these settings as a profile</label>
          <input id="profile-name" className="input" value={name} maxLength={200} placeholder="e.g. Nightly quality check"
            onChange={(e) => setName(e.target.value)} />
        </div>
        <button type="button" className="btn" disabled={busy || !name.trim()} onClick={() => void save()}>
          <Save size={16} aria-hidden="true" /> Save as profile
        </button>
      </div>
      {message && (
        <p role="status" className="small" style={{ margin: "8px 0 0", color: message.ok ? undefined : "var(--danger)" }}>
          {message.text}
        </p>
      )}
    </section>
  );
}

function ReviewStep({
  draft,
  endpoints,
  datasets,
}: {
  draft: EvaluationDraft;
  endpoints: Endpoint[];
  datasets: Dataset[];
}) {
  const models = draft.endpointIds.map((id) => endpoints.find((e) => e.id === id)).filter(Boolean) as Endpoint[];
  const chosen = draft.datasetIds.map((id) => datasets.find((d) => d.id === id)).filter(Boolean) as Dataset[];
  const cases = chosen.reduce((sum, d) => sum + (d.current_version?.record_count ?? 0), 0);
  const notHealthy = models.filter((m) => m.last_test_status !== "ok");
  const runnable = draft.evaluationTypes.filter((k) => IMPLEMENTED_TYPES.includes(k));
  const perf = runnable.length > 0;
  const quality = runnable.filter((k) => QUALITY_TYPES.includes(k));
  const later = draft.evaluationTypes.filter((k) => !IMPLEMENTED_TYPES.includes(k));
  const embedding = endpoints.find((e) => e.id === draft.quality.embeddingEndpointId);
  const p = draft.performance;
  return (
    <div className="form">
      <StepTitle n={4}>Review & Run</StepTitle>
      <div className="grid grid-main" style={{ gap: 16 }}>
        <section className="card" aria-label="Evaluation summary" style={{ boxShadow: "none" }}>
          <h3 style={{ marginTop: 0 }}>Evaluation summary</h3>
          <dl className="summary-list">
            <div><dt>Name</dt><dd>{draft.name || "—"}</dd></div>
            <div><dt>Models</dt><dd>{models.length} model{models.length === 1 ? "" : "s"}</dd></div>
            <div><dt>Evaluation types</dt><dd>{draft.evaluationTypes.map((k) => evaluationType(k)?.title ?? k).join(", ") || "—"}</dd></div>
            <div><dt>Datasets</dt><dd>{chosen.length} dataset{chosen.length === 1 ? "" : "s"} · {cases} cases</dd></div>
            {perf && (
              <>
                <div><dt>Load</dt><dd>{describeLoad(p, cases)}</dd></div>
                <div><dt>Warm-up</dt><dd>{Number(p.warmup) ? `${p.warmup} per model (excluded from metrics)` : "None"}</dd></div>
                <div><dt>Streaming</dt><dd>{p.stream ? "On (TTFT measured)" : "Off (TTFT not measured)"}</dd></div>
                <div>
                  <dt>Generation</dt>
                  <dd>
                    temperature {p.temperature.trim() || "endpoint default"} · max tokens {p.maxTokens.trim() || "endpoint default"}
                  </dd>
                </div>
              </>
            )}
            {quality.length > 0 && (
              <div>
                <dt>Quality</dt>
                <dd>
                  {quality.map((k) => evaluationType(k)?.title ?? k).join(", ")}
                  {quality.includes("tool_calling") &&
                    ` · tools ${draft.quality.toolMode === "simulated" ? "simulated (fixtures)" : "inspected only"}`}
                  {embedding && ` · embeddings via ${embedding.name}`}
                </dd>
              </div>
            )}
            {models.length > 1 && <div><dt>Order</dt><dd>Models run one after another, so they don't compete for the server.</dd></div>}
          </dl>
        </section>
        <section className="card" aria-label="Selected models" style={{ boxShadow: "none" }}>
          <h3 style={{ marginTop: 0 }}>Selected models</h3>
          <div className="health-list">
            {models.map((m) => (
              <div className="health-row" key={m.id}>
                <span className="name"><Server size={16} aria-hidden="true" /> {m.name}</span>
                <EndpointStatusBadge status={m.last_test_status} />
              </div>
            ))}
          </div>
        </section>
      </div>
      {chosen.length > 0 && (
        <div className="small muted">
          Datasets: {chosen.map((d) => `${d.name} v${d.current_version?.version_number} (${d.current_version?.content_hash.slice(0, 8)})`).join(" · ")}
        </div>
      )}
      {notHealthy.length > 0 && (
        <Callout tone="warn">
          {notHealthy.map((m) => m.name).join(", ")} {notHealthy.length === 1 ? "has" : "have"} not passed a connection
          test. <Link to="/endpoints">Test endpoints</Link> before running.
        </Callout>
      )}
      {!perf ? (
        <Callout tone="warn">
          None of the selected evaluation types can run in this release yet. Go back to Evaluation Types and select one marked
          Available; your other selections stay in the draft.
        </Callout>
      ) : later.length > 0 ? (
        <Callout tone="info">
          {later.map((k) => evaluationType(k)?.title ?? k).join(", ")} {later.length === 1 ? "arrives" : "arrive"} in a later
          milestone and {later.length === 1 ? "is" : "are"} not part of this run.
        </Callout>
      ) : null}
      <SaveProfile draft={draft} />
    </div>
  );
}

// ------------------------------------------------------------------ wizard

export function NewEvaluationWizard() {
  const navigate = useNavigate();
  const [stored, setDraft] = usePersistentState<EvaluationDraft>(DRAFT_KEY, EMPTY_DRAFT);
  const draft = normalizeDraft(stored);
  const endpoints = useAsync(() => api.endpoints.list(), []);
  const datasets = useAsync(() => api.datasets.list(), []);
  const samples = useAsync(() => api.datasets.samples(), []);
  const update = (patch: Partial<EvaluationDraft>) => setDraft((d) => ({ ...normalizeDraft(d), ...patch }));
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string>();

  const eps = endpoints.data?.items ?? [];
  const ds = datasets.data?.items ?? [];
  // Drop selections whose endpoint/dataset was deleted since the draft was saved.
  const liveEndpointIds = endpoints.data ? draft.endpointIds.filter((id) => eps.some((e) => e.id === id)) : draft.endpointIds;
  const liveDatasetIds = datasets.data ? draft.datasetIds.filter((id) => ds.some((d) => d.id === id)) : draft.datasetIds;
  const current = { ...draft, endpointIds: liveEndpointIds, datasetIds: liveDatasetIds };

  const suites = current.evaluationTypes.filter((t) => IMPLEMENTED_TYPES.includes(t));
  const perfSelected = suites.length > 0;
  const perfValid =
    !perfSelected ||
    (Object.keys(validatePerformance(current.performance)).length === 0 &&
      Object.keys(validateQuality(current.quality)).length === 0);
  const complete = [
    current.name.trim().length > 0 && current.endpointIds.length > 0,
    current.evaluationTypes.length > 0 && perfValid,
    current.datasetIds.length > 0,
  ];
  const missing = [
    "Add at least one model and an evaluation name.",
    current.evaluationTypes.length ? "Fix the highlighted performance settings." : "Select at least one evaluation type.",
    "Select at least one dataset.",
  ];
  const maxReachable = complete.findIndex((ok) => !ok) === -1 ? 3 : complete.findIndex((ok) => !ok);
  const step = Math.min(draft.step, maxReachable);
  const go = (s: number) => update({ step: Math.max(0, Math.min(s, maxReachable)) });
  const canStart = complete.every(Boolean) && perfSelected && !starting;

  async function start() {
    setStarting(true);
    setStartError(undefined);
    try {
      const run = await api.runs.create({
        name: current.name.trim(),
        description: current.description.trim() || null,
        project_id: current.projectId || null,
        endpoint_ids: current.endpointIds,
        datasets: current.datasetIds.map((dataset_id) => ({ dataset_id })),
        suites,
        ...toRunConfig(current.performance),
        evaluation: toEvaluationConfig(current.quality),
        profile_id: current.profileId || null,
      });
      // Keep the settings for the next run; clear what identifies this one.
      setDraft({ ...EMPTY_DRAFT, performance: current.performance, quality: current.quality, profileId: current.profileId });
      navigate(`/runs/${run.id}`);
    } catch (err) {
      const fields = err instanceof ApiError ? Object.values(err.fieldErrors) : [];
      setStartError(fields.length ? fields.join(" ") : err instanceof Error ? err.message : String(err));
      setStarting(false);
    }
  }

  return (
    <div className="page">
      <PageHeader
        icon={PlayCircle}
        title="New Evaluation"
        description="Configure an evaluation of one or more models. Progress is saved as you go."
        actions={
          <button type="button" className="btn" onClick={() => setDraft(EMPTY_DRAFT)}>
            <RotateCcw size={16} /> Start over
          </button>
        }
      />
      <Card>
        <Stepper step={step} maxReachable={maxReachable} onGo={go} />
      </Card>
      <Card>
        {step === 0 && <BasicSetup draft={current} update={update} endpoints={eps} />}
        {step === 1 && <TypesStep draft={current} update={update} endpoints={eps} />}
        {step === 2 && (
          <DatasetsStep draft={current} update={update} datasets={ds} samples={samples.data ?? []}
            onInstalled={() => { void datasets.reload(); void samples.reload(); }} />
        )}
        {step === 3 && <ReviewStep draft={current} endpoints={eps} datasets={ds} />}
        {step === 3 && startError && <Callout tone="error" role="alert">Could not start the run: {startError}</Callout>}

        <div className="wizard-footer">
          {step > 0 ? (
            <button type="button" className="btn" onClick={() => go(step - 1)}>
              <ArrowLeft size={16} /> Back
            </button>
          ) : (
            <Link to="/" className="btn">Cancel</Link>
          )}
          <div className="page-actions" style={{ alignItems: "center" }}>
            {step < 3 && !complete[step] && (
              <span className="small muted" role="status" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                <Info size={14} aria-hidden="true" /> {missing[step]}
              </span>
            )}
            {step < 3 ? (
              <button type="button" className="btn btn-primary" disabled={!complete[step]} onClick={() => go(step + 1)}>
                Next: {STEPS[step + 1]} <ArrowRight size={16} />
              </button>
            ) : (
              <button type="button" className="btn btn-primary" disabled={!canStart} onClick={() => void start()}
                title={perfSelected ? undefined : "Select an evaluation type marked Available to start a run"}>
                <PlayCircle size={16} /> {starting ? "Starting…" : "Start Evaluation"}
              </button>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
