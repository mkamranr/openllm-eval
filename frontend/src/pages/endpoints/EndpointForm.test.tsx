import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ADAPTERS, jsonResponse, makeEndpoint, makeProject, stubFetch } from "../../test/fakeApi";
import type { EndpointInput } from "../../types/api";
import { EndpointForm } from "./EndpointForm";

describe("EndpointForm", () => {
  it("creates an endpoint with nulls for blank generation settings", async () => {
    const { calls } = stubFetch((method, path, body) =>
      method === "POST" && path === "/endpoints"
        ? jsonResponse(makeEndpoint({ name: (body as EndpointInput).name }), 201)
        : undefined,
    );
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(
      <EndpointForm projects={[makeProject()]} adapters={ADAPTERS} onSaved={onSaved} onClose={() => {}} />,
    );

    await user.type(screen.getByLabelText("Name"), "my-vllm");
    await user.type(screen.getByLabelText("Base URL"), "http://vllm:8000/v1");
    await user.type(screen.getByLabelText("Model identifier"), "qwen");
    await user.type(screen.getByLabelText("API key secret reference"), "env:OPENLLM_SECRET_VLLM");
    await user.selectOptions(screen.getByLabelText("Project"), "p1");
    await user.type(screen.getByLabelText("Deployment metadata value 2"), "A100");
    await user.type(screen.getByLabelText("Tags"), "gpu, prod");
    await user.click(screen.getByRole("button", { name: "Create endpoint" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const sent = calls[0]!.body as EndpointInput;
    expect(sent).toMatchObject({
      name: "my-vllm",
      project_id: "p1",
      base_url: "http://vllm:8000/v1",
      model_identifier: "qwen",
      secret_reference: "env:OPENLLM_SECRET_VLLM",
      deployment_metadata: { gpu_type: "A100" },
      model_metadata: {},
      telemetry: null,
      tags: ["gpu", "prod"],
      enabled: true,
    });
    expect(sent.request_configuration).toMatchObject({
      temperature: null,
      top_p: null,
      max_tokens: null,
      stream: true,
      request_timeout_seconds: 120,
    });
  });

  it("shows server-side field errors next to the fields", async () => {
    stubFetch((method) =>
      method === "POST"
        ? jsonResponse(
            {
              error: {
                code: "validation_error",
                message: "Request validation failed",
                request_id: "x",
                details: [
                  { loc: ["body", "base_url"], msg: "Value error, URL must not contain credentials" },
                  { loc: ["body", "request_configuration", "temperature"], msg: "Input should be less than or equal to 2" },
                ],
              },
            },
            422,
          )
        : undefined,
    );
    const user = userEvent.setup();
    render(<EndpointForm projects={[]} adapters={ADAPTERS} onSaved={() => {}} onClose={() => {}} />);

    await user.type(screen.getByLabelText("Name"), "x");
    await user.type(screen.getByLabelText("Temperature"), "5");
    await user.click(screen.getByRole("button", { name: "Create endpoint" }));

    expect(await screen.findByText("URL must not contain credentials")).toBeInTheDocument();
    expect(screen.getByText("Input should be less than or equal to 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Base URL")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("Please fix the highlighted fields.");
  });

  it("edits an existing endpoint via PATCH and warns about a missing secret", async () => {
    const endpoint = makeEndpoint({
      secret_reference: "env:OPENLLM_SECRET_MISSING",
      secret_configured: false,
      request_configuration: { ...makeEndpoint().request_configuration, temperature: 0.2 },
    });
    const { calls } = stubFetch((method, path) =>
      method === "PATCH" && path === "/endpoints/e1" ? jsonResponse(endpoint) : undefined,
    );
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(<EndpointForm endpoint={endpoint} projects={[]} adapters={ADAPTERS} onSaved={onSaved} onClose={() => {}} />);

    expect(screen.getByRole("dialog", { name: "Edit local-vllm" })).toBeInTheDocument();
    expect(screen.getByText(/is not set in the server environment/)).toBeInTheDocument();
    expect(screen.getByLabelText("Temperature")).toHaveValue(0.2);

    await user.click(screen.getByRole("switch", { name: /Verify TLS/ }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect((calls[0]!.body as EndpointInput).request_configuration.verify_tls).toBe(false);
    expect((calls[0]!.body as EndpointInput).request_configuration.temperature).toBe(0.2);
  });
  it("saves declared variant fields and telemetry, keeping unknown fields out", async () => {
    const endpoint = makeEndpoint({ model_metadata: { family: "llama", note: "kept" } });
    const { calls } = stubFetch((method, path) =>
      method === "PATCH" && path === "/endpoints/e1" ? jsonResponse(endpoint) : undefined,
    );
    const onSaved = vi.fn();
    const user = userEvent.setup();
    render(<EndpointForm endpoint={endpoint} projects={[]} adapters={ADAPTERS} onSaved={onSaved} onClose={() => {}} />);

    expect(screen.getByLabelText("Model family")).toHaveValue("llama");
    await user.type(screen.getByLabelText("Quantization method"), "AWQ");
    await user.type(screen.getByLabelText("Weight bits"), "4");
    await user.type(screen.getByLabelText("Exporter URL"), "http://gpu:9400/metrics");
    await user.type(screen.getByLabelText("GPU indices"), "0, 1");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const sent = calls[0]!.body as EndpointInput;
    expect(sent.model_metadata).toEqual({ family: "llama", note: "kept", quantization_method: "AWQ", weight_bits: 4 });
    expect(sent.telemetry).toMatchObject({ url: "http://gpu:9400/metrics", interval_seconds: 2, gpu_indices: [0, 1] });
  });
});
