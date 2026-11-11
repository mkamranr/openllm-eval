import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { AuthProvider } from "../../auth/AuthContext";
import { ROUTER_FUTURE } from "../../app/routerFuture";
import { jsonResponse, makeMe, page, stubFetch } from "../../test/fakeApi";
import type { AuthSettings, User } from "../../types/auth";
import { LoginPage } from "../auth/LoginPage";
import { RunsPage } from "../runs/RunsPage";
import { SettingsPage } from "./SettingsPage";

function renderAt(path: string) {
  return render(
    <MemoryRouter future={ROUTER_FUTURE} initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/change-password" element={<p>change password page</p>} />
          <Route path="/" element={<p>home page</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

const UNAUTH = jsonResponse({ error: { code: "not_authenticated", message: "Sign in required." } }, 401);

describe("Sign-in page", () => {
  it("signs in, sends the CSRF header, and goes to the requested page", async () => {
    const { calls, fetchMock } = stubFetch((method, path) => {
      if (path === "/auth/me") return UNAUTH.clone();
      if (path === "/auth/providers") return jsonResponse({ auth_enabled: true, local: true, ldap: true, kerberos: true });
      if (method === "POST" && path === "/auth/login") return jsonResponse(makeMe("editor"));
      if (path.startsWith("/runs")) return jsonResponse(page([]));
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/login?next=%2Fruns");

    expect(await screen.findByRole("button", { name: /Sign in with Windows/ })).toBeInTheDocument();
    expect(screen.getByText(/Windows \(Active Directory\) account/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Username"), "CORP\\ada");
    await user.type(screen.getByLabelText("Password"), "secret-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Benchmark Runs" })).toBeInTheDocument();
    const login = calls.find((c) => c.path === "/auth/login")!;
    expect(login.body).toEqual({ username: "CORP\\ada", password: "secret-password" });
    const init = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/auth/login"))![1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-OpenLLM-CSRF"]).toBe("1");
    expect(init.credentials).toBe("same-origin");
  });

  it("shows the server's message and clears the password on failure", async () => {
    stubFetch((method, path) => {
      if (path === "/auth/me") return UNAUTH.clone();
      if (path === "/auth/providers") return jsonResponse({ auth_enabled: true, local: true, ldap: false, kerberos: false });
      if (method === "POST" && path === "/auth/login")
        return jsonResponse({ error: { code: "account_locked", message: "Too many failed sign-in attempts. Try again in 15 minute(s)." } }, 429);
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/login");
    await user.type(await screen.findByLabelText("Username"), "ada");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many failed sign-in attempts");
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.queryByRole("button", { name: /Sign in with Windows/ })).not.toBeInTheDocument();
  });

  it("never redirects to another site after sign-in", async () => {
    stubFetch((method, path) => {
      if (path === "/auth/me") return UNAUTH.clone();
      if (path === "/auth/providers") return jsonResponse({ auth_enabled: true, local: true, ldap: false, kerberos: false });
      if (method === "POST" && path === "/auth/login") return jsonResponse(makeMe());
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/login?next=%2F%2Fevil.example");
    await user.type(await screen.findByLabelText("Username"), "ada");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("home page")).toBeInTheDocument();
  });
});

describe("Role-aware pages", () => {
  it("hides create actions from viewers", async () => {
    stubFetch((_m, path) => {
      if (path === "/auth/me") return jsonResponse(makeMe("viewer"));
      if (path.startsWith("/runs")) return jsonResponse(page([]));
      return undefined;
    });
    renderAt("/runs");
    expect(await screen.findByRole("heading", { name: "Benchmark Runs" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("link", { name: /New evaluation/ })).not.toBeInTheDocument());
  });
});

const USERS: User[] = [
  { ...makeMe().user!, id: "u1", username: "ada" },
  { ...makeMe().user!, id: "u2", username: "bob", display_name: "Bob B", source: "ldap", role: "editor", role_pinned: false, groups: ["CN=LLM Editors"] },
];

describe("Settings", () => {
  it("creates an API token and shows it only once", async () => {
    let created = false;
    const { calls } = stubFetch((method, path) => {
      if (path === "/auth/me") return jsonResponse(makeMe("viewer"));
      if (method === "GET" && path === "/auth/tokens")
        return jsonResponse(created ? [{ id: "t1", name: "ci", prefix: "oel_abcdef", created_at: "2026-11-10T00:00:00Z", expires_at: null, last_used_at: null, revoked_at: null }] : []);
      if (method === "POST" && path === "/auth/tokens") {
        created = true;
        return jsonResponse({ id: "t1", name: "ci", prefix: "oel_abcdef", token: "oel_abcdef-full-secret-token", created_at: "", expires_at: null, last_used_at: null, revoked_at: null }, 201);
      }
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/settings");

    // Viewers only see their own account.
    expect(await screen.findByRole("tab", { name: "Account" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Users" })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Token name"), "ci");
    await user.selectOptions(screen.getByLabelText("Token lifetime"), "never");
    await user.click(screen.getByRole("button", { name: /Create token/ }));

    expect(await screen.findByLabelText("New API token")).toHaveTextContent("oel_abcdef-full-secret-token");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "ci", expires_in_days: null });
    const table = await screen.findByRole("table", { name: "Your API tokens" });
    expect(table).toHaveTextContent("oel_abcdef…");
    expect(table).not.toHaveTextContent("full-secret");
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.queryByText(/full-secret-token/)).not.toBeInTheDocument();
  });

  it("lets administrators create users and shows the one-time password", async () => {
    const { calls } = stubFetch((method, path) => {
      if (path === "/auth/me") return jsonResponse(makeMe("admin"));
      if (method === "GET" && path.startsWith("/users")) return jsonResponse(page(USERS));
      if (method === "POST" && path === "/users")
        return jsonResponse({ ...USERS[0], id: "u3", username: "carol", role: "editor", initial_password: "Gen-Pass-xyz-123" }, 201);
      if (method === "PATCH" && path === "/users/u2") return jsonResponse({ ...USERS[1], role: "admin", role_pinned: true });
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/settings?tab=users");

    const table = await screen.findByRole("table", { name: "Users" });
    const bob = within(table).getByText("bob").closest("tr")!;
    expect(bob).toHaveTextContent("Windows / AD");
    expect(bob).toHaveTextContent("from group mapping");
    // Your own row cannot be demoted or disabled.
    expect(within(table).getByLabelText("Role of ada")).toBeDisabled();

    await user.selectOptions(within(bob).getByLabelText("Role of bob"), "admin");
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ role: "admin" });

    await user.click(screen.getByRole("button", { name: /New local user/ }));
    await user.type(screen.getByLabelText("Username *"), "Carol");
    await user.selectOptions(screen.getByLabelText("Role"), "editor");
    await user.click(screen.getByRole("button", { name: "Create user" }));
    expect(await screen.findByLabelText("One-time password")).toHaveTextContent("Gen-Pass-xyz-123");
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ username: "carol", role: "editor" });
  });

  it("edits directory settings without ever showing the stored bind password", async () => {
    const saved: AuthSettings = {
      ldap: {
        enabled: true, server_urls: ["ldaps://dc1.corp.example:636"], start_tls: false, verify_tls: true, ca_cert_pem: null,
        bind_mode: "service", user_bind_format: "", bind_dn: "CN=svc,DC=corp", user_search_base: "OU=Users,DC=corp", user_filter: "(&(objectClass=user)(sAMAccountName={username}))",
        username_attribute: "sAMAccountName", display_name_attribute: "displayName", email_attribute: "mail", group_attribute: "memberOf",
        nested_groups: false, timeout_seconds: 5, role_mappings: [{ group: "LLM Admins", role: "admin" }], allowed_groups: [], default_role: "viewer",
        bind_password_set: true,
      },
      kerberos: { enabled: false, service_principal: null, keytab_set: false, library_available: true },
      secret_key_configured: true,
      updated_by: "ada",
      updated_at: "2026-11-10T10:00:00Z",
    };
    const { calls } = stubFetch((method, path) => {
      if (path === "/auth/me") return jsonResponse(makeMe("admin"));
      if (method === "GET" && path === "/settings/auth") return jsonResponse(saved);
      if (method === "PUT" && path === "/settings/auth") return jsonResponse(saved);
      if (method === "POST" && path === "/settings/auth/test-user")
        return jsonResponse({ ok: true, message: "bob: password accepted; would sign in as editor.", details: { dn: "CN=Bob", groups: ["CN=LLM Editors"], role: "editor" } });
      return undefined;
    });
    const user = userEvent.setup();
    renderAt("/settings?tab=authentication");

    const password = await screen.findByLabelText("Service account password");
    expect(password).toHaveValue("");
    expect(password).toHaveAttribute("placeholder", expect.stringContaining("stored"));
    await user.click(screen.getByRole("button", { name: /Add mapping/ }));
    await user.type(screen.getByLabelText("Group 2"), "LLM Editors");
    await user.selectOptions(screen.getByLabelText("Role for group 2"), "editor");

    await user.type(screen.getByLabelText("Test username"), "bob");
    await user.click(screen.getByRole("button", { name: /Test user/ }));
    expect(await screen.findByText(/would sign in as editor/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Save authentication settings/ }));
    expect(await screen.findByText("Authentication settings saved.")).toBeInTheDocument();
    const put = calls.find((c) => c.method === "PUT")!.body as { ldap: Record<string, unknown> };
    // Blank password field = keep the stored one (sent as null, never a placeholder).
    expect(put.ldap.bind_password).toBeNull();
    expect(put.ldap.role_mappings).toEqual([{ group: "LLM Admins", role: "admin" }, { group: "LLM Editors", role: "editor" }]);
    expect(put.ldap).not.toHaveProperty("bind_password_set");

    // Switching to direct bind hides the service account and sends the bind format.
    await user.click(screen.getByRole("radio", { name: /Bind as the user/ }));
    expect(screen.queryByLabelText("Service account password")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("User bind format"), "{{username}@corp.example");
    await user.click(screen.getByRole("button", { name: /Save authentication settings/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(2));
    const direct = calls.filter((c) => c.method === "PUT")[1]!.body as { ldap: Record<string, unknown> };
    expect(direct.ldap).toMatchObject({ bind_mode: "direct", user_bind_format: "{username}@corp.example" });
  });
});
