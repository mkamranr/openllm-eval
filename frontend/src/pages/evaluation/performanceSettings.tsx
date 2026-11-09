import { Switch } from "../../components/ui";
import type { GenerationConfig, LoadConfig, LoadProfile } from "../../types/runs";
import { PROFILE_META } from "../runs/runMeta";

/** Performance run settings as edited in the wizard. Numbers stay strings so fields can be empty. */
export interface PerformanceSettings {
  profile: LoadProfile;
  requests: string; // "" = one per dataset case
  concurrency: string;
  warmup: string;
  stream: boolean;
  maxTokens: string; // "" = endpoint default
  temperature: string; // "" = endpoint default
  timeout: string; // "" = endpoint setting
  retries: string; // "" = endpoint setting
  rampSteps: string; // "1, 2, 4, 8"
  requestsPerStep: string;
  duration: string;
}

export const DEFAULT_PERFORMANCE: PerformanceSettings = {
  profile: "fixed_concurrency",
  requests: "",
  concurrency: "4",
  warmup: "2",
  stream: true,
  maxTokens: "256",
  temperature: "0",
  timeout: "",
  retries: "",
  rampSteps: "1, 2, 4, 8",
  requestsPerStep: "20",
  duration: "60",
};

// Mirrors backend/app/schemas/run.py.
const SMOKE_MAX_REQUESTS = 5;
const SMOKE_MAX_CONCURRENCY = 2;
const MAX_RAMP_STEPS = 20;

type Field = keyof PerformanceSettings;
export type SettingsErrors = Partial<Record<Field, string>>;

const isInt = (s: string) => /^\d+$/.test(s.trim());
const num = (s: string) => Number(s.trim());
const parseSteps = (s: string) => s.split(/[\s,→>]+/).filter(Boolean);

export function validatePerformance(p: PerformanceSettings): SettingsErrors {
  const e: SettingsErrors = {};
  const intField = (f: Field, label: string, min: number, optional: boolean, max?: number) => {
    const v = String(p[f]).trim();
    if (!v) {
      if (!optional) e[f] = `${label} is required.`;
      return;
    }
    if (!isInt(v) || num(v) < min) e[f] = `${label} must be a whole number of at least ${min}.`;
    else if (max != null && num(v) > max) e[f] = `${label} must be at most ${max}.`;
  };
  const ramp = p.profile === "ramp";
  const sustained = p.profile === "sustained";
  const smoke = p.profile === "smoke";
  if (!ramp) intField("requests", "Requests", 1, true, smoke ? SMOKE_MAX_REQUESTS : undefined);
  if (!ramp && p.profile !== "sequential")
    intField("concurrency", "Concurrency", 1, false, smoke ? SMOKE_MAX_CONCURRENCY : undefined);
  intField("warmup", "Warm-up requests", 0, true, 1000);
  intField("maxTokens", "Max tokens", 1, true);
  intField("retries", "Retries", 0, true, 10);
  if (p.temperature.trim()) {
    const t = num(p.temperature);
    if (Number.isNaN(t) || t < 0 || t > 2) e.temperature = "Temperature must be between 0 and 2.";
  }
  if (p.timeout.trim()) {
    const t = num(p.timeout);
    if (Number.isNaN(t) || t <= 0 || t > 3600) e.timeout = "Timeout must be between 0 and 3600 seconds.";
  }
  if (ramp) {
    const steps = parseSteps(p.rampSteps);
    if (steps.length < 2) e.rampSteps = "Enter at least two concurrency steps, e.g. 1, 2, 4, 8.";
    else if (steps.length > MAX_RAMP_STEPS) e.rampSteps = `At most ${MAX_RAMP_STEPS} steps.`;
    else if (!steps.every((s) => isInt(s) && num(s) >= 1)) e.rampSteps = "Steps must be whole numbers of at least 1.";
    else if (steps.some((s, i) => i > 0 && num(s) <= num(steps[i - 1] ?? "0"))) e.rampSteps = "Each step must be higher than the previous one.";
    intField("requestsPerStep", "Requests per step", 1, true);
  }
  if (sustained) {
    const d = num(p.duration);
    if (!p.duration.trim() || Number.isNaN(d) || d < 1) e.duration = "Duration must be at least 1 second.";
  }
  return e;
}

const optInt = (s: string) => (s.trim() ? Math.trunc(num(s)) : null);
const optNum = (s: string) => (s.trim() ? num(s) : null);

/** Convert wizard settings into the API's generation and load blocks. */
export function toRunConfig(p: PerformanceSettings): { generation: Partial<GenerationConfig>; load: Partial<LoadConfig> } {
  const load: Partial<LoadConfig> = {
    profile: p.profile,
    warmup_requests: optInt(p.warmup) ?? 0,
    stream: p.stream,
    request_timeout_seconds: optNum(p.timeout),
    max_retries: optInt(p.retries),
  };
  if (p.profile === "ramp") {
    load.ramp_concurrency = parseSteps(p.rampSteps).map(Number);
    load.requests_per_step = optInt(p.requestsPerStep);
  } else {
    load.requests = optInt(p.requests);
    load.concurrency = p.profile === "sequential" ? 1 : (optInt(p.concurrency) ?? 1);
  }
  if (p.profile === "sustained") load.duration_seconds = optNum(p.duration);
  return {
    generation: { temperature: optNum(p.temperature), max_tokens: optInt(p.maxTokens) },
    load,
  };
}

/** Inverse of toRunConfig: fill wizard settings from a saved profile's generation and load. */
export function fromRunConfig(load: Partial<LoadConfig>, generation: Partial<GenerationConfig>): PerformanceSettings {
  const str = (v: number | null | undefined, fallback = "") => (v == null ? fallback : String(v));
  return {
    ...DEFAULT_PERFORMANCE,
    profile: load.profile ?? DEFAULT_PERFORMANCE.profile,
    requests: str(load.requests),
    concurrency: str(load.concurrency, DEFAULT_PERFORMANCE.concurrency),
    warmup: str(load.warmup_requests, "0"),
    stream: load.stream ?? true,
    timeout: str(load.request_timeout_seconds),
    retries: str(load.max_retries),
    rampSteps: load.ramp_concurrency?.length ? load.ramp_concurrency.join(", ") : DEFAULT_PERFORMANCE.rampSteps,
    requestsPerStep: str(load.requests_per_step, DEFAULT_PERFORMANCE.requestsPerStep),
    duration: str(load.duration_seconds, DEFAULT_PERFORMANCE.duration),
    maxTokens: str(generation.max_tokens),
    temperature: str(generation.temperature),
  };
}

export function describeLoad(p: PerformanceSettings, cases: number): string {
  const label = PROFILE_META[p.profile].label;
  const req = p.requests.trim() ? `${p.requests.trim()} requests` : `${cases} requests (one per case)`;
  switch (p.profile) {
    case "ramp": {
      const steps = parseSteps(p.rampSteps);
      const per = p.requestsPerStep.trim() || String(cases);
      return `${label}: concurrency ${steps.join(" → ")}, ${per} requests per step`;
    }
    case "sustained":
      return `${label}: ${p.concurrency} in flight for ${p.duration}s${p.requests.trim() ? `, capped at ${p.requests.trim()} requests` : ""}`;
    case "sequential":
      return `${label}: ${req}, one at a time`;
    case "smoke":
      return `${label}: ${p.requests.trim() || Math.min(SMOKE_MAX_REQUESTS, cases)} requests at concurrency ${p.concurrency}`;
    default:
      return `${label}: ${req} at concurrency ${p.concurrency}`;
  }
}

function NumberField({
  id, label, value, onChange, error, hint, placeholder, step,
}: {
  id: string; label: string; value: string; onChange: (v: string) => void; error?: string; hint?: string;
  placeholder?: string; step?: string;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} className="input" inputMode="decimal" type="number" min={0} step={step ?? "1"} value={value}
        placeholder={placeholder} onChange={(e) => onChange(e.target.value)} aria-invalid={error ? true : undefined}
        aria-describedby={error || hint ? `${id}-help` : undefined} />
      {error ? <span className="field-error" id={`${id}-help`}>{error}</span> : hint ? <span className="hint" id={`${id}-help`}>{hint}</span> : null}
    </div>
  );
}

export function PerformanceSettingsForm({
  value, onChange, errors,
}: {
  value: PerformanceSettings; onChange: (p: Partial<PerformanceSettings>) => void; errors: SettingsErrors;
}) {
  const p = value;
  const set = (f: Field) => (v: string) => onChange({ [f]: v } as Partial<PerformanceSettings>);
  const switchProfile = (profile: LoadProfile) => {
    const patch: Partial<PerformanceSettings> = { profile };
    if (profile === "smoke") {
      patch.concurrency = String(Math.min(SMOKE_MAX_CONCURRENCY, Number(p.concurrency) || 1));
      if (p.requests.trim() && Number(p.requests) > SMOKE_MAX_REQUESTS) patch.requests = "";
    }
    onChange(patch);
  };
  return (
    <section className="card" aria-label="Run settings" style={{ boxShadow: "none" }}>
      <h3 style={{ marginTop: 0 }}>Run settings</h3>
      <p className="small muted" style={{ marginTop: -6 }}>
        Load and generation apply to every run; performance metrics are always recorded.
      </p>
      <div className="form">
        <div className="field">
          <span className="label" id="profile-label">Load profile</span>
          <div className="segmented" role="radiogroup" aria-labelledby="profile-label">
            {(Object.keys(PROFILE_META) as LoadProfile[]).map((k) => (
              <button key={k} type="button" role="radio" aria-checked={p.profile === k} aria-pressed={p.profile === k}
                onClick={() => switchProfile(k)}>
                {PROFILE_META[k].label}
              </button>
            ))}
          </div>
          <span className="hint">{PROFILE_META[p.profile].description}</span>
        </div>

        <div className="form-row form-row-3">
          {p.profile === "ramp" ? (
            <>
              <div className="field">
                <label htmlFor="pf-ramp">Concurrency steps</label>
                <input id="pf-ramp" className="input" value={p.rampSteps} onChange={(e) => set("rampSteps")(e.target.value)}
                  placeholder="1, 2, 4, 8" aria-invalid={errors.rampSteps ? true : undefined} aria-describedby="pf-ramp-help" />
                {errors.rampSteps ? <span className="field-error" id="pf-ramp-help">{errors.rampSteps}</span>
                  : <span className="hint" id="pf-ramp-help">Increasing, comma-separated.</span>}
              </div>
              <NumberField id="pf-per-step" label="Requests per step" value={p.requestsPerStep} onChange={set("requestsPerStep")}
                error={errors.requestsPerStep} placeholder="one per case" />
            </>
          ) : (
            <>
              <NumberField id="pf-requests" label={p.profile === "sustained" ? "Max requests (optional)" : "Measured requests per model"}
                value={p.requests} onChange={set("requests")} error={errors.requests}
                placeholder={p.profile === "sustained" ? "no cap" : "one per case"}
                hint={p.profile === "smoke" ? `At most ${SMOKE_MAX_REQUESTS}.` : "Cases repeat in order when you ask for more."} />
              {p.profile !== "sequential" && (
                <NumberField id="pf-concurrency" label="Concurrency" value={p.concurrency} onChange={set("concurrency")}
                  error={errors.concurrency} hint={p.profile === "smoke" ? `At most ${SMOKE_MAX_CONCURRENCY}.` : "Requests kept in flight."} />
              )}
            </>
          )}
          {p.profile === "sustained" && (
            <NumberField id="pf-duration" label="Duration (seconds)" value={p.duration} onChange={set("duration")} error={errors.duration} />
          )}
          <NumberField id="pf-warmup" label="Warm-up requests" value={p.warmup} onChange={set("warmup")} error={errors.warmup}
            hint="Sent first, excluded from metrics." />
        </div>

        <div className="form-row form-row-3">
          <NumberField id="pf-max-tokens" label="Max tokens" value={p.maxTokens} onChange={set("maxTokens")} error={errors.maxTokens}
            placeholder="endpoint default" />
          <NumberField id="pf-temperature" label="Temperature" value={p.temperature} onChange={set("temperature")} step="0.1"
            error={errors.temperature} placeholder="endpoint default" hint="0 = greedy, most repeatable." />
          <NumberField id="pf-timeout" label="Timeout (seconds)" value={p.timeout} onChange={set("timeout")} step="any"
            error={errors.timeout} placeholder="endpoint setting" />
          <NumberField id="pf-retries" label="Retries" value={p.retries} onChange={set("retries")} error={errors.retries}
            placeholder="endpoint setting" hint="Retries are recorded and reported separately." />
        </div>

        <Switch checked={p.stream} onChange={(v) => onChange({ stream: v })} label="Stream responses (needed to measure TTFT)" />
      </div>
    </section>
  );
}
