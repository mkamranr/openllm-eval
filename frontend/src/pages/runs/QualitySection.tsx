import { CheckCircle2, FlaskConical, Wrench, XCircle } from "lucide-react";

import { Card } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { api } from "../../services/api";
import type { EvaluationResult, EvaluationSummary, EvaluatorInfo, MetricValue, RunDetail, Simulation } from "../../types/runs";
import { fmt, fmtMs, fmtPct, metricIndex, metricTitle } from "./runMeta";

type Get = ReturnType<typeof metricIndex>;

export const SUITE_TITLES: Record<string, string> = {
  answer_quality: "Answer quality",
  structured_output: "Structured output",
  tool_calling: "Tool calling",
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
        correctness, and the refusal check is a phrase heuristic.
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
