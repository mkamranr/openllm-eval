import { Callout, Switch } from "../../components/ui";
import type { Endpoint } from "../../types/api";
import type { EvaluationConfig, ToolMode } from "../../types/runs";

/** Quality evaluation settings as edited in the wizard. */
export interface QualitySettings {
  toolMode: ToolMode;
  f1Threshold: string;
  allowCodeFence: boolean;
  embeddingEndpointId: string; // "" = no embedding similarity
  embeddingThreshold: string;
  faithfulnessThreshold: string;
  supportThreshold: string;
  judgeEndpointId: string; // "" = no LLM judge (lexical evidence checks)
  judgeConcurrency: string;
}

export const DEFAULT_QUALITY: QualitySettings = {
  toolMode: "simulated",
  f1Threshold: "0.5",
  allowCodeFence: true,
  embeddingEndpointId: "",
  embeddingThreshold: "0.8",
  faithfulnessThreshold: "0.8",
  supportThreshold: "0.65",
  judgeEndpointId: "",
  judgeConcurrency: "4",
};

export const QUALITY_TYPES = ["answer_quality", "structured_output", "tool_calling", "rag", "hallucination"];
/** Suites that read supplied evidence and can use the LLM judge. */
export const EVIDENCE_TYPES = ["rag", "hallucination"];

const between = (v: string, lo: number, hi: number) => {
  const n = Number(v);
  return v.trim() !== "" && !Number.isNaN(n) && n >= lo && n <= hi;
};

export function validateQuality(q: QualitySettings): Partial<Record<keyof QualitySettings, string>> {
  const e: Partial<Record<keyof QualitySettings, string>> = {};
  const f1 = Number(q.f1Threshold);
  if (!q.f1Threshold.trim() || Number.isNaN(f1) || f1 < 0 || f1 > 1) e.f1Threshold = "Use a value between 0 and 1.";
  if (q.embeddingEndpointId) {
    const t = Number(q.embeddingThreshold);
    if (!q.embeddingThreshold.trim() || Number.isNaN(t) || t < -1 || t > 1)
      e.embeddingThreshold = "Use a value between -1 and 1.";
  }
  if (!between(q.faithfulnessThreshold, 0, 1)) e.faithfulnessThreshold = "Use a value between 0 and 1.";
  if (!between(q.supportThreshold, 0.3, 1)) e.supportThreshold = "Use a value between 0.3 and 1.";
  if (q.judgeEndpointId && !(between(q.judgeConcurrency, 1, 32) && Number.isInteger(Number(q.judgeConcurrency))))
    e.judgeConcurrency = "Use a whole number from 1 to 32.";
  return e;
}

export function toEvaluationConfig(q: QualitySettings): Partial<EvaluationConfig> {
  return {
    tool_mode: q.toolMode,
    token_f1_threshold: Number(q.f1Threshold),
    allow_code_fence: q.allowCodeFence,
    embedding: q.embeddingEndpointId
      ? { endpoint_id: q.embeddingEndpointId, threshold: Number(q.embeddingThreshold) }
      : null,
    faithfulness_threshold: Number(q.faithfulnessThreshold),
    support_threshold: Number(q.supportThreshold),
    judge: q.judgeEndpointId ? { endpoint_id: q.judgeEndpointId, concurrency: Number(q.judgeConcurrency) } : null,
  };
}

export function fromEvaluationConfig(c: Partial<EvaluationConfig>): QualitySettings {
  return {
    toolMode: c.tool_mode ?? DEFAULT_QUALITY.toolMode,
    f1Threshold: String(c.token_f1_threshold ?? DEFAULT_QUALITY.f1Threshold),
    allowCodeFence: c.allow_code_fence ?? true,
    embeddingEndpointId: c.embedding?.endpoint_id ?? "",
    embeddingThreshold: String(c.embedding?.threshold ?? DEFAULT_QUALITY.embeddingThreshold),
    faithfulnessThreshold: String(c.faithfulness_threshold ?? DEFAULT_QUALITY.faithfulnessThreshold),
    supportThreshold: String(c.support_threshold ?? DEFAULT_QUALITY.supportThreshold),
    judgeEndpointId: c.judge?.endpoint_id ?? "",
    judgeConcurrency: String(c.judge?.concurrency ?? DEFAULT_QUALITY.judgeConcurrency),
  };
}

const TOOL_MODES: [ToolMode, string][] = [
  ["simulated", "Simulated results"],
  ["generation_only", "Inspect calls only"],
];

export function QualitySettingsForm({
  value: q,
  onChange,
  types,
  endpoints,
}: {
  value: QualitySettings;
  onChange: (patch: Partial<QualitySettings>) => void;
  types: string[];
  endpoints: Endpoint[];
}) {
  const errors = validateQuality(q);
  return (
    <section className="card" aria-label="Quality settings" style={{ boxShadow: "none" }}>
      <h3 style={{ marginTop: 0 }}>Quality settings</h3>
      <div className="form">
        {types.includes("tool_calling") && (
          <div className="field">
            <span className="label" id="tool-mode-label">Tool calls</span>
            <div className="segmented" role="radiogroup" aria-labelledby="tool-mode-label">
              {TOOL_MODES.map(([k, label]) => (
                <button key={k} type="button" role="radio" aria-checked={q.toolMode === k} aria-pressed={q.toolMode === k}
                  onClick={() => onChange({ toolMode: k })}>
                  {label}
                </button>
              ))}
            </div>
            <span className="hint">
              {q.toolMode === "simulated"
                ? "Valid calls get the dataset's fixture results and the model continues, so the final answer can be checked. Nothing is ever executed."
                : "Calls are checked as returned; no follow-up request is sent, so final answers are not evaluated."}
            </span>
          </div>
        )}
        {types.includes("answer_quality") && (
          <div className="form-row form-row-3">
            <div className="field">
              <label htmlFor="q-f1">Token F1 pass threshold</label>
              <input id="q-f1" className="input" type="number" step="0.05" min={0} max={1} value={q.f1Threshold}
                onChange={(e) => onChange({ f1Threshold: e.target.value })}
                aria-invalid={errors.f1Threshold ? true : undefined} />
              {errors.f1Threshold && <span className="field-error">{errors.f1Threshold}</span>}
            </div>
            <div className="field">
              <label htmlFor="q-embedding">Embedding similarity (optional)</label>
              <select id="q-embedding" className="input" value={q.embeddingEndpointId}
                onChange={(e) => onChange({ embeddingEndpointId: e.target.value })}>
                <option value="">Off</option>
                {endpoints.filter((e) => e.enabled).map((e) => (
                  <option key={e.id} value={e.id}>{e.name} ({e.model_identifier})</option>
                ))}
              </select>
              <span className="hint">The endpoint must serve /embeddings. Similarity is not correctness.</span>
            </div>
            {q.embeddingEndpointId && (
              <div className="field">
                <label htmlFor="q-emb-threshold">Similarity threshold</label>
                <input id="q-emb-threshold" className="input" type="number" step="0.05" min={-1} max={1}
                  value={q.embeddingThreshold} onChange={(e) => onChange({ embeddingThreshold: e.target.value })}
                  aria-invalid={errors.embeddingThreshold ? true : undefined} />
                {errors.embeddingThreshold && <span className="field-error">{errors.embeddingThreshold}</span>}
              </div>
            )}
          </div>
        )}
        {types.some((t) => EVIDENCE_TYPES.includes(t)) && (
          <EvidenceSettings q={q} onChange={onChange} endpoints={endpoints} errors={errors} />
        )}
        {types.includes("structured_output") && (
          <Switch checked={q.allowCodeFence} onChange={(v) => onChange({ allowCodeFence: v })}
            label="Accept JSON inside a ```json code fence (labeled separately)" />
        )}
      </div>
    </section>
  );
}

function EvidenceSettings({
  q,
  onChange,
  endpoints,
  errors,
}: {
  q: QualitySettings;
  onChange: (patch: Partial<QualitySettings>) => void;
  endpoints: Endpoint[];
  errors: Partial<Record<keyof QualitySettings, string>>;
}) {
  return (
    <>
      <div className="form-row form-row-3">
        <div className="field">
          <label htmlFor="q-judge">LLM judge (optional)</label>
          <select id="q-judge" className="input" value={q.judgeEndpointId}
            onChange={(e) => onChange({ judgeEndpointId: e.target.value })}>
            <option value="">Off: lexical evidence checks</option>
            {endpoints.filter((e) => e.enabled).map((e) => (
              <option key={e.id} value={e.id}>{e.name} ({e.model_identifier})</option>
            ))}
          </select>
          <span className="hint">
            Extracts and verifies claims and rates relevance. Runs after each model is measured.
          </span>
        </div>
        <div className="field">
          <label htmlFor="q-faith">Faithfulness pass threshold</label>
          <input id="q-faith" className="input" type="number" step="0.05" min={0} max={1}
            value={q.faithfulnessThreshold} onChange={(e) => onChange({ faithfulnessThreshold: e.target.value })}
            aria-invalid={errors.faithfulnessThreshold ? true : undefined} />
          {errors.faithfulnessThreshold && <span className="field-error">{errors.faithfulnessThreshold}</span>}
        </div>
        {q.judgeEndpointId ? (
          <div className="field">
            <label htmlFor="q-judge-concurrency">Judge concurrency</label>
            <input id="q-judge-concurrency" className="input" type="number" min={1} max={32}
              value={q.judgeConcurrency} onChange={(e) => onChange({ judgeConcurrency: e.target.value })}
              aria-invalid={errors.judgeConcurrency ? true : undefined} />
            {errors.judgeConcurrency && <span className="field-error">{errors.judgeConcurrency}</span>}
          </div>
        ) : (
          <div className="field">
            <label htmlFor="q-support">Lexical support threshold</label>
            <input id="q-support" className="input" type="number" step="0.05" min={0.3} max={1}
              value={q.supportThreshold} onChange={(e) => onChange({ supportThreshold: e.target.value })}
              aria-invalid={errors.supportThreshold ? true : undefined} />
            <span className="hint">Share of a claim&apos;s words that must appear in the evidence.</span>
            {errors.supportThreshold && <span className="field-error">{errors.supportThreshold}</span>}
          </div>
        )}
      </div>
      {q.judgeEndpointId && (
        <Callout tone="warn">
          Questions, contexts, and answers are sent to the judge endpoint. A judge can be wrong, and its
          confidence is not a measured accuracy. Every verdict keeps the judge&apos;s rationale and evidence.
        </Callout>
      )}
    </>
  );
}
