import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ROUTER_FUTURE } from "../../app/routerFuture";
import { jsonResponse, makeDataset, makeProject, page, stubFetch, type Handler } from "../../test/fakeApi";
import type { Dataset, SampleDataset, ValidationResult } from "../../types/api";
import { DatasetsPage } from "./DatasetsPage";

const SAMPLE: SampleDataset = {
  sample_id: "general-qa",
  name: "Sample: general Q&A",
  description: "Short technical questions.",
  dataset_type: "qa",
  record_count: 12,
  license: "CC0-1.0",
  source: "Original synthetic examples",
  installed_dataset_id: null,
};

const VALID: ValidationResult = {
  valid: true,
  format: "jsonl",
  dataset_type: "qa",
  schema_version: "1",
  rows: 2,
  record_count: 2,
  error_count: 0,
  warning_count: 0,
  errors: [],
  warnings: [],
  type_counts: { qa: 2 },
  tag_counts: {},
  split_counts: {},
  content_hash: "b".repeat(64),
  preview: [{ id: "q1" }],
  limits: { max_upload_bytes: 1, max_records: 1, max_record_bytes: 1 },
};

function handlers(datasets: Dataset[], samples = [SAMPLE]): Handler {
  return (method, path) => {
    if (method === "GET" && path.startsWith("/datasets/samples")) return jsonResponse(samples);
    if (method === "GET" && path.startsWith("/datasets")) return jsonResponse(page(datasets));
    if (method === "GET" && path.startsWith("/projects")) return jsonResponse(page([makeProject()]));
    return undefined;
  };
}

const renderPage = (url = "/datasets") =>
  render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={[url]}>
      <Routes>
        <Route path="/datasets" element={<DatasetsPage />} />
        <Route path="/datasets/:id" element={<h1>detail page</h1>} />
      </Routes>
    </MemoryRouter>,
  );

describe("DatasetsPage", () => {
  it("lists datasets with type, records, version and hash", async () => {
    stubFetch(handlers([makeDataset(), makeDataset({ id: "d2", name: "Tools", dataset_type: "tool_call", tags: [] })]));
    const user = userEvent.setup();
    renderPage();
    const table = await screen.findByRole("table", { name: "Datasets" });
    const row = within(table).getByText("Support QA").closest("tr")!;
    expect(row).toHaveTextContent("Q&A (reference answer)");
    expect(row).toHaveTextContent("12");
    expect(row).toHaveTextContent("v1");
    expect(row).toHaveTextContent("aaaaaaaaaaaa…");

    await user.selectOptions(screen.getByLabelText("Filter by type"), "tool_call");
    expect(within(table).queryByText("Support QA")).not.toBeInTheDocument();
    expect(within(table).getByText("Tools")).toBeInTheDocument();

    await user.click(within(table).getByText("Tools"));
    expect(await screen.findByRole("heading", { name: "detail page" })).toBeInTheDocument();
  });

  it("installs a sample from the samples tab", async () => {
    let installed = false;
    const { calls } = stubFetch((method, path) => {
      if (method === "POST" && path === "/datasets/samples/general-qa/install") {
        installed = true;
        return jsonResponse(makeDataset({ name: "Sample: general Q&A" }), 201);
      }
      return handlers([], [{ ...SAMPLE, installed_dataset_id: installed ? "d1" : null }])(method, path, undefined);
    });
    const user = userEvent.setup();
    renderPage("/datasets?tab=samples");
    const list = await screen.findByRole("list", { name: "Sample datasets" });
    expect(within(list).getByText("CC0-1.0", { exact: false })).toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: "Install Sample: general Q&A" }));
    expect(await screen.findByText("Installed Sample: general Q&A.")).toBeInTheDocument();
    expect(await within(list).findByText("Installed")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(true);
  });

  it("validates pasted content, shows row errors, then imports", async () => {
    const invalid: ValidationResult = {
      ...VALID,
      valid: false,
      record_count: 1,
      error_count: 1,
      content_hash: null,
      errors: [{ line: 2, case_id: "q2", field: "reference_answer", message: "Field required" }],
    };
    let attempt = 0;
    const { calls } = stubFetch(handlers([]), (method, path, body) => {
      if (method === "POST" && path === "/datasets/validate") return jsonResponse(attempt++ === 0 ? invalid : VALID);
      if (method === "POST" && path === "/datasets")
        return jsonResponse(makeDataset({ id: "new", name: (body as { name: string }).name }), 201);
      return undefined;
    });
    const user = userEvent.setup();
    renderPage("/datasets?tab=import");

    await user.type(await screen.findByLabelText("Name"), "my-set");
    await user.click(screen.getByRole("button", { name: "Paste" }));
    await user.click(screen.getByLabelText("Dataset content"));
    await user.paste('{"id":"q1","type":"qa","input":"a","reference_answer":"b"}\n{"id":"q2","type":"qa","input":"c"}');
    await user.click(screen.getByRole("button", { name: "Validate" }));

    const errors = await screen.findByRole("table", { name: "Errors" });
    expect(within(errors).getByText("Field required").closest("tr")).toHaveTextContent("2q2reference_answer");
    expect(screen.getByText(/1 error found/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Validate" }));
    expect(await screen.findByText("Valid: 2 records ready to import.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Import dataset" }));
    expect(await screen.findByRole("heading", { name: "detail page" })).toBeInTheDocument();
    const created = calls.find((c) => c.method === "POST" && c.path === "/datasets")!;
    expect(created.body).toMatchObject({ name: "my-set", dataset_type: "qa", project_id: null, tags: [] });
  });

  it("shows server-side validation details when an import is rejected", async () => {
    stubFetch(handlers([]), (method, path) =>
      method === "POST" && path === "/datasets"
        ? jsonResponse(
            {
              error: {
                code: "dataset_invalid",
                message: "The dataset has 1 validation error; nothing was imported.",
                request_id: "r",
                details: {
                  ...VALID,
                  valid: false,
                  error_count: 1,
                  content_hash: null,
                  errors: [{ line: 7, case_id: null, field: null, message: "invalid JSON: Expecting value (column 1)" }],
                },
              },
            },
            422,
          )
        : undefined,
    );
    const user = userEvent.setup();
    renderPage("/datasets?tab=import");
    await user.type(await screen.findByLabelText("Name"), "x");
    await user.click(screen.getByRole("button", { name: "Paste" }));
    await user.type(screen.getByLabelText("Dataset content"), "oops");
    await user.click(screen.getByRole("button", { name: "Import dataset" }));
    const errors = await screen.findByRole("table", { name: "Errors" });
    expect(errors).toHaveTextContent("invalid JSON: Expecting value");
    await waitFor(() => expect(errors).toHaveTextContent("7"));
  });

  it("asks for content before validating a file", async () => {
    stubFetch(handlers([]));
    const user = userEvent.setup();
    renderPage("/datasets?tab=import");
    await user.click(await screen.findByRole("button", { name: "Validate" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a file first.");

    const file = new File(['{"id":"q1","type":"qa","input":"a","reference_answer":"b"}\n'], "cases.jsonl", {
      type: "application/x-ndjson",
    });
    await user.upload(screen.getByLabelText("Dataset file"), file);
    expect(screen.getByText("cases.jsonl")).toBeInTheDocument();
  });
});
