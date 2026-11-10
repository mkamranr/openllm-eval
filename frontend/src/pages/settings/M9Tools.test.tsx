import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { AuthProvider } from "../../auth/AuthContext";
import { ROUTER_FUTURE } from "../../app/routerFuture";
import { jsonResponse, makeMe, stubFetch } from "../../test/fakeApi";
import type { ToolsStatus } from "../../types/tools";
import { DEFAULT_QUALITY, QualitySettingsForm, type QualitySettings } from "../evaluation/qualitySettings";
import { SimulationView } from "../runs/QualitySection";
import { SettingsPage } from "./SettingsPage";

const STATUS: ToolsStatus = {
  enabled: true,
  reachable: true,
  detail: null,
  registry_sha256: "ab".repeat(32),
  registry_error: null,
  tools: [
    {
      name: "calculator",
      description: "Arithmetic",
      version: "1",
      kind: "script",
      parameters: { type: "object" },
      timeout_seconds: 5,
      max_output_bytes: 65536,
    },
  ],
};

function Form() {
  const [q, setQ] = useState<QualitySettings>(DEFAULT_QUALITY);
  return (
    <>
      <QualitySettingsForm value={q} onChange={(p) => setQ({ ...q, ...p })} types={["tool_calling"]} endpoints={[]} />
      <output data-testid="mode">{q.toolMode}</output>
    </>
  );
}

describe("Integration-mode tool option", () => {
  it("is offered when the tool runner is enabled and reachable", async () => {
    stubFetch((_m, path) => (path === "/tools/status" ? jsonResponse(STATUS) : undefined));
    const user = userEvent.setup();
    render(<Form />);
    const option = screen.getByRole("radio", { name: "Execute in sandbox" });
    await waitFor(() => expect(option).toBeEnabled());
    await user.click(option);
    expect(screen.getByTestId("mode")).toHaveTextContent("integration");
    expect(screen.getByText(/executed by the administrator-declared tools/)).toBeInTheDocument();
  });

  it("is disabled, with the reason, when tool execution is off", async () => {
    stubFetch((_m, path) =>
      path === "/tools/status" ? jsonResponse({ ...STATUS, enabled: false, reachable: false, tools: [] }) : undefined,
    );
    render(<Form />);
    expect(await screen.findByText(/Tool execution is off on this server/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Execute in sandbox" })).toBeDisabled();
  });
});

describe("Settings → Tools", () => {
  it("lists the registered tools for administrators", async () => {
    stubFetch((_m, path) => {
      if (path === "/auth/me") return jsonResponse(makeMe("admin"));
      if (path === "/tools/status") return jsonResponse(STATUS);
      return undefined;
    });
    render(
      <MemoryRouter future={ROUTER_FUTURE} initialEntries={["/settings?tab=tools"]}>
        <AuthProvider>
          <Routes>
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("table", { name: "Registered tools" })).toBeInTheDocument();
    expect(screen.getByText("calculator")).toBeInTheDocument();
    expect(screen.getByText("ab".repeat(32))).toBeInTheDocument();
  });
});

describe("Result detail", () => {
  it("labels executed tool calls", () => {
    render(
      <SimulationView
        simulation={{
          mode: "integration",
          executed: true,
          completed: true,
          final_content: "It is 42.",
          rounds: [
            {
              round: 1,
              latency_ms: 12,
              ok: true,
              error: null,
              next_tool_calls: [],
              tool_results: [
                {
                  name: "calculator",
                  arguments: { expression: "6*7" },
                  fixture_matched: false,
                  valid: true,
                  result: '{"result": 42}',
                  executed: true,
                  duration_ms: 31,
                },
              ],
            },
          ],
        }}
      />,
    );
    expect(screen.getByRole("heading", { name: "Executed tool calls" })).toBeInTheDocument();
    expect(screen.getByText(/executed ·/)).toBeInTheDocument();
  });
});
