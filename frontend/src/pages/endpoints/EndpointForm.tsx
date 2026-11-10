import { Activity, Cpu, KeyRound, Layers3, Network, Server, SlidersHorizontal } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";

import { Callout, Drawer, Switch } from "../../components/ui";
import { ApiError, api } from "../../services/api";
import type { AdapterInfo, Endpoint, EndpointInput, Project } from "../../types/api";
import { KeyValueEditor, fromPairs, toPairs, type KeyValue } from "./KeyValueEditor";

const SUGGESTED_METADATA = ["serving_backend", "gpu_type", "gpu_count", "max_model_len"];

/** Declared model-variant fields (backend: app/schemas/model_metadata.py). Blank = unknown. */
export const VARIANT_FIELDS = [
  { key: "family", label: "Model family", placeholder: "llama-3.1-8b", hint: "Same family = same base weights" },
  { key: "quantization_method", label: "Quantization method", placeholder: "AWQ, GPTQ, GGUF Q4_K_M, none" },
  { key: "weight_precision", label: "Weight precision", placeholder: "fp16, bf16, int8, int4" },
  { key: "weight_bits", label: "Weight bits", placeholder: "16, 8, 4", numeric: true },
  { key: "kv_cache_dtype", label: "KV cache dtype", placeholder: "auto, fp8" },
] as const;
type VariantKey = (typeof VARIANT_FIELDS)[number]["key"];
const VARIANT_KEYS: readonly string[] = VARIANT_FIELDS.map((f) => f.key);

interface FormState {
  name: string;
  project_id: string;
  adapter_type: string;
  base_url: string;
  model_identifier: string;
  secret_reference: string;
  temperature: string;
  top_p: string;
  max_tokens: string;
  stream: boolean;
  request_timeout_seconds: string;
  connect_timeout_seconds: string;
  max_retries: string;
  retry_backoff_seconds: string;
  verify_tls: boolean;
  ca_cert_path: string;
  headers: KeyValue[];
  secret_headers: KeyValue[];
  deployment: KeyValue[];
  variant: Record<VariantKey, string>;
  telemetry_url: string;
  telemetry_interval: string;
  telemetry_gpus: string;
  tags: string;
  enabled: boolean;
}

function initialState(endpoint?: Endpoint): FormState {
  const rc = endpoint?.request_configuration;
  const num = (v: number | null | undefined, fallback = "") => (v == null ? fallback : String(v));
  const deployment = toPairs(endpoint?.deployment_metadata);
  if (!endpoint) {
    for (const key of SUGGESTED_METADATA) deployment.push({ key, value: "" });
  }
  return {
    name: endpoint?.name ?? "",
    project_id: endpoint?.project_id ?? "",
    adapter_type: endpoint?.adapter_type ?? "openai_compatible",
    base_url: endpoint?.base_url ?? "",
    model_identifier: endpoint?.model_identifier ?? "",
    secret_reference: endpoint?.secret_reference ?? "",
    temperature: num(rc?.temperature),
    top_p: num(rc?.top_p),
    max_tokens: num(rc?.max_tokens),
    stream: rc?.stream ?? true,
    request_timeout_seconds: num(rc?.request_timeout_seconds, "120"),
    connect_timeout_seconds: num(rc?.connect_timeout_seconds, "10"),
    max_retries: num(rc?.max_retries, "1"),
    retry_backoff_seconds: num(rc?.retry_backoff_seconds, "1"),
    verify_tls: rc?.verify_tls ?? true,
    ca_cert_path: rc?.ca_cert_path ?? "",
    headers: toPairs(rc?.headers),
    secret_headers: toPairs(rc?.secret_headers),
    deployment,
    variant: Object.fromEntries(
      VARIANT_FIELDS.map((f) => {
        const v = endpoint?.model_metadata?.[f.key];
        return [f.key, v == null ? "" : String(v)];
      }),
    ) as Record<VariantKey, string>,
    telemetry_url: endpoint?.telemetry?.url ?? "",
    telemetry_interval: num(endpoint?.telemetry?.interval_seconds, "2"),
    telemetry_gpus: (endpoint?.telemetry?.gpu_indices ?? []).join(", "),
    tags: (endpoint?.tags ?? []).join(", "),
    enabled: endpoint?.enabled ?? true,
  };
}

const optionalNumber = (v: string): number | null => (v.trim() === "" ? null : Number(v));

/** Keeps undeclared extra keys; blank variant fields are left out (unknown, never ""). */
function modelMetadata(s: FormState, existing?: Endpoint): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(existing?.model_metadata ?? {})) {
    if (!VARIANT_KEYS.includes(k)) out[k] = v;
  }
  for (const f of VARIANT_FIELDS) {
    const v = s.variant[f.key].trim();
    if (v) out[f.key] = "numeric" in f && f.numeric && /^\d+$/.test(v) ? Number(v) : v;
  }
  return out;
}

export function toPayload(s: FormState, existing?: Endpoint): EndpointInput {
  return {
    name: s.name.trim(),
    project_id: s.project_id || null,
    adapter_type: s.adapter_type,
    base_url: s.base_url.trim(),
    model_identifier: s.model_identifier.trim(),
    secret_reference: s.secret_reference.trim() || null,
    request_configuration: {
      request_timeout_seconds: Number(s.request_timeout_seconds),
      connect_timeout_seconds: Number(s.connect_timeout_seconds),
      max_retries: Number(s.max_retries),
      retry_backoff_seconds: Number(s.retry_backoff_seconds),
      temperature: optionalNumber(s.temperature),
      top_p: optionalNumber(s.top_p),
      max_tokens: optionalNumber(s.max_tokens),
      stream: s.stream,
      stream_include_usage: existing?.request_configuration.stream_include_usage ?? true,
      verify_tls: s.verify_tls,
      ca_cert_path: s.ca_cert_path.trim() || null,
      headers: fromPairs(s.headers),
      secret_headers: fromPairs(s.secret_headers),
    },
    model_metadata: modelMetadata(s, existing),
    deployment_metadata: fromPairs(s.deployment),
    telemetry: s.telemetry_url.trim()
      ? {
          enabled: true,
          source: "prometheus",
          url: s.telemetry_url.trim(),
          interval_seconds: Number(s.telemetry_interval || "2"),
          timeout_seconds: existing?.telemetry?.timeout_seconds ?? 2,
          gpu_indices: s.telemetry_gpus.trim()
            ? s.telemetry_gpus.split(",").map((g) => Number(g.trim())).filter((g) => g.toString() !== "NaN")
            : null,
        }
      : null,
    tags: s.tags
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean),
    enabled: s.enabled,
  };
}

function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: ReactNode;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && !error && <span className="hint">{hint}</span>}
      {error && (
        <span className="field-error" id={`${htmlFor}-error`}>
          {error}
        </span>
      )}
    </div>
  );
}

function Section({ icon: Icon, title, children }: { icon: typeof Cpu; title: string; children: ReactNode }) {
  return (
    <fieldset className="form-section" style={{ border: 0, padding: 0, margin: 0 }}>
      <legend className="form-section-title" style={{ marginBottom: 14 }}>
        <Icon size={16} aria-hidden="true" /> {title}
      </legend>
      {children}
    </fieldset>
  );
}

export function EndpointForm({
  endpoint,
  projects,
  adapters,
  onSaved,
  onClose,
}: {
  endpoint?: Endpoint;
  projects: Project[];
  adapters: AdapterInfo[];
  onSaved: (endpoint: Endpoint) => void;
  onClose: () => void;
}) {
  const [state, setState] = useState<FormState>(() => initialState(endpoint));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setState((s) => ({ ...s, [key]: value }));

  const input = (key: keyof FormState, errorKey: string = key) => ({
    id: `ep-${key}`,
    className: "input",
    value: state[key] as string,
    onChange: (e: { target: { value: string } }) => set(key, e.target.value as never),
    "aria-invalid": errors[errorKey] ? true : undefined,
    "aria-describedby": errors[errorKey] ? `ep-${key}-error` : undefined,
  });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setErrors({});
    setFormError(undefined);
    try {
      const payload = toPayload(state, endpoint);
      const saved = endpoint
        ? await api.endpoints.update(endpoint.id, payload)
        : await api.endpoints.create(payload);
      onSaved(saved);
    } catch (err) {
      if (err instanceof ApiError) {
        const fields = err.fieldErrors;
        setErrors(fields);
        setFormError(Object.keys(fields).length ? "Please fix the highlighted fields." : err.message);
      } else {
        setFormError(String(err));
      }
    } finally {
      setSaving(false);
    }
  }

  const rcError = (k: string) => errors[`request_configuration.${k}`];
  const formId = "endpoint-form";

  return (
    <Drawer
      title={endpoint ? `Edit ${endpoint.name}` : "Add model endpoint"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form={formId} className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : endpoint ? "Save changes" : "Create endpoint"}
          </button>
        </>
      }
    >
      <form id={formId} className="form" onSubmit={submit} noValidate>
        {formError && (
          <Callout tone="error" role="alert">
            {formError}
          </Callout>
        )}

        <Section icon={Server} title="Endpoint">
          <div className="form-row">
            <Field label="Name" htmlFor="ep-name" error={errors.name}
              hint="Unique; used by the CLI, e.g. local-vllm">
              <input {...input("name")} required placeholder="local-vllm" />
            </Field>
            <Field label="Project" htmlFor="ep-project_id" error={errors.project_id}>
              <select {...input("project_id")}>
                <option value="">Global (all projects)</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="Adapter" htmlFor="ep-adapter_type" error={errors.adapter_type}>
            <select {...input("adapter_type")}>
              {adapters.map((a) => (
                <option key={a.adapter_type} value={a.adapter_type}>
                  {a.display_name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Base URL" htmlFor="ep-base_url" error={errors.base_url}
            hint={<>API root containing <code>/chat/completions</code>, e.g. <code>http://vllm:8000/v1</code></>}>
            <input {...input("base_url")} required placeholder="http://vllm:8000/v1" />
          </Field>
          <Field label="Model identifier" htmlFor="ep-model_identifier"
            error={errors.model_identifier} hint="The model name sent to the server">
            <input {...input("model_identifier")} required placeholder="Qwen/Qwen2.5-7B-Instruct" />
          </Field>
        </Section>

        <Section icon={KeyRound} title="Authentication">
          <Field label="API key secret reference" htmlFor="ep-secret_reference"
            error={errors.secret_reference}
            hint={<>Optional. Keys are never stored here: add <code>OPENLLM_SECRET_NAME=…</code> to
              <code> secrets.env</code> on the server and enter <code>env:OPENLLM_SECRET_NAME</code>.</>}>
            <input {...input("secret_reference")} placeholder="env:OPENLLM_SECRET_VLLM_KEY"
              autoComplete="off" spellCheck={false} />
          </Field>
          {endpoint?.secret_reference && endpoint.secret_configured === false && (
            <Callout tone="warn">
              <code>{endpoint.secret_reference}</code> is not set in the server environment.
            </Callout>
          )}
          <KeyValueEditor label="Secret headers" pairs={state.secret_headers}
            onChange={(v) => set("secret_headers", v)} keyPlaceholder="X-Gateway-Key"
            valuePlaceholder="env:OPENLLM_SECRET_GATEWAY" addLabel="Add secret header"
            error={rcError("secret_headers")} />
        </Section>

        <Section icon={SlidersHorizontal} title="Generation defaults">
          <div className="form-row form-row-3">
            <Field label="Temperature" htmlFor="ep-temperature" error={rcError("temperature")}
              hint="Blank = server default">
              <input {...input("temperature", "request_configuration.temperature")}
                type="number" step="0.1" min="0" max="2" />
            </Field>
            <Field label="Top-p" htmlFor="ep-top_p" error={rcError("top_p")}>
              <input {...input("top_p", "request_configuration.top_p")}
                type="number" step="0.05" min="0" max="1" />
            </Field>
            <Field label="Max output tokens" htmlFor="ep-max_tokens" error={rcError("max_tokens")}>
              <input {...input("max_tokens", "request_configuration.max_tokens")}
                type="number" min="1" />
            </Field>
          </div>
          <Switch checked={state.stream} onChange={(v) => set("stream", v)}
            label="Stream responses (required to measure time to first token)" />
        </Section>

        <Section icon={Network} title="Connection">
          <div className="form-row">
            <Field label="Request timeout (s)" htmlFor="ep-request_timeout_seconds"
              error={rcError("request_timeout_seconds")}>
              <input {...input("request_timeout_seconds",
                "request_configuration.request_timeout_seconds")} type="number" min="1" />
            </Field>
            <Field label="Connect timeout (s)" htmlFor="ep-connect_timeout_seconds"
              error={rcError("connect_timeout_seconds")}>
              <input {...input("connect_timeout_seconds",
                "request_configuration.connect_timeout_seconds")} type="number" min="1" />
            </Field>
            <Field label="Max retries" htmlFor="ep-max_retries" error={rcError("max_retries")}
              hint="Used by benchmark runs (Milestone 3)">
              <input {...input("max_retries", "request_configuration.max_retries")}
                type="number" min="0" max="10" />
            </Field>
            <Field label="Retry backoff (s)" htmlFor="ep-retry_backoff_seconds"
              error={rcError("retry_backoff_seconds")}>
              <input {...input("retry_backoff_seconds",
                "request_configuration.retry_backoff_seconds")} type="number" min="0" step="0.5" />
            </Field>
          </div>
          <Switch checked={state.verify_tls} onChange={(v) => set("verify_tls", v)}
            label="Verify TLS certificates" />
          <Field label="Custom CA certificate path" htmlFor="ep-ca_cert_path"
            error={rcError("ca_cert_path")} hint="Optional; must be inside CA_CERT_DIR on the server">
            <input {...input("ca_cert_path", "request_configuration.ca_cert_path")}
              placeholder="/certs/internal-ca.pem" />
          </Field>
          <KeyValueEditor label="Custom headers" pairs={state.headers}
            onChange={(v) => set("headers", v)} keyPlaceholder="X-Team" valuePlaceholder="eval"
            addLabel="Add header" error={rcError("headers")}
            hint="Non-secret headers only. Credentials belong in secret headers." />
        </Section>

        <Section icon={Layers3} title="Model variant (declared)">
          <p className="small muted" style={{ margin: 0 }}>
            Used to compare quantized variants. Leave a field blank when you don't know it: it stays unknown and is never
            guessed from the model name.
          </p>
          <div className="form-row">
            {VARIANT_FIELDS.map((f) => (
              <Field key={f.key} label={f.label} htmlFor={`ep-variant-${f.key}`}
                hint={"hint" in f ? f.hint : undefined}>
                <input id={`ep-variant-${f.key}`} className="input" value={state.variant[f.key]}
                  placeholder={f.placeholder} inputMode={"numeric" in f ? "numeric" : undefined}
                  onChange={(e) => set("variant", { ...state.variant, [f.key]: e.target.value })} />
              </Field>
            ))}
          </div>
          {errors.model_metadata && (
            <span className="field-error" role="alert">{errors.model_metadata}</span>
          )}
        </Section>

        <Section icon={Activity} title="Resource telemetry (optional)">
          <Field label="Exporter URL" htmlFor="ep-telemetry_url" error={errors["telemetry.url"] ?? errors.telemetry}
            hint="Prometheus text endpoint: NVIDIA DCGM exporter (:9400/metrics), nvidia_gpu_exporter, or vLLM /metrics. Scraped while the model is measured; failures never fail a run.">
            <input {...input("telemetry_url", "telemetry.url")} placeholder="http://gpu-host:9400/metrics" />
          </Field>
          <div className="form-row">
            <Field label="Scrape interval (s)" htmlFor="ep-telemetry_interval"
              error={errors["telemetry.interval_seconds"]} hint="0.5 to 60">
              <input {...input("telemetry_interval", "telemetry.interval_seconds")} inputMode="decimal" />
            </Field>
            <Field label="GPU indices" htmlFor="ep-telemetry_gpus" error={errors["telemetry.gpu_indices"]}
              hint="Comma-separated; blank = all GPUs on the page">
              <input {...input("telemetry_gpus", "telemetry.gpu_indices")} placeholder="0, 1" />
            </Field>
          </div>
        </Section>

        <Section icon={Cpu} title="Deployment metadata (declared)">
          <KeyValueEditor label="Deployment metadata" pairs={state.deployment}
            onChange={(v) => set("deployment", v)} keyPlaceholder="gpu_type"
            valuePlaceholder="RTX 4090" addLabel="Add field" error={errors.deployment_metadata}
            hint="What you know about how this model is served (e.g. serving_backend, backend_version, gpu_type, gpu_count, tensor_parallel_size, max_model_len). Recorded with every run; not verified." />
          <Field label="Tags" htmlFor="ep-tags" error={errors.tags} hint="Comma-separated">
            <input {...input("tags")} placeholder="local, gpu, int8" />
          </Field>
          <Switch checked={state.enabled} onChange={(v) => set("enabled", v)} label="Enabled" />
        </Section>
      </form>
    </Drawer>
  );
}
