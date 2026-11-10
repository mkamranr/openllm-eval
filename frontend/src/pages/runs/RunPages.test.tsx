import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ROUTER_FUTURE } from "../../app/routerFuture";
import { jsonResponse, makeMetric, makeResult, makeRun, makeRunModel, page, stubFetch } from "../../test/fakeApi";
import type { RunDetail } from "../../types/runs";
import { RunDetailPage } from "./RunDetailPage";
import { RunsPage } from "./RunsPage";

const METRICS = [
  makeMetric("requests", "attempted", 10),
  makeMetric("requests", "succeeded", 9),
  makeMetric("error_rate", "ratio", 0.1),
  makeMetric("ttft_ms", "p50", 85),
  makeMetric("latency_ms", "p95", 640),
  makeMetric("output_token_throughput", "value", 312.4),
  makeMetric("inter_token_latency_ms", "p50", null, { metadata: { unavailable_reason: "fewer than two output chunks" } }),
];

const FAILED = makeResult({
  id: "res2",
  case_identifier: "qa-007",
  request_index: 6,
  status: "failed",
  error_type: "http_error",
  error_message: "Upstream returned 503",
  http_status: 503,
  latency_ms: 12,
  ttft_ms: null,
  attempt_number: 2,
  response_data: {},
  performance_metrics: { streamed: true },
});

function renderAt(path: string) {
  return render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={[path]}>
      <Routes>
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/runs/:runId" element={<RunDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function stubRun(run: RunDetail, extra: Parameters<typeof stubFetch>[0] = () => undefined) {
  return stubFetch(extra, (method, path) => {
    if (method === "GET" && path === `/runs/${run.id}`) return jsonResponse(run);
    if (path === `/runs/${run.id}/metrics`) return jsonResponse({ run_id: run.id, live: false, metrics_version: "1", items: METRICS });
    if (path.startsWith(`/runs/${run.id}/results?`)) {
      const failedOnly = path.includes("status=failed");
      return jsonResponse(page(failedOnly ? [FAILED] : [makeResult(), FAILED]));
    }
    return undefined;
  });
}

describe("RunsPage", () => {
  it("lists runs with status and progress, and filters them", async () => {
    stubFetch((_m, path) =>
      path.startsWith("/runs?")
        ? jsonResponse(page([makeRun(), makeRun({ id: "r2", name: "Nightly", status: "completed", error_summary: null, progress: { completed: 10, succeeded: 10, failed: 0 } })]))
        : undefined,
    );
    const user = userEvent.setup();
    renderAt("/runs");
    const table = await screen.findByRole("table", { name: "Runs" });
    expect(within(table).getByText("Prod benchmark")).toBeInTheDocument();
    expect(within(table).getByText("Completed with errors")).toBeInTheDocument();
    expect(within(table).getByText("1 failed")).toBeInTheDocument();
    expect(within(table).getAllByText("Fixed concurrency · concurrency 4")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "With errors" }));
    expect(within(table).queryByText("Nightly")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "All runs" }));
    await user.type(screen.getByLabelText("Search runs"), "night");
    expect(within(table).queryByText("Prod benchmark")).not.toBeInTheDocument();
    expect(within(table).getByText("Nightly")).toBeInTheDocument();
  });

  it("shows an empty state that links to the wizard", async () => {
    stubFetch((_m, path) => (path.startsWith("/runs?") ? jsonResponse(page([])) : undefined));
    renderAt("/runs");
    expect(await screen.findByText("No benchmark runs yet")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /New evaluation/ }).length).toBeGreaterThan(0);
  });
});

describe("RunDetailPage", () => {
  it("shows metrics, failures, unavailable reasons and the configuration", async () => {
    stubRun(makeRun());
    const user = userEvent.setup();
    renderAt("/runs/r1");

    expect(await screen.findByRole("heading", { level: 1, name: /Prod benchmark/ })).toBeInTheDocument();
    expect(screen.getByText(/1 measured request failed/)).toHaveTextContent("http_error 1");

    const metrics = await screen.findByRole("table", { name: "Metrics by model" });
    expect(within(metrics).getByText("9 / 10")).toBeInTheDocument();
    expect(within(metrics).getByText("10.0%")).toBeInTheDocument();
    expect(within(metrics).getAllByText("85 ms").length).toBeGreaterThan(0);
    expect(within(metrics).getByText("640 ms")).toBeInTheDocument();
    // Missing values show a dash with the reason, never 0.
    const itl = within(metrics).getAllByTitle(/fewer than two output chunks/);
    expect(itl[0]).toHaveTextContent("—");

    const results = await screen.findByRole("table", { name: "Request results" });
    expect(within(results).getByText("qa-001")).toBeInTheDocument();
    expect(within(results).getByText("http_error (HTTP 503)")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Failures" }));
    await waitFor(() =>
      expect(within(screen.getByRole("table", { name: "Request results" })).queryByText("qa-001")).not.toBeInTheDocument(),
    );
    await user.click(screen.getByRole("button", { name: "qa-007" }));
    const drawer = await screen.findByRole("dialog");
    expect(drawer).toHaveTextContent("Upstream returned 503");
    expect(drawer).toHaveTextContent("Attempt2 (final)");

    expect(screen.getByText("Greedy (temperature 0); servers may still vary slightly")).toBeInTheDocument();
    expect(screen.getByText("2 per model, excluded from metrics")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry failed/ })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /Cancel run/ })).not.toBeInTheDocument();
  });

  it("polls a running run and lets you cancel it", async () => {
    const running = makeRun({
      status: "running",
      completed_at: null,
      error_summary: null,
      progress: { completed: 3, succeeded: 3, failed: 0, in_flight: 2 },
      models: [makeRunModel({ status: "running", completed_at: null, progress: { phase: "measured", completed: 3, succeeded: 3, in_flight: 2, current_concurrency: 4 } })],
    });
    const { calls } = stubRun(running, (method, path) =>
      method === "POST" && path === "/runs/r1/cancel"
        ? jsonResponse({ ...running, status: "cancel_requested", cancel_requested_at: "2026-11-01T00:00:10Z" })
        : undefined,
    );
    const user = userEvent.setup();
    renderAt("/runs/r1");

    expect(await screen.findByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.getByTestId("model-local-vllm")).toHaveTextContent("Measuring · concurrency 4");
    expect(screen.getByTestId("model-local-vllm")).toHaveTextContent("2 in flight");

    await user.click(screen.getByRole("button", { name: /Cancel run/ }));
    expect(await screen.findByText("Cancellation requested.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cancelling/ })).toBeDisabled();
    expect(calls.some((c) => c.method === "POST" && c.path === "/runs/r1/cancel")).toBe(true);
  });

  it("shows per-step metrics for a ramp run", async () => {
    const steps = [
      { step: 0, concurrency: 1, start: 0, count: 5, duration_seconds: null },
      { step: 1, concurrency: 4, start: 5, count: 5, duration_seconds: null },
    ];
    const run = makeRun({ load: { ...makeRun().load, profile: "ramp", steps } });
    stubFetch((_m, path) => {
      if (path === "/runs/r1") return jsonResponse(run);
      if (path === "/runs/r1/metrics")
        return jsonResponse({
          run_id: "r1",
          live: false,
          metrics_version: "1",
          items: [
            makeMetric("request_throughput", "value", 1.5, { load_step: 0 }),
            makeMetric("request_throughput", "value", 4.25, { load_step: 1 }),
          ],
        });
      if (path.startsWith("/runs/r1/results")) return jsonResponse(page([]));
      return undefined;
    });
    renderAt("/runs/r1");
    const ramp = await screen.findByRole("table", { name: "Ramp steps for local-vllm" });
    expect(within(ramp).getByText("1.50")).toBeInTheDocument();
    expect(within(ramp).getByText("4.25")).toBeInTheDocument();
  });

  it("shows quality and tool-call metrics, quality failures and evaluation details", async () => {
    const run = makeRun({
      id: "rq",
      suites: ["performance", "answer_quality", "tool_calling"],
      configuration_snapshot: {
        ...makeRun().configuration_snapshot,
        profile: { id: "pf1", name: "Nightly quality" },
        evaluation: {
          suites: ["answer_quality", "tool_calling"],
          evaluators: [
            { name: "exact_match", version: "1.0.0", suite: "answer_quality", kind: "objective" },
            { name: "tool_selection", version: "1.0.0", suite: "tool_calling", kind: "objective" },
          ],
          config: { tool_mode: "simulated", max_tool_rounds: 3, token_f1_threshold: 0.5, allow_code_fence: true, disabled_evaluators: [], embedding: null, faithfulness_threshold: 0.8, support_threshold: 0.65, judge: null },
          framework_version: "1",
          metrics_version: "1",
          tool_execution: "Simulated: fixture results only; nothing is executed.",
          embedding_endpoint: null,
        },
      },
    });
    const summary = { evaluator_name: "exact_match", suite: "answer_quality", status: "completed" as const, score: 0, passed: false, label: "mismatch" };
    const bad = makeResult({ id: "res3", case_identifier: "qa-003", evaluations: [summary] });
    const qualityMetrics = [
      ...METRICS,
      makeMetric("quality.evaluated_requests", "count", 9),
      makeMetric("quality.exact_match", "pass_rate", 0.5, { sample_count: 8 }),
      makeMetric("quality.exact_match", "mean_score", 0.5, { sample_count: 8 }),
      makeMetric("quality.tool_selection", "pass_rate", null, { sample_count: 0, metadata: { unavailable_reason: "no applicable cases" } }),
      makeMetric("tool.selection_accuracy", "ratio", 0.6, { sample_count: 5 }),
      makeMetric("tool.hallucinated_tool_rate", "ratio", 0.25, { sample_count: 4 }),
    ];
    const { calls } = stubFetch((method, path) => {
      if (path === "/evaluators")
        return jsonResponse([
          { name: "exact_match", version: "1.0.0", suite: "answer_quality", kind: "objective", title: "Exact match", description: "", case_types: ["qa"], optional: false },
        ]);
      if (method === "GET" && path === "/runs/rq") return jsonResponse(run);
      if (path === "/runs/rq/metrics") return jsonResponse({ run_id: "rq", live: false, metrics_version: "1", items: qualityMetrics });
      if (path === "/runs/rq/results/res3")
        return jsonResponse({
          ...bad,
          response_data: {
            content: "Paris, France",
            simulation: {
              mode: "simulated", executed: false, completed: true, final_content: "It is sunny.",
              rounds: [{ round: 1, latency_ms: 40, ok: true, error: null, next_tool_calls: [],
                tool_results: [{ name: "get_weather", arguments: { city: "Paris" }, fixture_matched: true, valid: true, result: "{\"temp\": 21}" }] }],
            },
          },
          evaluations: [{ ...summary, id: "ev1", evaluator_version: "1.0.0", rationale: "Answer differs from the reference.", evidence: [], metadata: {}, created_at: "2026-11-01T00:00:00Z" }],
        });
      if (path.startsWith("/runs/rq/results?"))
        return jsonResponse(page(path.includes("evaluation=failed") ? [bad] : [makeResult(), bad]));
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/runs/rq");

    const answer = await screen.findByRole("table", { name: "Answer quality results" });
    expect(within(answer).getByText("Exact match")).toBeInTheDocument();
    expect(within(answer).getByText("50.0%")).toBeInTheDocument();
    expect(screen.getByText(/9 successful requests evaluated/)).toBeInTheDocument();
    // Missing values show a dash, never 0.
    expect(within(screen.getByRole("table", { name: "Tool calling results" })).getByTitle(/no applicable cases/)).toHaveTextContent("—");
    const tools = screen.getByRole("table", { name: "Tool-call metrics" });
    expect(within(tools).getByText("60.0%")).toBeInTheDocument();
    expect(within(tools).getByText("25.0%")).toBeInTheDocument();
    expect(screen.getByText("Simulated: fixture results only; nothing is executed.", { selector: "span" })).toBeInTheDocument();

    expect(screen.getByText("Nightly quality")).toBeInTheDocument();
    expect(screen.getByText(/exact_match v1.0.0, tool_selection v1.0.0/)).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Quality failures" }));
    await waitFor(() => expect(calls.some((c) => c.path.includes("evaluation=failed") && c.path.includes("phase=measured"))).toBe(true));
    const results = screen.getByRole("table", { name: "Request results" });
    await waitFor(() => expect(within(results).queryByText("qa-001")).not.toBeInTheDocument());
    expect(within(results).getByText("0/1 passed")).toBeInTheDocument();

    await user.click(within(results).getByRole("button", { name: "qa-003" }));
    const drawer = await screen.findByRole("dialog");
    const list = await within(drawer).findByRole("list", { name: "Evaluations" });
    expect(list).toHaveTextContent("Exact match");
    expect(list).toHaveTextContent("Answer differs from the reference.");
    expect(list).toHaveTextContent("fail");
    expect(drawer).toHaveTextContent("Simulated tool execution");
    expect(drawer).toHaveTextContent('get_weather({"city":"Paris"})');
    expect(drawer).toHaveTextContent("It is sunny.");
  });
  it("shows evidence metrics, the judge, and each claim with its source", async () => {
    const config = {
      tool_mode: "simulated" as const, max_tool_rounds: 3, token_f1_threshold: 0.5, allow_code_fence: true,
      disabled_evaluators: [], embedding: null, faithfulness_threshold: 0.8, support_threshold: 0.65,
      judge: { endpoint_id: "e9", concurrency: 4 },
    };
    const run = makeRun({
      id: "rr",
      suites: ["performance", "rag", "hallucination"],
      models: [makeRunModel({ progress: { phase: "judging", judge_planned: 6, judge_completed: 2 }, status: "running", completed_at: null })],
      status: "running",
      configuration_snapshot: {
        ...makeRun().configuration_snapshot,
        evaluation: {
          suites: ["rag", "hallucination"],
          evaluators: [
            { name: "rag_faithfulness", version: "1.0.0", suite: "rag", kind: "evidence" },
            { name: "claim_support", version: "1.0.0", suite: "hallucination", kind: "evidence" },
          ],
          config,
          judge_endpoint: { name: "judge-70b", model_identifier: "llama-70b", judges_tested_model: true },
          evidence_method: "judge",
        },
      },
    });
    const faith = { evaluator_name: "rag_faithfulness", suite: "rag", status: "completed" as const, score: 0, passed: false, label: "contradicted" };
    const result = makeResult({ id: "rx", case_identifier: "rag-002", evaluations: [faith] });
    stubFetch((method, path) => {
      if (path === "/evaluators")
        return jsonResponse([
          { name: "rag_faithfulness", version: "1.0.0", suite: "rag", kind: "evidence", title: "Faithfulness", description: "", case_types: ["rag"], optional: false },
        ]);
      if (method === "GET" && path === "/runs/rr") return jsonResponse(run);
      if (path === "/runs/rr/metrics")
        return jsonResponse({
          run_id: "rr", live: true, metrics_version: "2",
          items: [
            ...METRICS,
            makeMetric("hallucination.contradiction_rate", "ratio", 0.25, { sample_count: 4 }),
            makeMetric("hallucination.abstention_recall", "ratio", null, { sample_count: 0, metadata: { unavailable_reason: "No case is labeled unanswerable." } }),
          ],
        });
      if (path === "/runs/rr/results/rx")
        return jsonResponse({
          ...result,
          evaluations: [{
            ...faith, id: "ev9", evaluator_version: "1.0.0", rationale: "Faithfulness 0.00", metadata: { method: "judge" }, created_at: "2026-11-01T00:00:00Z",
            evidence: [{
              claim: "Claims are due within 45 days.", classification: "contradicted", evidence: "within 30 days of purchase",
              passage: "policy-002#c01", document_id: "policy-002", chunk_id: "c01", page: 1, rationale: "30, not 45.",
              confidence: 0.9, method: "judge_verifier", review_recommended: false, review_status: "not_reviewed",
            }],
          }],
        });
      if (path.startsWith("/runs/rr/results?")) return jsonResponse(page([result]));
      if (path.startsWith("/runs/rr/events")) return undefined;
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/runs/rr");

    expect(await screen.findByText("Judging 2/6")).toBeInTheDocument();
    const evidence = await screen.findByRole("table", { name: "Evidence metrics" });
    expect(within(evidence).getByText("25.0%")).toBeInTheDocument();
    expect(within(evidence).getByTitle(/No case is labeled unanswerable/)).toHaveTextContent("—");
    expect(screen.getByText("LLM judge: judge-70b (llama-70b)")).toBeInTheDocument();
    expect(screen.getByText(/grades its own answers/)).toBeInTheDocument();

    await user.click(within(screen.getByRole("table", { name: "Request results" })).getByRole("button", { name: "rag-002" }));
    const drawer = await screen.findByRole("dialog");
    const claims = await within(drawer).findByRole("table", { name: "Claims" });
    expect(claims).toHaveTextContent("Claims are due within 45 days.");
    expect(claims).toHaveTextContent("contradicted");
    expect(claims).toHaveTextContent("within 30 days of purchase");
    expect(claims).toHaveTextContent("policy-002 #c01 · p.1");
    expect(claims).toHaveTextContent("judge confidence 0.90");
  });
});
