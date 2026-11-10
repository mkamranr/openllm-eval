import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { ROUTER_FUTURE } from "../../app/routerFuture";
import { jsonResponse, makeDataset, makeEndpoint, makeProfile, makeRun, page, stubFetch } from "../../test/fakeApi";
import { DRAFT_KEY, NewEvaluationWizard } from "./NewEvaluationWizard";

const ENDPOINTS = [
  makeEndpoint({ id: "e1", name: "local-vllm", last_test_status: "ok" }),
  makeEndpoint({ id: "e2", name: "ollama", model_identifier: "llama3" }),
  makeEndpoint({ id: "e3", name: "disabled-one", enabled: false }),
];

function stub(profiles = [] as ReturnType<typeof makeProfile>[]) {
  return stubFetch((method, path, body) => {
    if (method === "POST" && path === "/runs") return jsonResponse(makeRun({ id: "run-9" }), 201);
    if (method === "POST" && path === "/profiles")
      return jsonResponse(makeProfile({ name: (body as { name: string }).name }), 201);
    if (path.startsWith("/profiles")) return jsonResponse(page(profiles));
    if (path.startsWith("/endpoints")) return jsonResponse(page(ENDPOINTS));
    if (path.startsWith("/datasets/samples")) return jsonResponse([]);
    if (path.startsWith("/datasets"))
      return jsonResponse(page([makeDataset(), makeDataset({ id: "d2", name: "Tools", dataset_type: "tool_call" })]));
    if (path.startsWith("/projects")) return jsonResponse(page([]));
    return undefined;
  });
}

const renderWizard = () =>
  render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={["/new"]}>
      <Routes>
        <Route path="/new" element={<NewEvaluationWizard />} />
        <Route path="/runs/:runId" element={<p>run page</p>} />
      </Routes>
    </MemoryRouter>,
  );

describe("NewEvaluationWizard", () => {
  beforeEach(() => window.localStorage.clear());

  it("walks through the four steps with real endpoints and datasets", async () => {
    stub();
    const user = userEvent.setup();
    renderWizard();

    expect(await screen.findByRole("heading", { name: /Basic Setup/ })).toBeInTheDocument();
    const next = () => screen.getByRole("button", { name: /^Next:/ });
    expect(next()).toBeDisabled();
    expect(screen.getByText("Add at least one model and an evaluation name.")).toBeInTheDocument();

    // Disabled endpoints are not offered.
    const picker = await screen.findByLabelText("Add model");
    expect(within(picker).queryByText(/disabled-one/)).not.toBeInTheDocument();
    await user.selectOptions(picker, "e1");
    await user.click(screen.getByRole("button", { name: "Add model" }));
    await user.selectOptions(screen.getByLabelText("Add model"), "e2");
    await user.click(screen.getByRole("button", { name: "Add model" }));
    await user.click(screen.getByRole("button", { name: "Remove ollama" }));
    await user.type(screen.getByLabelText("Evaluation name *"), "Prod benchmark");
    await user.click(next());

    expect(screen.getByRole("heading", { name: /Select Evaluation Types/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Tool-calling/ }));
    await user.click(screen.getByRole("button", { name: /Performance/ }));
    expect(screen.getByRole("button", { name: /Tool-calling/ })).toHaveAttribute("aria-pressed", "true");

    // Tool-calling shows quality settings; nothing is ever executed.
    const quality = screen.getByLabelText("Quality settings");
    expect(within(quality).getByRole("radio", { name: "Simulated results" })).toHaveAttribute("aria-checked", "true");
    expect(within(quality).getByText(/Nothing is ever executed/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Quantization/ }));

    // Run settings appear and are validated before moving on.
    const settings = screen.getByLabelText("Run settings");
    await user.click(within(settings).getByRole("radio", { name: "Smoke" }));
    const concurrency = within(settings).getByLabelText("Concurrency");
    await user.clear(concurrency);
    await user.type(concurrency, "8");
    expect(within(settings).getByText("Concurrency must be at most 2.")).toBeInTheDocument();
    expect(next()).toBeDisabled();
    await user.click(within(settings).getByRole("radio", { name: "Fixed concurrency" }));
    const requests = within(settings).getByLabelText("Measured requests per model");
    await user.type(requests, "20");
    await user.clear(within(settings).getByLabelText("Warm-up requests"));
    await user.type(within(settings).getByLabelText("Warm-up requests"), "1");
    await user.click(next());

    expect(screen.getByRole("heading", { name: /Select Datasets/ })).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: /My datasets/ }));
    const tools = screen.getByRole("button", { name: /Tools/ });
    expect(tools).toHaveTextContent("Suggested");
    await user.click(tools);
    await user.click(next());

    const summary = screen.getByLabelText("Evaluation summary");
    expect(summary).toHaveTextContent("Prod benchmark");
    expect(summary).toHaveTextContent("1 model");
    expect(summary).toHaveTextContent("Tool-calling, Performance, Quantization");
    expect(summary).toHaveTextContent("QualityTool-calling · tools simulated (fixtures)");
    expect(summary).toHaveTextContent("1 dataset · 12 cases");
    expect(screen.getByLabelText("Selected models")).toHaveTextContent("local-vllm");
    expect(summary).toHaveTextContent("Fixed concurrency: 20 requests at concurrency 8");
    expect(summary).toHaveTextContent("1 per model (excluded from metrics)");
    expect(screen.getByText(/Quantization is not a separate suite/)).toBeInTheDocument();
    expect(screen.queryByText(/arrives in a later milestone/)).not.toBeInTheDocument();

    // Stepper lets you jump back to a completed step.
    await user.click(screen.getByRole("button", { name: /Basic Setup/ }));
    expect(screen.getByLabelText("Evaluation name *")).toHaveValue("Prod benchmark");
  });

  it("starts a performance run and opens it", async () => {
    window.localStorage.setItem(
      DRAFT_KEY,
      JSON.stringify({
        step: 3,
        name: "Ready run",
        description: "",
        projectId: "",
        endpointIds: ["e1"],
        evaluationTypes: ["performance", "answer_quality", "rag"],
        datasetIds: ["d1"],
        quality: { f1Threshold: "0.6", judgeEndpointId: "e1", judgeConcurrency: "2" },
        performance: { profile: "ramp", rampSteps: "1, 2, 4", requestsPerStep: "5", stream: false, temperature: "" },
      }),
    );
    const { calls } = stub();
    const user = userEvent.setup();
    renderWizard();
    const startBtn = await screen.findByRole("button", { name: /Start Evaluation/ });
    await waitFor(() => expect(startBtn).toBeEnabled());
    await user.click(startBtn);
    expect(await screen.findByText("run page")).toBeInTheDocument();

    const post = calls.find((c) => c.method === "POST" && c.path === "/runs");
    expect(post?.body).toEqual({
      name: "Ready run",
      description: null,
      project_id: null,
      endpoint_ids: ["e1"],
      datasets: [{ dataset_id: "d1" }],
      suites: ["performance", "answer_quality", "rag"],
      generation: { temperature: null, max_tokens: 256 },
      load: {
        profile: "ramp",
        warmup_requests: 2,
        stream: false,
        request_timeout_seconds: null,
        max_retries: null,
        ramp_concurrency: [1, 2, 4],
        requests_per_step: 5,
      },
      evaluation: {
        tool_mode: "simulated",
        token_f1_threshold: 0.6,
        allow_code_fence: true,
        embedding: null,
        faithfulness_threshold: 0.8,
        support_threshold: 0.65,
        judge: { endpoint_id: "e1", concurrency: 2 },
      },
      profile_id: null,
    });
    // The identifying fields reset; the performance settings are kept for next time.
    const saved = JSON.parse(window.localStorage.getItem(DRAFT_KEY)!);
    expect(saved.name).toBe("");
    expect(saved.performance.profile).toBe("ramp");
    expect(saved.quality.f1Threshold).toBe("0.6");
  });

  it("starts from a profile and saves settings as a new profile", async () => {
    window.localStorage.setItem(
      DRAFT_KEY,
      JSON.stringify({ step: 1, name: "From profile", endpointIds: ["e1"], evaluationTypes: [], datasetIds: ["d1"] }),
    );
    const { calls } = stub([makeProfile()]);
    const user = userEvent.setup();
    renderWizard();
    await user.selectOptions(await screen.findByLabelText("Start from a profile"), "pf1");
    expect(screen.getByRole("button", { name: /Answer quality/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Token F1 pass threshold")).toHaveValue(0.7);
    const settings = screen.getByLabelText("Run settings");
    expect(within(settings).getByRole("radio", { name: "Fixed concurrency" })).toHaveAttribute("aria-checked", "true");
    expect(within(settings).getByLabelText("Concurrency")).toHaveValue(4);

    await user.click(screen.getByRole("button", { name: /^Next:/ }));
    await user.click(screen.getByRole("button", { name: /^Next:/ }));
    await user.type(screen.getByLabelText("Save these settings as a profile"), "Copy");
    await user.click(screen.getByRole("button", { name: "Save as profile" }));
    expect(await screen.findByText('Saved profile "Copy".')).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST" && c.path === "/profiles")?.body as Record<string, unknown>;
    expect(post).toMatchObject({ name: "Copy", suites: ["performance", "answer_quality"] });
    expect(post.load).toMatchObject({ profile: "fixed_concurrency", concurrency: 4, requests: 40 });

    await user.click(screen.getByRole("button", { name: /Start Evaluation/ }));
    await screen.findByText("run page");
    expect(calls.find((c) => c.method === "POST" && c.path === "/runs")?.body).toMatchObject({ profile_id: "pf1" });
  });

  it("offers an optional LLM judge for RAG and validates its settings", async () => {
    window.localStorage.setItem(
      DRAFT_KEY,
      JSON.stringify({ step: 1, name: "RAG", endpointIds: ["e1"], evaluationTypes: ["rag"], datasetIds: [] }),
    );
    stub();
    const user = userEvent.setup();
    renderWizard();
    const quality = await screen.findByLabelText("Quality settings");
    expect(within(quality).getByLabelText("LLM judge (optional)")).toHaveValue("");
    expect(within(quality).getByLabelText("Lexical support threshold")).toHaveValue(0.65);
    expect(within(quality).queryByText(/sent to the judge endpoint/)).not.toBeInTheDocument();
    await user.selectOptions(within(quality).getByLabelText("LLM judge (optional)"), "e1");
    expect(within(quality).getByText(/sent to the judge endpoint/)).toBeInTheDocument();
    const concurrency = within(quality).getByLabelText("Judge concurrency");
    await user.clear(concurrency);
    await user.type(concurrency, "99");
    expect(within(quality).getByText("Use a whole number from 1 to 32.")).toBeInTheDocument();
    await user.clear(within(quality).getByLabelText("Faithfulness pass threshold"));
    expect(within(quality).getAllByText("Use a value between 0 and 1.").length).toBe(1);
  });

  it("cannot start with only quantization (compared across runs)", async () => {
    window.localStorage.setItem(
      DRAFT_KEY,
      JSON.stringify({ step: 3, name: "Later only", endpointIds: ["e1"], evaluationTypes: ["quantization"], datasetIds: ["d1"] }),
    );
    stub();
    renderWizard();
    expect(await screen.findByText(/None of the selected evaluation types can run/)).toBeInTheDocument();
    expect(screen.getByText(/Quantization is not a separate suite/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start Evaluation/ })).toBeDisabled();
  });

  it("restores the draft from storage and drops deleted selections", async () => {
    window.localStorage.setItem(
      DRAFT_KEY,
      JSON.stringify({
        step: 3,
        name: "Saved draft",
        description: "",
        projectId: "",
        endpointIds: ["e2", "gone"],
        evaluationTypes: ["performance"],
        datasetIds: ["d1"],
      }),
    );
    stub();
    renderWizard();
    const summary = await screen.findByLabelText("Evaluation summary");
    expect(summary).toHaveTextContent("Saved draft");
    expect(await screen.findByText(/have not passed a connection test|has not passed a connection test/)).toHaveTextContent(
      "ollama",
    );
    expect(summary).toHaveTextContent("1 model");
  });

  it("start over clears the draft", async () => {
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify({ name: "Old", step: 0 }));
    stub();
    const user = userEvent.setup();
    renderWizard();
    expect(await screen.findByLabelText("Evaluation name *")).toHaveValue("Old");
    await user.click(screen.getByRole("button", { name: /Start over/ }));
    expect(screen.getByLabelText("Evaluation name *")).toHaveValue("");
    expect(JSON.parse(window.localStorage.getItem(DRAFT_KEY)!).name).toBe("");
  });
});
