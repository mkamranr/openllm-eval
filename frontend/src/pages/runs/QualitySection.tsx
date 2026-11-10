import { CheckCircle2, FileSearch, FlaskConical, Wrench, XCircle } from "lucide-react";

import { Callout, Card } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { api } from "../../services/api";
import type { EvaluationResult, EvaluationSummary, EvaluatorInfo, MetricValue, RunDetail, Simulation } from "../../types/runs";
import { fmt, fmtMs, fmtPct, metricIndex, metricTitle } from "./runMeta";

type Get = ReturnType<typeof metricIndex>;

export const SUITE_TITLES: Record<string, string> = {
  answer_quality: "Answer quality",
  structured_output: "Structured output",
  tool_calling: "Tool calling",
  rag: "RAG",
  hallucination: "Hallucination",
  context_window: "Context window",
};

/** Titles and descriptions for evaluator names (falls back to the name). */
export function useEvaluatorInfo() {
  const info = useAsync(() => api.evaluators(), []);
  const byName = new Map<string, EvaluatorInfo>((info.data ?? []).map((e) => [e.name, e]));
  return (name: string) => byName.get(name);
}

function RateCell({ m }: { m: MetricValue | undefined }) {
  const v = m?.metric_value;
  return (
    <td className={`num ${v == null ? "na" : ""}`} title={metricTitle(m)}>
      {v == null ? "—" : fmtPct(v)}
      {m && <span className="cell-sub"> n={m.sample_count}</span>}
    </td>
  );
}

export function QualityCard({ run, get }: { run: RunDetail; get: Get }) {
  const evaluation = run.configuration_snapshot.evaluation;
  const info = useEvaluatorInfo();
  if (!evaluation?.suites.length) return null;
  const multi = run.models.length > 1;
  return (
    <Card title="Quality" icon={FlaskConical}>
      {run.models.map((m) => {
        const evaluated = get(m.id, "quality.evaluated_requests", "count");
        const consistency = get(m.id, "quality.output_consistency", "ratio");
        return multi ? null : (
          <p className="small muted" key={m.id} style={{ margin: "0 0 12px" }}>
            {evaluated?.metric_value != null ? `${evaluated.metric_value} successful requests evaluated` : "Waiting for results"}
            {" · "}failed requests are not evaluated.
            {consistency?.metric_value != null && ` Identical answers on repeated cases: ${fmtPct(consistency.metric_value)}.`}
          </p>
        );
      })}
      {evaluation.suites.map((suite) => {
        const evaluators = evaluation.evaluators.filter((e) => e.suite === suite);
        return (
          <div key={suite} style={{ marginBottom: 18 }}>
            <h3 style={{ margin: "0 0 8px" }}>{SUITE_TITLES[suite] ?? suite}</h3>
            <div className="table-wrap">
              <table className="table" aria-label={`${SUITE_TITLES[suite] ?? suite} results`}>
                <thead>
                  <tr>
                    <th scope="col">Evaluator</th>
                    {run.models.map((m) => (
                      <th key={m.id} scope="col" className="num">{multi ? `${m.name} pass rate` : "Pass rate"}</th>
                    ))}
                    {!multi && <th scope="col" className="num">Mean score</th>}
                  </tr>
                </thead>
                <tbody>
                  {evaluators.map((e) => {
                    const meta = info(e.name);
                    return (
                      <tr key={e.name}>
                        <td title={meta?.description}>
                          <div className="cell-main">{meta?.title ?? e.name}</div>
                          <div className="cell-sub mono">{e.name} v{e.version}{e.kind !== "objective" ? ` · ${e.kind.replace("_", " ")}` : ""}</div>
                        </td>
                        {run.models.map((m) => <RateCell key={m.id} m={get(m.id, `quality.${e.name}`, "pass_rate")} />)}
                        {!multi && (() => {
                          const ms = get(run.models[0]!.id, `quality.${e.name}`, "mean_score");
                          return <td className={`num ${ms?.metric_value == null ? "na" : ""}`} title={metricTitle(ms)}>{fmt(ms?.metric_value, 2)}</td>;
                        })()}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
      <p className="small muted" style={{ margin: 0 }}>
        Each evaluator is shown on its own; there is no combined quality score. Pass rates count only cases where the
        evaluator applied; "—" means no applicable case (hover for details). Similarity scores are not proof of
        correctness, and the refusal and abstention checks are phrase heuristics.
      </p>
    </Card>
  );
}

const TOOL_ROWS: { name: string; label: string; lowerIsBetter?: boolean }[] = [
  { name: "tool.selection_accuracy", label: "Tool selection accuracy" },
  { name: "tool.argument_name_accuracy", label: "Argument names" },
  { name: "tool.argument_schema_validity", label: "Argument schema validity" },
  { name: "tool.required_field_completeness", label: "Required arguments present" },
  { name: "tool.argument_exact_match_accuracy", label: "Argument values exact" },
  { name: "tool.call_precision", label: "Call precision" },
  { name: "tool.call_recall", label: "Call recall" },
  { name: "tool.sequence_accuracy", label: "Call order" },
  { name: "tool.final_answer_correctness", label: "Final answer correct" },
  { name: "tool.task_success", label: "End-to-end task success" },
  { name: "tool.missing_call_rate", label: "Missing calls", lowerIsBetter: true },
  { name: "tool.unnecessary_call_rate", label: "Unnecessary calls", lowerIsBetter: true },
  { name: "tool.hallucinated_tool_rate", label: "Invented tools", lowerIsBetter: true },
  { name: "tool.malformed_arguments_rate", label: "Malformed arguments", lowerIsBetter: true },
];

export function ToolMetricsCard({ run, get }: { run: RunDetail; get: Get }) {
  const evaluation = run.configuration_snapshot.evaluation;
  if (!evaluation?.suites.includes("tool_calling")) return null;
  return (
    <Card title="Tool-call metrics" icon={Wrench}
      action={<span className="small muted">{evaluation.tool_execution}</span>}>
      <div className="table-wrap">
        <table className="table" aria-label="Tool-call metrics">
          <thead>
            <tr>
              <th scope="col">Metric</th>
              {run.models.map((m) => <th key={m.id} scope="col" className="num">{m.name}</th>)}
            </tr>
          </thead>
          <tbody>
            {TOOL_ROWS.map((row) => (
              <tr key={row.name}>
                <td>
                  {row.label}
                  {row.lowerIsBetter && <span className="cell-sub"> (lower is better)</span>}
                </td>
                {run.models.map((m) => <RateCell key={m.id} m={get(m.id, row.name, "ratio")} />)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted" style={{ margin: "10px 0 0" }}>
        Argument metrics only count cases where the expected tool was selected, so a wrong tool choice is never
        double-counted as an argument error. No tool is ever executed: simulated results come from the dataset&apos;s
        fixtures.
      </p>
    </Card>
  );
}

export function EvaluationChips({ evaluations }: { evaluations: EvaluationSummary[] }) {
  const done = evaluations.filter((e) => e.status === "completed");
  if (!evaluations.length) return <span className="muted small">—</span>;
  const failed = done.filter((e) => !e.passed);
  return (
    <span className="small" title={failed.map((e) => e.evaluator_name).join(", ") || undefined}>
      {done.length - failed.length}/{done.length} passed
      {failed.length > 0 && <span style={{ color: "var(--danger)" }}> · {failed.length} failed</span>}
    </span>
  );
}

function statusBadge(e: EvaluationSummary) {
  if (e.status === "completed")
    return e.passed ? (
      <span className="badge badge-ok"><CheckCircle2 size={12} aria-hidden="true" /> pass</span>
    ) : (
      <span className="badge badge-error"><XCircle size={12} aria-hidden="true" /> fail</span>
    );
  const tone = e.status === "failed" ? "warn" : "neutral";
  return <span className={`badge badge-${tone}`}>{e.status.replace("_", " ")}</span>;
}

export function EvaluationList({ evaluations }: { evaluations: EvaluationResult[] }) {
  const info = useEvaluatorInfo();
  if (!evaluations.length) return null;
  return (
    <div>
      <h3 style={{ margin: "8px 0" }}>Evaluations</h3>
      <ul className="eval-list" aria-label="Evaluations">
        {evaluations.map((e) => (
          <li key={e.id}>
            <div className="eval-head">
              <span>
                <strong>{info(e.evaluator_name)?.title ?? e.evaluator_name}</strong>{" "}
                <span className="mono small muted">v{e.evaluator_version}</span>
              </span>
              <span className="eval-score">
                {e.score != null && <span className="mono small">{fmt(e.score, 2)}</span>}
                {statusBadge(e)}
              </span>
            </div>
            {e.rationale && <div className="small">{e.rationale}</div>}
            {isClaimList(e.evidence) && <ClaimTable claims={e.evidence as unknown as Claim[]} />}
            {e.evidence.length > 0 && (
              <details>
                <summary className="small">Evidence ({e.evidence.length})</summary>
                <pre className="code-block pre-wrap">{JSON.stringify(e.evidence, null, 2)}</pre>
              </details>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

const EVIDENCE_ROWS: { name: string; label: string; lowerIsBetter?: boolean }[] = [
  { name: "rag.claim_faithfulness", label: "Claim faithfulness (pooled)" },
  { name: "rag.contradicted_claim_rate", label: "Contradicted claims", lowerIsBetter: true },
  { name: "hallucination.claim_support_precision", label: "Claim support precision" },
  { name: "hallucination.unsupported_claim_rate", label: "Unsupported claims", lowerIsBetter: true },
  { name: "hallucination.contradiction_rate", label: "Contradicted claims", lowerIsBetter: true },
  { name: "hallucination.unverifiable_claim_rate", label: "Unverifiable (needs review)", lowerIsBetter: true },
  { name: "hallucination.evidence_coverage", label: "Evidence coverage" },
  { name: "hallucination.answers_with_contradictions", label: "Answers with a contradiction", lowerIsBetter: true },
  { name: "hallucination.answer_despite_insufficient_evidence", label: "Answered without evidence", lowerIsBetter: true },
  { name: "hallucination.abstention_precision", label: "Abstention precision" },
  { name: "hallucination.abstention_recall", label: "Abstention recall" },
];

export function EvidenceMetricsCard({ run, get }: { run: RunDetail; get: Get }) {
  const evaluation = run.configuration_snapshot.evaluation;
  const suites = evaluation?.suites ?? [];
  if (!suites.includes("rag") && !suites.includes("hallucination")) return null;
  const judge = evaluation?.judge_endpoint;
  const rows = EVIDENCE_ROWS.filter((r) => suites.includes(r.name.split(".")[0]!));
  return (
    <Card title="Evidence and claims" icon={FileSearch}
      action={
        <span className="small muted">
          {judge ? `LLM judge: ${judge.name} (${judge.model_identifier})` : "Lexical evidence checks (no judge)"}
        </span>
      }>
      {judge?.judges_tested_model && (
        <Callout tone="warn">The judge is also one of the models under test, so it grades its own answers.</Callout>
      )}
      <div className="table-wrap">
        <table className="table" aria-label="Evidence metrics">
          <thead>
            <tr>
              <th scope="col">Metric</th>
              {run.models.map((m) => <th key={m.id} scope="col" className="num">{m.name}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.name}>
                <td>
                  {row.label}
                  {row.lowerIsBetter && <span className="cell-sub"> (lower is better)</span>}
                </td>
                {run.models.map((m) => <RateCell key={m.id} m={get(m.id, row.name, "ratio")} />)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted" style={{ margin: "10px 0 0" }}>
        Claims are compared with the evidence supplied in the dataset only; a supported claim is not proven true, and
        the retrieved context may itself be wrong. Supported, unsupported, contradicted, and unverifiable claims are
        counted separately; open a case to see each claim with its evidence and source.
      </p>
    </Card>
  );
}

interface Claim {
  claim: string;
  classification: "supported" | "unsupported" | "contradicted" | "unverifiable";
  evidence: string | null;
  passage: string | null;
  document_id?: string | null;
  chunk_id?: string | null;
  page?: number | null;
  rationale: string;
  confidence: number | null;
  method: string;
  review_recommended: boolean;
}

const CLAIM_TONE: Record<Claim["classification"], string> = {
  supported: "ok",
  unsupported: "warn",
  contradicted: "error",
  unverifiable: "neutral",
};

function isClaimList(evidence: Record<string, unknown>[]) {
  return evidence.length > 0 && evidence.every((e) => typeof e.classification === "string" && typeof e.claim === "string");
}

function source(c: Claim) {
  if (!c.passage && !c.document_id) return "—";
  const doc = c.document_id ?? c.passage;
  return `${doc}${c.chunk_id ? ` #${c.chunk_id}` : ""}${c.page != null ? ` · p.${c.page}` : ""}`;
}

export function ClaimTable({ claims }: { claims: Claim[] }) {
  return (
    <div className="table-wrap" style={{ marginTop: 8 }}>
      <table className="table" aria-label="Claims">
        <thead>
          <tr>
            <th scope="col">Claim</th>
            <th scope="col">Verdict</th>
            <th scope="col">Evidence</th>
            <th scope="col">Source</th>
          </tr>
        </thead>
        <tbody>
          {claims.map((c, i) => (
            <tr key={i}>
              <td>
                <div className="cell-main">{c.claim}</div>
                <div className="cell-sub">{c.rationale}</div>
              </td>
              <td>
                <span className={`badge badge-${CLAIM_TONE[c.classification] ?? "neutral"}`}>{c.classification}</span>
                {c.review_recommended && <div className="cell-sub">review</div>}
                {c.confidence != null && <div className="cell-sub">judge confidence {fmt(c.confidence, 2)}</div>}
              </td>
              <td className="small">{c.evidence ?? "—"}</td>
              <td className="mono small">{source(c)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SimulationView({ simulation }: { simulation: Simulation }) {
  return (
    <div>
      <h3 style={{ margin: "8px 0" }}>Simulated tool execution</h3>
      <p className="small muted" style={{ marginTop: 0 }}>
        Nothing was executed. Each call was validated against the tool&apos;s schema and answered with the dataset&apos;s
        fixture result (or an error), then the model was asked to continue.
      </p>
      <ol className="eval-list">
        {simulation.rounds.map((r) => (
          <li key={r.round}>
            <div className="eval-head">
              <strong>Round {r.round}</strong>
              <span className="small muted">{fmtMs(r.latency_ms)}{r.error ? ` · ${r.error}` : ""}</span>
            </div>
            {r.tool_results.map((t, i) => (
              <div key={i} className="small">
                <span className="mono">{t.name}({typeof t.arguments === "string" ? t.arguments : JSON.stringify(t.arguments)})</span>
                {" → "}
                <span className={t.fixture_matched ? "" : "fail"} style={t.fixture_matched ? undefined : { color: "var(--danger)" }}>
                  {t.result}
                </span>
                {t.fixture_matched ? <span className="badge badge-neutral" style={{ marginLeft: 6 }}>fixture</span> : null}
              </div>
            ))}
          </li>
        ))}
      </ol>
      {simulation.completed ? (
        <pre className="code-block pre-wrap">{simulation.final_content ?? "(final answer not stored)"}</pre>
      ) : (
        <p className="small" style={{ color: "var(--danger)" }}>{simulation.stop_reason}</p>
      )}
    </div>
  );
}
