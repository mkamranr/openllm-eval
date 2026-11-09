import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ROUTER_FUTURE } from "../../app/routerFuture";
import { jsonResponse, makeDataset, makeVersion, page, stubFetch } from "../../test/fakeApi";
import type { DatasetCase } from "../../types/api";
import { DatasetDetailPage } from "./DatasetDetailPage";

const CASES: DatasetCase[] = Array.from({ length: 3 }, (_, i) => ({
  position: i,
  case_identifier: `q${i + 1}`,
  case_type: "qa",
  tags: i % 2 ? ["web"] : ["basic"],
  content: { id: `q${i + 1}`, type: "qa", input: `Question ${i + 1}?`, reference_answer: "A", tags: [] },
  truncated: i === 2,
}));

const V2 = makeVersion({ id: "v2", version_number: 2, content_hash: "c".repeat(64), record_count: 3 });
const V1 = makeVersion({ id: "v1", version_number: 1, is_current: false, version_label: "initial" });
const DATASET = makeDataset({ current_version: V2, version_count: 2 });

function renderPage() {
  return render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={["/datasets/d1"]}>
      <Routes>
        <Route path="/datasets/:datasetId" element={<DatasetDetailPage />} />
        <Route path="/datasets" element={<h1>list page</h1>} />
      </Routes>
    </MemoryRouter>,
  );
}

function stub(extra?: Parameters<typeof stubFetch>[0]) {
  return stubFetch(
    ...(extra ? [extra] : []),
    (method, path) => {
      if (method === "GET" && path === "/datasets/d1") return jsonResponse(DATASET);
      if (method === "GET" && path === "/datasets/d1/versions") return jsonResponse([V2, V1]);
      if (method === "GET" && path.startsWith("/datasets/d1/preview")) {
        const tag = new URLSearchParams(path.split("?")[1]).get("tag");
        return jsonResponse(page(tag ? CASES.filter((c) => c.tags.includes(tag)) : CASES));
      }
      if (method === "GET" && path.startsWith("/datasets/d1/cases/q3"))
        return jsonResponse({ ...CASES[2], truncated: false, content: { ...CASES[2]!.content, input: "FULL TEXT" } });
      if (method === "GET" && path.startsWith("/projects")) return jsonResponse(page([]));
      return undefined;
    },
  );
}

describe("DatasetDetailPage", () => {
  it("shows summary facts and records, filters by tag, and opens a full record", async () => {
    const { calls } = stub();
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("heading", { level: 1, name: "Support QA" })).toBeInTheDocument();
    const facts = screen.getByLabelText("Dataset summary");
    expect(facts).toHaveTextContent("v2");
    expect(facts).toHaveTextContent("cccccccccccc…");
    expect(screen.getByRole("link", { name: /Export JSONL/ })).toHaveAttribute("href", "/api/v1/datasets/d1/export");

    const records = await screen.findByRole("table", { name: "Records" });
    expect(within(records).getAllByRole("row")).toHaveLength(4);
    expect(within(records).getByText("Question 2?")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Filter by tag"), "web");
    await waitFor(() => expect(within(screen.getByRole("table", { name: "Records" })).getAllByRole("row")).toHaveLength(2));
    expect(calls.some((c) => c.path.includes("tag=web") && c.path.includes("version_id=v2"))).toBe(true);

    await user.selectOptions(screen.getByLabelText("Filter by tag"), "");
    await user.click(await screen.findByRole("button", { name: "q3" }));
    const drawer = await screen.findByRole("dialog", { name: "Record q3" });
    await waitFor(() => expect(within(drawer).getByLabelText("Record JSON")).toHaveTextContent("FULL TEXT"));
  });

  it("lists versions and switches the current version", async () => {
    const { calls } = stub((method, path) =>
      method === "PATCH" && path === "/datasets/d1" ? jsonResponse(DATASET) : undefined,
    );
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("tab", { name: /Versions/ }));
    const table = await screen.findByRole("table", { name: "Versions" });
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(within(table).getByText("initial")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Export v1" })).toHaveAttribute(
      "href",
      "/api/v1/datasets/d1/export?version_id=v1",
    );
    await user.click(within(table).getByRole("button", { name: "Make current" }));
    expect(await screen.findByText("Version 1 is now current.")).toBeInTheDocument();
    expect(calls).toContainEqual({ method: "PATCH", path: "/datasets/d1", body: { current_version_id: "v1" } });
  });

  it("deletes after confirmation and returns to the list", async () => {
    stub((method, path) => (method === "DELETE" && path === "/datasets/d1" ? jsonResponse(null, 204) : undefined));
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Delete Support QA" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("all 2 version(s)");
    await user.click(within(dialog).getByRole("button", { name: "Delete dataset" }));
    expect(await screen.findByRole("heading", { name: "list page" })).toBeInTheDocument();
  });
});
