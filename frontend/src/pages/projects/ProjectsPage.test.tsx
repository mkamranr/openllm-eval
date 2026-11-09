import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { jsonResponse, makeProject, page, stubFetch } from "../../test/fakeApi";
import { ProjectsPage } from "./ProjectsPage";

describe("ProjectsPage", () => {
  it("creates a project", async () => {
    let projects = [] as ReturnType<typeof makeProject>[];
    const { calls } = stubFetch((method, path, body) => {
      if (method === "GET" && path.startsWith("/projects")) return jsonResponse(page(projects));
      if (method === "POST" && path === "/projects") {
        const created = makeProject({ name: (body as { name: string }).name });
        projects = [created];
        return jsonResponse(created, 201);
      }
      return undefined;
    });
    const user = userEvent.setup();
    render(<ProjectsPage />);

    expect(await screen.findByText("No projects yet")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New project" }));
    await user.type(screen.getByLabelText("Name"), "RAG assistant");
    await user.type(screen.getByLabelText("Tags"), "rag, internal");
    await user.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByText("Created RAG assistant.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      name: "RAG assistant",
      description: null,
      tags: ["rag", "internal"],
    });
    expect(within(await screen.findByRole("list", { name: "Projects" })).getByText("RAG assistant")).toBeInTheDocument();
  });

  it("archives, and asks before deleting a project with endpoints", async () => {
    const { calls } = stubFetch((method, path) => {
      if (method === "GET" && path.startsWith("/projects"))
        return jsonResponse(page([makeProject({ endpoint_count: 2 })]));
      if (method === "PATCH") return jsonResponse(makeProject({ archived_at: "2026-11-09T00:00:00Z" }));
      if (method === "DELETE") return jsonResponse(null, 204);
      return undefined;
    });
    const user = userEvent.setup();
    render(<ProjectsPage />);

    expect(await screen.findByText("2 endpoints")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Archive" }));
    await waitFor(() =>
      expect(calls).toContainEqual({ method: "PATCH", path: "/projects/p1", body: { archived: true } }),
    );

    await user.click(screen.getByRole("button", { name: "Delete Support bot" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("2 project-scoped endpoint(s) and 0 dataset(s)");
    await user.click(within(dialog).getByRole("button", { name: "Delete project" }));
    expect(await screen.findByText("Deleted Support bot.")).toBeInTheDocument();
  });

  it("requests archived projects when toggled", async () => {
    const { calls } = stubFetch((method) =>
      method === "GET" ? jsonResponse(page([])) : undefined,
    );
    const user = userEvent.setup();
    render(<ProjectsPage />);
    await screen.findByText("No projects yet");
    await user.click(screen.getByRole("switch", { name: "Show archived" }));
    await waitFor(() => expect(calls.some((c) => c.path.includes("include_archived=true"))).toBe(true));
  });
});
