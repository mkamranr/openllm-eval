import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ROUTER_FUTURE } from "../../app/routerFuture";
import { jsonResponse, makeEndpoint, makeProject, makeRun, page, stubFetch } from "../../test/fakeApi";
import type { Endpoint } from "../../types/api";
import type { ReadinessResponse } from "../../types/health";
import { Dashboard } from "./Dashboard";

const VERSION = { name: "openllm-eval", version: "0.1.0", api_version: "v1", python_version: "3.12.0" };

function mockApi(readiness: ReadinessResponse, readyStatus = 200, endpoints: Endpoint[] = []) {
  return stubFetch((_method, path) => {
    if (path === "/health/ready") return jsonResponse(readiness, readyStatus);
    if (path === "/version") return jsonResponse(VERSION);
    if (path.startsWith("/endpoints")) return jsonResponse(page(endpoints));
    if (path.startsWith("/projects")) return jsonResponse(page([makeProject()]));
    if (path.startsWith("/runs")) return jsonResponse(page([makeRun()]));
    return undefined;
  });
}

const renderDashboard = () =>
  render(
    <MemoryRouter future={ROUTER_FUTURE}>
      <Dashboard />
    </MemoryRouter>,
  );

describe("Dashboard", () => {
  it("shows healthy components reported by the API", async () => {
    const { fetchMock } = mockApi({
      status: "ok",
      database: { status: "ok", latency_ms: 1.23 },
      redis: { status: "ok", latency_ms: 0.4 },
      worker: { status: "ok", active_workers: 2 },
    });
    renderDashboard();

    await screen.findByLabelText("System health");
    expect(within(screen.getByTestId("health-api")).getByText("v0.1.0")).toBeInTheDocument();
    expect(within(screen.getByTestId("health-database")).getByText("OK")).toBeInTheDocument();
    expect(within(screen.getByTestId("health-database")).getByText("1.2 ms")).toBeInTheDocument();
    expect(within(screen.getByTestId("health-worker")).getByText("2 active")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "" })).toHaveTextContent("System healthy");
    expect(screen.queryByText(/not ready/)).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/health/ready", expect.anything());
  });

  it("renders a degraded (503) readiness response as data, not an error", async () => {
    mockApi(
      {
        status: "degraded",
        database: { status: "error", detail: "OperationalError" },
        redis: { status: "ok", latency_ms: 0.5 },
        worker: { status: "unavailable", active_workers: 0, detail: "no worker heartbeat" },
      },
      503,
    );
    renderDashboard();

    expect(await screen.findByText("OperationalError")).toBeInTheDocument();
    expect(within(screen.getByTestId("health-database")).getByText("Error")).toBeInTheDocument();
    expect(within(screen.getByTestId("health-worker")).getByText("no worker heartbeat")).toBeInTheDocument();
    expect(screen.getByText(/not ready/)).toBeInTheDocument();
    expect(screen.getByText("System degraded")).toBeInTheDocument();
  });

  it("summarises real endpoint data and leaves unknown values blank", async () => {
    mockApi({ status: "ok", database: { status: "ok" }, redis: { status: "ok" }, worker: { status: "ok", active_workers: 1 } }, 200, [
      makeEndpoint({
        last_test_status: "ok",
        last_tested_at: new Date().toISOString(),
        last_test_summary: { ok: true, error_type: null, error_message: null, latency_ms: 420, ttft_ms: 85, streamed: true },
      }),
      makeEndpoint({ id: "e2", name: "llama-cpp", enabled: false, deployment_metadata: {} }),
    ]);
    renderDashboard();

    const table = await screen.findByRole("table", { name: "Model endpoints" });
    expect(within(table).getByText("local-vllm")).toBeInTheDocument();
    expect(within(table).getByText("RTX 4090")).toBeInTheDocument();
    expect(within(table).getByText("420 ms · TTFT 85 ms")).toBeInTheDocument();
    expect(within(table).getByText("Untested")).toBeInTheDocument();
    expect(screen.getByText("1 enabled")).toBeInTheDocument();
    expect(screen.getByText("1/1")).toBeInTheDocument();
    expect(screen.getByText("85 ms")).toBeInTheDocument();
    const recent = await screen.findByRole("table", { name: "Recent evaluations" });
    expect(within(recent).getByText("Prod benchmark")).toBeInTheDocument();
    expect(within(recent).getByText("Completed with errors")).toBeInTheDocument();
  });

  it("reports an unreachable API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    renderDashboard();

    expect(await screen.findByRole("alert")).toHaveTextContent("API unreachable");
  });
});
