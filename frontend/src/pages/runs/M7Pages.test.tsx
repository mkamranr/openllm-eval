import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ROUTER_FUTURE } from "../../app/routerFuture";
import { jsonResponse, makeDataset, makeMetric, makeResult, makeRun, page, stubFetch } from "../../test/fakeApi";
import type { ComparisonResult } from "../../types/reports";
import type { MetricValue } from "../../types/runs";
import { ComparePage } from "../compare/ComparePage";
import { DatasetsPage } from "../datasets/DatasetsPage";
import { RunDetailPage } from "./RunDetailPage";

function renderAt(path: string) {
  return render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={[path]}>
      <Routes>
        <Route path="/runs/:runId" element={<RunDetailPage />} />
        <Route path="/compare" element={<ComparePage />} />
        <Route path="/datasets" element={<DatasetsPage />} />
        <Route path="/datasets/:id" element={<p>dataset detail</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const ctx = (length: number, name: string, agg: string, value: number | null, metadata: Record<string, unknown> = {}): MetricValue =>
  makeMetric(name, agg, value, { context_length: length, metadata });

const CONTEXT_METRICS: MetricValue[] = [
  ctx(1024, "context.requests", "count", 3),
  ctx(1024, "context.success_rate", "ratio", 1),
  ctx(1024, "context.retrieval_accuracy", "pass_rate", 2 / 3),
  ctx(1024, "context.retrieval_accuracy", "late", 0),
  ctx(1024, "context.input_tokens", "mean", 790, { token_count_source: { server_reported: 3 } }),
  ctx(1024, "context.estimated_input_tokens", "mean", 1030),
  ctx(4096, "context.requests", "count", 3),
  ctx(4096, "context.success_rate", "ratio", 0),
  ctx(4096, "context.overflow_rate", "ratio", 1),
  ctx(4096, "context.retrieval_accuracy", "pass_rate", null, { unavailable_reason: "no completed needle_retrieval evaluation" }),
];

describe("Context window and telemetry on the run page", () => {
  it("shows context results by requested length and GPU telemetry", async () => {
    const run = makeRun({ suites: ["context_window"] });
    const items = [
      makeMetric("latency_ms", "p95", 640),
      // Same name in the overall table must not be overwritten by per-length rows.
      makeMetric("requests", "succeeded", 3),
      ...CONTEXT_METRICS,
      makeMetric("telemetry.samples", "count", 12, { metadata: { failures: 1, last_error: "timeout" } }),
      makeMetric("gpu.memory_used_mb", "max", 23552),
      makeMetric("gpu.power_w", "mean", null, { metadata: { unavailable_reason: "the exporter did not report gpu_power_w" } }),
    ];
    stubFetch((_method, path) => {
      if (path === "/runs/r1") return jsonResponse(run);
      if (path === "/runs/r1/metrics") return jsonResponse({ run_id: "r1", live: false, metrics_version: "1", items });
      if (path === "/runs/r1/telemetry")
        return jsonResponse({
          run_id: "r1",
          status: {},
          truncated: false,
          items: [0, 2, 4].map((t) => ({ run_model_id: "rm1", captured_at: "", offset_seconds: t, source: "prometheus", values: { gpu_memory_used_mb: 20000 + t * 500 } })),
        });
      if (path.startsWith("/runs/r1/results?")) return jsonResponse(page([makeResult()]));
      if (path.startsWith("/runs/r1/reports")) return jsonResponse(page([]));
      return undefined;
    });
    renderAt("/runs/r1");

    const table = await screen.findByRole("table", { name: "Context window results for local-vllm" });
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("1,024");
    expect(rows[1]).toHaveTextContent("66.7%");
    expect(rows[1]).toHaveTextContent("server reported: 3");
    expect(rows[2]).toHaveTextContent("4,096");
    expect(rows[2]).toHaveTextContent("100.0%");
    expect(within(rows[2]!).getByTitle(/no completed needle_retrieval evaluation/)).toHaveTextContent("—");

    const resources = screen.getByRole("table", { name: "Resource telemetry" });
    expect(resources).toHaveTextContent("23,552 MB");
    expect(resources).toHaveTextContent("(1 failed)");
    expect(within(resources).getByTitle(/did not report gpu_power_w/)).toHaveTextContent("—");
    expect(await screen.findByRole("img", { name: "GPU memory used over time for local-vllm" })).toBeInTheDocument();
  });

  it("hides the cards when there is nothing to show", async () => {
    stubFetch((_method, path) => {
      if (path === "/runs/r1") return jsonResponse(makeRun());
      if (path === "/runs/r1/metrics") return jsonResponse({ run_id: "r1", live: false, metrics_version: "1", items: [makeMetric("latency_ms", "p95", 640)] });
      if (path.startsWith("/runs/r1/results?")) return jsonResponse(page([makeResult()]));
      if (path.startsWith("/runs/r1/reports")) return jsonResponse(page([]));
      return undefined;
    });
    renderAt("/runs/r1");
    expect(await screen.findByRole("heading", { name: "Performance metrics" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Context window" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Resource telemetry" })).not.toBeInTheDocument();
  });
});

describe("Generate a context-window dataset", () => {
  it("sends the generator parameters and opens the new dataset", async () => {
    const { calls } = stubFetch((method, path) => {
      if (path.startsWith("/datasets?")) return jsonResponse(page([]));
      if (path === "/datasets/samples") return jsonResponse([]);
      if (path.startsWith("/projects")) return jsonResponse(page([]));
      if (method === "POST" && path === "/datasets/generate") return jsonResponse(makeDataset({ id: "d9" }), 201);
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/datasets?tab=generate");

    expect(await screen.findByText(/9 cases, about 64,512 tokens in total/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Name *"), "needles");
    const lengths = screen.getByLabelText("Requested context lengths (tokens)");
    await user.clear(lengths);
    await user.type(lengths, "2048, 8192");
    await user.clear(screen.getByLabelText("Facts per case"));
    await user.type(screen.getByLabelText("Facts per case"), "3");
    expect(screen.getByLabelText("Needle depths")).toBeDisabled();
    expect(screen.getByText(/2 cases, about 10,240 tokens/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Generate dataset" }));

    expect(await screen.findByText("dataset detail")).toBeInTheDocument();
    const body = calls.find((c) => c.method === "POST")?.body as { generator: string; params: Record<string, unknown> };
    expect(body.generator).toBe("needle");
    expect(body.params).toMatchObject({ context_lengths: [2048, 8192], needles_per_case: 3, repeats: 1, seed: 0 });
  });
});

const VARIANTS: ComparisonResult = {
  run_ids: ["r1", "r2"],
  baseline_subject_id: "m1",
  subjects: [
    { id: "m1", run_id: "r1", run_name: "fp16", run_status: "completed", run_completed_at: null, model_name: "llama", label: "fp16", index: 0, config: {} },
    { id: "m2", run_id: "r2", run_name: "awq", run_status: "completed", run_completed_at: null, model_name: "llama", label: "awq", index: 1, config: {} },
  ],
  comparability: { overall: "equivalent_configuration", performance: "equivalent_configuration", quality: "equivalent_configuration", differences: [] },
  metrics: [],
  transitions: [],
  variants: {
    fields: [
      { key: "quantization_method", label: "Quantization method", group: "model", values: { m1: null, m2: "AWQ" }, differs: true, unknown_for: ["m1"], all_known: false },
      { key: "weight_bits", label: "Weight bits", group: "model", values: { m1: 16, m2: 4 }, differs: true, unknown_for: [], all_known: true },
    ],
    warnings: [{ level: "warning", message: "Quantization is unknown for fp16 (not declared; it is never guessed from the model name)." }],
    key_metrics: [
      {
        label: "GPU memory peak", metric_name: "gpu.memory_used_mb", aggregation: "max", unit: "MB", family: "resources", evaluator: null, better: "lower",
        values: [
          { subject_id: "m1", value: 16000, sample_count: 5, unavailable_reason: null, delta: null, relative_change: null, improved: null },
          { subject_id: "m2", value: null, sample_count: null, unavailable_reason: "telemetry was configured but no sample was collected", delta: null, relative_change: null, improved: null },
        ],
      },
    ],
    context_lengths: [1024],
    context: [
      { context_length: 1024, label: "Retrieval accuracy", metric_name: "context.retrieval_accuracy", aggregation: "pass_rate", values: { m1: { value: 1, unavailable_reason: null }, m2: { value: 0.5, unavailable_reason: null } } },
    ],
  },
  notes: [],
};

describe("Deployment variants on Compare", () => {
  it("shows declared settings with unknowns, warnings, and per-length context results", async () => {
    stubFetch((_method, path) => {
      if (path.startsWith("/runs?")) return jsonResponse(page([makeRun({ id: "r1", status: "completed" }), makeRun({ id: "r2", status: "completed" })]));
      if (path.startsWith("/compare?")) return jsonResponse(VARIANTS);
      if (path.startsWith("/comparisons")) return jsonResponse(page([]));
      return undefined;
    });
    renderAt("/compare?runs=r1,r2");

    const settings = await screen.findByRole("table", { name: "Declared variant settings" });
    const quant = within(settings).getByText("Quantization method").closest("tr")!;
    expect(quant).toHaveTextContent("unknown");
    expect(quant).toHaveTextContent("AWQ");
    expect(screen.getByText(/never guessed from the model name/)).toBeInTheDocument();
    const key = screen.getByRole("table", { name: "Variant key metrics" });
    expect(key).toHaveTextContent("16,000 MB");
    expect(within(key).getByTitle(/no sample was collected/)).toHaveTextContent("—");
    const context = screen.getByRole("table", { name: "Context window comparison" });
    await waitFor(() => expect(context).toHaveTextContent("50.0%"));
  });
});
