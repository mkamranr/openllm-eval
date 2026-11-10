import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { NAV_ITEMS } from "../components/layout/Layout";
import { jsonResponse, makeMe } from "../test/fakeApi";
import type { Me } from "../types/auth";
import { App } from "./App";
import { ROUTER_FUTURE } from "./routerFuture";

/** Signed in as ``me``; every other request stays pending so pages stay loading. */
function signedIn(me: Me | null = makeMe()) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return Promise.resolve(
          me ? jsonResponse(me) : jsonResponse({ error: { code: "not_authenticated", message: "Sign in required." } }, 401),
        );
      }
      if (url.endsWith("/auth/providers")) {
        return Promise.resolve(jsonResponse({ auth_enabled: true, local: true, ldap: false, kerberos: false }));
      }
      return new Promise(() => {});
    }),
  );
}

function renderAt(path = "/") {
  return render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App shell", () => {
  it("renders every main navigation item from the spec", async () => {
    signedIn();
    renderAt();
    const nav = await screen.findByRole("navigation", { name: "Main" });
    for (const item of NAV_ITEMS) {
      expect(nav).toHaveTextContent(item.label);
    }
    expect(NAV_ITEMS).toHaveLength(10);
    expect(nav).toHaveTextContent("Ada Admin");
    expect(nav).toHaveTextContent("Administrator");
  });

  it("routes to the endpoints page", async () => {
    signedIn();
    renderAt("/endpoints");
    expect(await screen.findByRole("heading", { level: 1, name: "Models / Endpoints" })).toBeInTheDocument();
  });

  it("routes /settings to the settings page", async () => {
    signedIn();
    renderAt("/settings");
    expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Users" })).toBeInTheDocument();
  });

  it("routes /runs to the runs list", async () => {
    signedIn();
    renderAt("/runs");
    expect(await screen.findByRole("heading", { level: 1, name: "Benchmark Runs" })).toBeInTheDocument();
  });

  it("sends signed-out visitors to the sign-in page", async () => {
    signedIn(null);
    renderAt("/runs");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  it("forces a password change before anything else", async () => {
    signedIn(makeMe("admin", { must_change_password: true }));
    renderAt("/runs");
    expect(await screen.findByRole("heading", { name: "Choose a new password" })).toBeInTheDocument();
  });

  it("hides editing navigation from viewers and says why", async () => {
    signedIn(makeMe("viewer"));
    renderAt();
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(nav).not.toHaveTextContent("New Evaluation");
    expect(screen.getByText(/read-only access/)).toBeInTheDocument();
  });

  it("warns when sign-in is disabled on the server", async () => {
    signedIn(makeMe("admin", { auth_enabled: false, auth_method: "anonymous", user: null, username: "anonymous" }));
    renderAt();
    expect(await screen.findByText(/Sign-in is disabled \(AUTH_ENABLED=false\)/)).toBeInTheDocument();
  });
});
