import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ROUTER_FUTURE } from "../../app/routerFuture";
import { jsonResponse, makeEndpoint, makeProfile, page, stubFetch } from "../../test/fakeApi";
import { ProfilesPage } from "./ProfilesPage";

const renderPage = () =>
  render(
    <MemoryRouter future={ROUTER_FUTURE}>
      <ProfilesPage />
    </MemoryRouter>,
  );

describe("ProfilesPage", () => {
  it("creates a profile with quality settings", async () => {
    let profiles = [] as ReturnType<typeof makeProfile>[];
    const { calls } = stubFetch((method, path, body) => {
      if (path.startsWith("/endpoints")) return jsonResponse(page([makeEndpoint()]));
      if (method === "GET" && path === "/profiles") return jsonResponse(page(profiles));
      if (method === "POST" && path === "/profiles") {
        const created = makeProfile({ ...(body as object), id: "pf2" });
        profiles = [created];
        return jsonResponse(created, 201);
      }
      return undefined;
    });
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("No profiles yet")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New profile" }));
    const drawer = screen.getByRole("dialog");
    await user.type(within(drawer).getByLabelText("Name"), "Tools nightly");
    await user.click(within(drawer).getByRole("checkbox", { name: "Tool-calling" }));
    await user.click(within(drawer).getByRole("radio", { name: "Inspect calls only" }));
    await user.click(within(drawer).getByRole("button", { name: "Create profile" }));

    expect(await screen.findByText("Created Tools nightly.")).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST")?.body as Record<string, unknown>;
    expect(post).toMatchObject({
      name: "Tools nightly",
      description: null,
      suites: ["performance", "tool_calling"],
      evaluation: { tool_mode: "generation_only", embedding: null },
    });
    const list = await screen.findByRole("list", { name: "Profiles" });
    expect(within(list).getByText("Tools nightly")).toBeInTheDocument();
    expect(within(list).getByText("tools inspected only")).toBeInTheDocument();
  });

  it("edits and deletes a profile", async () => {
    const { calls } = stubFetch((method, path, body) => {
      if (path.startsWith("/endpoints")) return jsonResponse(page([]));
      if (method === "GET" && path === "/profiles") return jsonResponse(page([makeProfile()]));
      if (method === "PATCH") return jsonResponse(makeProfile(body as object));
      if (method === "DELETE") return jsonResponse(null, 204);
      return undefined;
    });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Edit Nightly quality" }));
    const drawer = screen.getByRole("dialog");
    expect(within(drawer).getByLabelText("Token F1 pass threshold")).toHaveValue(0.7);
    const f1 = within(drawer).getByLabelText("Token F1 pass threshold");
    await user.clear(f1);
    await user.type(f1, "2");
    expect(within(drawer).getByRole("button", { name: "Save changes" })).toBeDisabled();
    await user.clear(f1);
    await user.type(f1, "0.4");
    await user.click(within(drawer).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Saved Nightly quality.")).toBeInTheDocument();
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.path).toBe("/profiles/pf1");
    expect((patch?.body as { evaluation: { token_f1_threshold: number } }).evaluation.token_f1_threshold).toBe(0.4);

    await user.click(screen.getByRole("button", { name: "Delete Nightly quality" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete profile" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === "/profiles/pf1")).toBe(true));
    expect(await screen.findByText("Deleted Nightly quality.")).toBeInTheDocument();
  });
});
