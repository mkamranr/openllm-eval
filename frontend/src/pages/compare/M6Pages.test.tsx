import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ROUTER_FUTURE } from "../../app/routerFuture";
import { jsonResponse, makeMetric, makeResult, makeRun, makeRunModel, page, stubFetch } from "../../test/fakeApi";
import type { ComparisonResult, Report } from "../../types/reports";
import { ReportsPage } from "../reports/ReportsPage";
import { RunDetailPage } from "../runs/RunDetailPage";
import { ComparePage } from "./ComparePage";

function renderAt(path: string) {
  return render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={[path]}>
      <Routes>
        <Route path="/runs/:runId" element={<RunDetailPage />} />
        <Route path="/compare" element={<ComparePage />} />
        <Route path="/reports" element={<ReportsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function makeReport(overrides: Partial<Report> = {}): Report {
  return {
    id: "rep1",
    run_id: "r1",
    run_name: "Prod benchmark",
    report_type: "html",
    storage_location: "database",
    filename: "Prod-benchmark-r1.html",
    media_type: "text/html; charset=utf-8",
    size_bytes: 20480,
    sha256: "ab".repeat(32),
    options: { redact_content: false, redact_endpoints: false },
    report_metadata: { results_total: 10, results_included: 10, results_truncated: false },
    generated_at: "2026-11-10T10:00:00Z",
    ...overrides,
  };
}

function runStubs(run = makeRun(), reports: Report[] = []) {
  return (method: string, path: string) => {
    if (method === "GET" && path === `/runs/${run.id}`) return jsonResponse(run);
    if (path === `/runs/${run.id}/metrics`) return jsonResponse({ run_id: run.id, live: false, metrics_version: "1", items: [makeMetric("latency_ms", "p95", 640)] });
    if (path.startsWith(`/runs/${run.id}/results?`)) return jsonResponse(page([makeResult()]));
    if (method === "GET" && path.startsWith(`/runs/${run.id}/reports`)) return jsonResponse(page(reports));
    return undefined;
  };
}

describe("Run reports and re-run", () => {
  it("generates a report with redaction options and lists it with download links", async () => {
    const created = makeReport({ id: "rep2", report_type: "markdown", filename: "Prod-benchmark-r1.md", options: { redact_content: true, redact_endpoints: false } });
    const { calls } = stubFetch(runStubs(makeRun(), [makeReport()]), (method, path) =>
      method === "POST" && path === "/runs/r1/reports" ? jsonResponse(created, 201) : undefined,
    );
    const user = userEvent.setup();
    renderAt("/runs/r1");

    const table = await screen.findByRole("table", { name: "Generated reports" });
    expect(within(table).getByText("HTML report")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Open Prod-benchmark-r1.html" })).toHaveAttribute(
      "href", "/api/v1/reports/rep1/download?inline=true",
    );
    expect(within(table).getByRole("link", { name: "Download Prod-benchmark-r1.html" })).toHaveAttribute(
      "href", "/api/v1/reports/rep1/download",
    );

    await user.selectOptions(screen.getByLabelText("Format"), "markdown");
    await user.click(screen.getByRole("switch", { name: "Leave out generated text" }));
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(await within(table).findByText("Markdown summary")).toBeInTheDocument();
    expect(within(table).getByText("text left out")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ format: "markdown", redact_content: true, redact_endpoints: false });
  });

  it("explains that reports need a finished run", async () => {
    const run = makeRun({ status: "running", completed_at: null });
    stubFetch(runStubs(run));
    renderAt("/runs/r1");
    expect(await screen.findByText("Reports can be generated once the run has finished.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Re-run" })).not.toBeInTheDocument();
  });

  it("re-runs with the same configuration and opens the new run", async () => {
    const again = makeRun({ id: "r9", name: "Prod benchmark (re-run)", status: "queued", completed_at: null });
    stubFetch(runStubs(), runStubs(again), (method, path) =>
      method === "POST" && path === "/runs/r1/rerun" ? jsonResponse(again, 201) : undefined,
    );
    const user = userEvent.setup();
    renderAt("/runs/r1");
    expect(await screen.findByRole("link", { name: /Compare/ })).toHaveAttribute("href", "/compare?runs=r1");
    await user.click(screen.getByRole("button", { name: "Re-run" }));
    expect(await screen.findByRole("heading", { level: 1, name: /Prod benchmark \(re-run\)/ })).toBeInTheDocument();
  });

  it("filters quality failures by evaluator", async () => {
    const run = makeRun({
      configuration_snapshot: {
        ...makeRun().configuration_snapshot,
        evaluation: {
          suites: ["answer_quality"],
          evaluators: [
            { name: "exact_match", version: "1.0.0", suite: "answer_quality", kind: "objective" },
            { name: "token_f1", version: "1.0.0", suite: "answer_quality", kind: "objective" },
          ],
          config: {},
        },
      } as never,
    });
    const { calls } = stubFetch(runStubs(run), (_m, path) => (path === "/evaluators" ? jsonResponse([]) : undefined));
    const user = userEvent.setup();
    renderAt("/runs/r1");
    await user.click(await screen.findByRole("tab", { name: "Quality failures" }));
    await user.selectOptions(await screen.findByLabelText("Filter by evaluator"), "token_f1");
    await waitFor(() =>
      expect(calls.some((c) => c.path.includes("evaluation=failed") && c.path.includes("evaluator=token_f1"))).toBe(true),
    );
  });
});

const COMPARISON: ComparisonResult = {
  run_ids: ["r1", "r2"],
  baseline_subject_id: "m1",
  subjects: [
    { id: "m1", run_id: "r1", run_name: "baseline", run_status: "completed", run_completed_at: null, model_name: "llama", label: "baseline · llama", index: 0, config: {} },
    { id: "m2", run_id: "r2", run_name: "candidate", run_status: "completed", run_completed_at: null, model_name: "qwen", label: "candidate · qwen", index: 1, config: {} },
  ],
  comparability: {
    overall: "partially_controlled",
    performance: "partially_controlled",
    quality: "equivalent_configuration",
    differences: [
      { field: "concurrency", label: "Concurrency", aspect: "performance", severity: "major", values: { m1: 1, m2: 8 } },
      { field: "model_identifier", label: "Model identifier", aspect: "subject", severity: "info", values: { m1: "llama", m2: "qwen" } },
    ],
  },
  metrics: [
    {
      metric_name: "latency_ms", aggregation: "p50", unit: "ms", family: "performance", evaluator: null, better: "lower",
      values: [
        { subject_id: "m1", value: 100, sample_count: 10, unavailable_reason: null, delta: null, relative_change: null, improved: null },
        { subject_id: "m2", value: 150, sample_count: 10, unavailable_reason: null, delta: 50, relative_change: 0.5, improved: false },
      ],
    },
    {
      metric_name: "quality.exact_match", aggregation: "pass_rate", unit: "ratio", family: "quality", evaluator: "exact_match", better: "higher",
      values: [
        { subject_id: "m1", value: 0.5, sample_count: 10, unavailable_reason: null, delta: null, relative_change: null, improved: null },
        { subject_id: "m2", value: null, sample_count: null, unavailable_reason: "not measured in this run", delta: null, relative_change: null, improved: null },
      ],
    },
  ],
  transitions: [
    {
      subject_id: "m2", shared_cases: 10, only_in_baseline: 0, only_in_subject: 0, datasets_identical: true,
      new_errors_total: 1, new_errors: [{ case_identifier: "qa-3", error_type: "timeout" }], fixed_errors_total: 0, fixed_errors: [],
      evaluators: { exact_match: { pass_to_fail_total: 2, pass_to_fail: ["qa-1", "qa-2"], fail_to_pass_total: 0, fail_to_pass: [] } },
    },
  ],
  notes: ["Deltas are differences from the baseline. No statistical significance test is applied."],
};

describe("ComparePage", () => {
  it("shows comparability, differences, deltas, and per-case regressions", async () => {
    const { calls } = stubFetch((method, path) => {
      if (path.startsWith("/runs?")) return jsonResponse(page([makeRun({ id: "r1", name: "baseline", status: "completed" }), makeRun({ id: "r2", name: "candidate", status: "completed" })]));
      if (path.startsWith("/compare?")) return jsonResponse(COMPARISON);
      if (path.startsWith("/comparisons") && method === "GET") return jsonResponse(page([]));
      if (path === "/comparisons" && method === "POST") return jsonResponse({ id: "c1", name: "nightly", run_ids: ["r1", "r2"] }, 201);
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/compare?runs=r1,r2");

    expect(await screen.findByText("Partially controlled")).toBeInTheDocument();
    expect(screen.getByText("Performance: Partially controlled")).toBeInTheDocument();
    const diffs = screen.getByRole("table", { name: "Configuration differences" });
    expect(within(diffs).getByText("Concurrency")).toBeInTheDocument();
    expect(within(diffs).queryByText("Model identifier")).not.toBeInTheDocument();
    await user.click(screen.getByRole("switch", { name: /expected differences/ }));
    expect(within(diffs).getByText("Model identifier")).toBeInTheDocument();

    const metrics = screen.getByRole("table", { name: "Compared metrics" });
    expect(within(metrics).getByText("150 ms")).toBeInTheDocument();
    expect(within(metrics).getByText("+50 ms (+50%) · worse")).toBeInTheDocument();
    expect(within(metrics).getByTitle("not measured in this run")).toHaveTextContent("—");
    await user.click(screen.getByRole("button", { name: "Quality" }));
    expect(within(metrics).queryByText("150 ms")).not.toBeInTheDocument();

    const changes = screen.getByRole("region", { name: "Changes: candidate · qwen" });
    expect(changes).toHaveTextContent("2 pass→fail");
    expect(changes).toHaveTextContent("qa-1, qa-2");
    expect(changes).toHaveTextContent("1 new request errors");

    expect(screen.getByRole("link", { name: /HTML/ })).toHaveAttribute("href", "/api/v1/compare/export?run_ids=r1%2Cr2&format=html");
    await user.type(screen.getByLabelText("Comparison name"), "nightly");
    await user.click(screen.getByRole("button", { name: /Save/ }));
    expect(await screen.findByText("Comparison saved.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "nightly", run_ids: ["r1", "r2"], baseline: null });

    await user.selectOptions(screen.getByLabelText("Baseline"), "m2");
    await waitFor(() => expect(calls.some((c) => c.path.includes("baseline=m2"))).toBe(true));
  });

  it("starts empty and adds runs from the picker", async () => {
    const { calls } = stubFetch((_m, path) => {
      if (path.startsWith("/runs?")) return jsonResponse(page([makeRun({ id: "r1", name: "baseline", status: "completed", models: [makeRunModel()] })]));
      if (path.startsWith("/comparisons")) return jsonResponse(page([]));
      if (path.startsWith("/compare?")) return jsonResponse({ ...COMPARISON, subjects: [COMPARISON.subjects[0]], transitions: [] });
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/compare");
    expect(await screen.findByText("Pick runs to compare")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("option", { name: /baseline/ })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText("Runs to compare"), "r1");
    expect(await screen.findByText(/Only one model selected/)).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/compare?run_ids=r1")).toBe(true);
  });
});

describe("ReportsPage", () => {
  it("lists reports across runs and deletes one", async () => {
    let items = [makeReport(), makeReport({ id: "rep3", run_name: null, report_type: "csv", filename: "x.csv", report_metadata: { results_truncated: true } })];
    stubFetch((method, path) => {
      if (method === "DELETE" && path === "/reports/rep3") {
        items = items.filter((r) => r.id !== "rep3");
        return jsonResponse(null, 204);
      }
      if (path.startsWith("/reports?")) return jsonResponse(page(items));
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/reports");
    const table = await screen.findByRole("table", { name: "Reports" });
    expect(within(table).getByRole("link", { name: "Prod benchmark" })).toHaveAttribute("href", "/runs/r1");
    expect(within(table).getByText("deleted run")).toBeInTheDocument();
    expect(within(table).getByText("truncated")).toBeInTheDocument();
    await user.click(within(table).getByRole("button", { name: "Delete x.csv" }));
    await waitFor(() => expect(within(table).queryByText("x.csv")).not.toBeInTheDocument());
  });

  it("shows an empty state", async () => {
    stubFetch((_m, path) => (path.startsWith("/reports?") ? jsonResponse(page([])) : undefined));
    renderAt("/reports");
    expect(await screen.findByText("No reports yet")).toBeInTheDocument();
  });
});
