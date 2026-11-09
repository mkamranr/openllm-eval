import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ROUTER_FUTURE } from "../../app/routerFuture";
import {
  ADAPTERS,
  jsonResponse,
  makeEndpoint,
  makeProject,
  page,
  stubFetch,
  type Handler,
} from "../../test/fakeApi";
import type { Endpoint, EndpointTestResult } from "../../types/api";
import { EndpointsPage } from "./EndpointsPage";

function baseHandlers(endpoints: Endpoint[]): Handler {
  return (method, path) => {
    if (method === "GET" && path.startsWith("/endpoints")) return jsonResponse(page(endpoints));
    if (method === "GET" && path.startsWith("/projects")) return jsonResponse(page([makeProject()]));
    if (method === "GET" && path === "/adapters") return jsonResponse(ADAPTERS);
    return undefined;
  };
}

const renderPage = (url = "/endpoints") =>
  render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={[url]}>
      <EndpointsPage />
    </MemoryRouter>,
  );

const TEST_RESULT: EndpointTestResult = {
  endpoint_id: "e1",
  tested_at: "2026-11-09T10:00:00Z",
  ok: true,
  models_check: {
    ok: true,
    latency_ms: 4,
    http_status: 200,
    available_models: ["Qwen/Qwen2.5-7B-Instruct"],
    model_listed: true,
    error: null,
  },
  response: {
    request_id: "r1",
    response_id: "c1",
    model: "Qwen/Qwen2.5-7B-Instruct",
    content: "ok",
    reasoning_content: null,
    tool_calls: [],
    finish_reason: "stop",
    usage: { input_tokens: 12, output_tokens: 1, total_tokens: 13, source: "server_reported" },
    http_status: 200,
    timing: {
      streamed: true,
      started_at: "2026-11-09T10:00:00Z",
      first_content_at: "2026-11-09T10:00:00.085Z",
      ended_at: "2026-11-09T10:00:00.120Z",
      ttft_ms: 85,
      latency_ms: 120,
      generation_ms: 35,
    },
    error: null,
    warnings: [],
  },
  network_policy: { allow_private_networks: true, allowlist: [], blocked_hosts: ["postgres", "redis"] },
};

describe("EndpointsPage", () => {
  it("shows an empty state with a mock-server hint", async () => {
    stubFetch(baseHandlers([]));
    renderPage();
    expect(await screen.findByText("No endpoints yet")).toBeInTheDocument();
    expect(screen.getByText(/mock-llm:9000/)).toBeInTheDocument();
  });

  it("lists endpoints with status, warnings and filtering", async () => {
    stubFetch(
      baseHandlers([
        makeEndpoint({ warnings: ["TLS verification is disabled."] }),
        makeEndpoint({ id: "e2", name: "ollama", model_identifier: "llama3", enabled: false, tags: [] }),
      ]),
    );
    const user = userEvent.setup();
    renderPage();

    const table = await screen.findByRole("table", { name: "Endpoints" });
    expect(within(table).getByText("local-vllm")).toBeInTheDocument();
    expect(within(table).getByLabelText(/Warnings: TLS verification/)).toBeInTheDocument();
    expect(within(table).getAllByText("Untested")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Disabled" }));
    expect(within(table).queryByText("local-vllm")).not.toBeInTheDocument();
    expect(within(table).getByText("ollama")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "All" }));
    await user.type(screen.getByLabelText("Search endpoints"), "qwen");
    expect(within(table).getByText("local-vllm")).toBeInTheDocument();
    expect(within(table).queryByText("ollama")).not.toBeInTheDocument();
  });

  it("runs a connection test and shows real metrics", async () => {
    const { calls } = stubFetch(baseHandlers([makeEndpoint()]), (method, path) =>
      method === "POST" && path === "/endpoints/e1/test" ? jsonResponse(TEST_RESULT) : undefined,
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Test local-vllm" }));
    const dialog = await screen.findByRole("dialog", { name: "Connection test: local-vllm" });
    expect(await within(dialog).findByText("Endpoint responded successfully.")).toBeInTheDocument();
    const metrics = within(dialog).getByLabelText("Test metrics");
    expect(metrics).toHaveTextContent("120 ms");
    expect(metrics).toHaveTextContent("85 ms");
    expect(metrics).toHaveTextContent("12 / 1");
    expect(within(dialog).getByText(/Model is listed/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/endpoints/e1/test")).toBe(true);
  });

  it("shows a failed test with the adapter error", async () => {
    const failed: EndpointTestResult = {
      ...TEST_RESULT,
      ok: false,
      response: {
        ...TEST_RESULT.response,
        content: null,
        usage: { input_tokens: null, output_tokens: null, total_tokens: null, source: "unavailable" },
        timing: null,
        http_status: 401,
        error: { type: "http_status", message: "HTTP 401 Unauthorized", status_code: 401, retryable: false },
      },
    };
    stubFetch(baseHandlers([makeEndpoint()]), (method, path) =>
      method === "POST" && path === "/endpoints/e1/test" ? jsonResponse(failed) : undefined,
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Test local-vllm" }));
    const alert = await screen.findByText(/Test failed: HTTP 401/);
    expect(alert).toBeInTheDocument();
    expect(screen.getByText("HTTP 401 Unauthorized")).toBeInTheDocument();
    expect(screen.getByLabelText("Test metrics")).toHaveTextContent("unavailable");
  });

  it("toggles enabled, clones and deletes after confirmation", async () => {
    const { calls } = stubFetch(baseHandlers([makeEndpoint()]), (method, path) => {
      if (method === "PATCH" && path === "/endpoints/e1") return jsonResponse(makeEndpoint({ enabled: false }));
      if (method === "POST" && path === "/endpoints/e1/clone")
        return jsonResponse(makeEndpoint({ id: "e3", name: "local-vllm (copy)" }), 201);
      if (method === "DELETE" && path === "/endpoints/e1") return jsonResponse(null, 204);
      return undefined;
    });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("switch", { name: "Enable local-vllm" }));
    await waitFor(() =>
      expect(calls).toContainEqual({ method: "PATCH", path: "/endpoints/e1", body: { enabled: false } }),
    );

    await user.click(screen.getByRole("button", { name: "Clone local-vllm" }));
    expect(await screen.findByText("Cloned local-vllm.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Delete local-vllm" }));
    const confirm = screen.getByRole("alertdialog", { name: "Delete local-vllm?" });
    await user.click(within(confirm).getByRole("button", { name: "Delete endpoint" }));
    expect(await screen.findByText("Deleted local-vllm.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE")).toBe(true);
  });

  it("opens the create form from ?new=1", async () => {
    stubFetch(baseHandlers([]));
    renderPage("/endpoints?new=1");
    expect(await screen.findByRole("dialog", { name: "Add model endpoint" })).toBeInTheDocument();
  });
});
