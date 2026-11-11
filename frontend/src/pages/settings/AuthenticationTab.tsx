import { Plug, Plus, Save, ShieldCheck, Trash2, UserSearch } from "lucide-react";
import { useEffect, useState } from "react";

import { Callout, Switch } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { ApiError, api } from "../../services/api";
import type {
  AuthSettings,
  DirectoryTestResult,
  KerberosSettingsInput,
  LdapSettingsInput,
  Role,
  RoleMapping,
} from "../../types/auth";

const lines = (value: string) => value.split("\n").map((s) => s.trim()).filter(Boolean);

function toForm(s: AuthSettings): LdapSettingsInput {
  const ldap: LdapSettingsInput & { bind_password_set?: boolean } = { ...s.ldap };
  delete ldap.bind_password_set;
  return { ...ldap, bind_password: "", clear_bind_password: false };
}

async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

function ResultCallout({ result }: { result: DirectoryTestResult }) {
  const d = result.details;
  return (
    <Callout tone={result.ok ? "ok" : "error"} role="status">
      <div>{result.message}</div>
      {Array.isArray(d.groups) && (
        <details style={{ marginTop: 6 }}>
          <summary className="small">{(d.groups as string[]).length} groups · DN {String(d.dn)}</summary>
          <ul className="plain-list small mono">{(d.groups as string[]).map((g) => <li key={g}>{g}</li>)}</ul>
        </details>
      )}
    </Callout>
  );
}

export function AuthenticationTab() {
  const settings = useAsync(() => api.authSettings.get(), []);
  const [ldap, setLdap] = useState<LdapSettingsInput | null>(null);
  const [urls, setUrls] = useState("");
  const [allowed, setAllowed] = useState("");
  const [kerberos, setKerberos] = useState<KerberosSettingsInput>({ enabled: false, service_principal: null });
  const [keytabName, setKeytabName] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [test, setTest] = useState<DirectoryTestResult | null>(null);
  const [testUser, setTestUser] = useState({ username: "", password: "" });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!settings.data) return;
    const form = toForm(settings.data);
    setLdap(form);
    setUrls(form.server_urls.join("\n"));
    setAllowed(form.allowed_groups.join("\n"));
    setKerberos({ enabled: settings.data.kerberos.enabled, service_principal: settings.data.kerberos.service_principal });
    setKeytabName(null);
  }, [settings.data]);

  if (settings.error) return <Callout tone="error" role="alert">Could not load the settings: {settings.error.message}</Callout>;
  if (!ldap || !settings.data) return <p className="muted" role="status">Loading…</p>;
  const saved = settings.data;
  const set = <K extends keyof LdapSettingsInput>(key: K, value: LdapSettingsInput[K]) => setLdap({ ...ldap, [key]: value });
  const payload = (): LdapSettingsInput => ({
    ...ldap,
    server_urls: lines(urls),
    allowed_groups: lines(allowed),
    bind_dn: ldap.bind_dn?.trim() || null,
    ca_cert_pem: ldap.ca_cert_pem?.trim() || null,
    bind_password: ldap.bind_password || null,
  });
  const setMapping = (i: number, m: Partial<RoleMapping>) =>
    set("role_mappings", ldap.role_mappings.map((x, j) => (j === i ? { ...x, ...m } : x)));

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  };

  const save = () =>
    run(async () => {
      setNotice(null);
      await api.authSettings.save(payload(), { ...kerberos, service_principal: kerberos.service_principal?.trim() || null });
      await settings.reload();
      setNotice({ tone: "ok", text: "Authentication settings saved." });
    });

  return (
    <div className="form" style={{ paddingTop: 8 }}>
      {!saved.secret_key_configured && (
        <Callout tone="warn">
          AUTH_SECRET_KEY is not set on the server, so the bind password and keytab cannot be stored. Set it in .env
          (deploy.sh generates one) and restart.
        </Callout>
      )}
      {notice && <Callout tone={notice.tone} role={notice.tone === "error" ? "alert" : "status"}>{notice.text}</Callout>}

      <section className="form-section">
        <div className="form-section-title"><ShieldCheck size={16} aria-hidden="true" /> Windows / Active Directory (LDAP)</div>
        <Switch label="Allow sign-in with directory accounts" checked={ldap.enabled} onChange={(v) => set("enabled", v)} />
        <p className="small muted" style={{ margin: 0 }}>
          Users sign in with their Windows username and password, checked against the directory. Local accounts keep working.
        </p>
        <div className="form-row">
          <div className="field">
            <label className="field">
              <span className="label">Directory servers (one per line)</span>
              <textarea className="input mono" rows={2} value={urls} onChange={(e) => setUrls(e.target.value)}
                aria-describedby="ldap-urls-hint" placeholder={"ldaps://dc1.corp.example.com:636\nldaps://dc2.corp.example.com:636"} />
            </label>
            <small className="hint" id="ldap-urls-hint">ldaps:// is recommended. Tried in order.</small>
          </div>
          <div className="form-grid">
            <Switch label="Use StartTLS (ldap:// URLs)" checked={ldap.start_tls} onChange={(v) => set("start_tls", v)} />
            <Switch label="Verify the server certificate" checked={ldap.verify_tls} onChange={(v) => set("verify_tls", v)} />
            <label className="field">
              <span className="label">Timeout (seconds)</span>
              <input className="input" type="number" min={1} max={30} value={ldap.timeout_seconds}
                onChange={(e) => set("timeout_seconds", Number(e.target.value))} style={{ maxWidth: 120 }} />
            </label>
          </div>
        </div>
        <div className="field">
          <label className="field">
            <span className="label">CA certificate (PEM, optional)</span>
            <textarea className="input mono" rows={3} value={ldap.ca_cert_pem ?? ""} onChange={(e) => set("ca_cert_pem", e.target.value)}
              aria-describedby="ldap-ca-hint" placeholder="-----BEGIN CERTIFICATE-----" />
          </label>
          <small className="hint" id="ldap-ca-hint">Your internal CA, if the domain controllers' certificates are not publicly trusted.</small>
        </div>
        <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="label">How users are checked</legend>
          <label className="small" style={{ display: "flex", gap: 6 }}>
            <input type="radio" name="bind-mode" checked={ldap.bind_mode === "direct"} onChange={() => set("bind_mode", "direct")} />
            Bind as the user: each user signs in with their own username and password. No service account needed.
          </label>
          <label className="small" style={{ display: "flex", gap: 6 }}>
            <input type="radio" name="bind-mode" checked={ldap.bind_mode === "service"} onChange={() => set("bind_mode", "service")} />
            Service account: search for the user first. Needed for Windows single sign-on.
          </label>
        </fieldset>
        {ldap.bind_mode === "direct" && (
          <div className="field">
            <label className="field">
              <span className="label">User bind format</span>
              <input className="input mono" value={ldap.user_bind_format} aria-describedby="ldap-bind-format-hint"
                onChange={(e) => set("user_bind_format", e.target.value)} placeholder="{username}@corp.example.com" />
            </label>
            <small className="hint" id="ldap-bind-format-hint">
              {"{username}"} is replaced by the name the user typed (without any domain part). For Active Directory use{" "}
              {"{username}@your.domain"} or {"DOMAIN\\{username}"}. With a user search base, the user's own entry is read
              for groups. Without one, everyone gets the default role below.
            </small>
          </div>
        )}
        {ldap.bind_mode === "service" && (
        <div className="form-row">
          <label className="field">
            <span className="label">Service account (bind DN)</span>
            <input className="input mono" value={ldap.bind_dn ?? ""} onChange={(e) => set("bind_dn", e.target.value)}
              placeholder="CN=svc-openllm,OU=Service Accounts,DC=corp,DC=example,DC=com" />
          </label>
          <div className="field">
            <label className="field">
              <span className="label">Service account password</span>
              <input className="input" type="password" autoComplete="new-password" value={ldap.bind_password ?? ""}
                onChange={(e) => setLdap({ ...ldap, bind_password: e.target.value, clear_bind_password: false })}
                placeholder={saved.ldap.bind_password_set ? "•••••••• (stored; leave blank to keep)" : "not set"} />
            </label>
            {saved.ldap.bind_password_set && (
              <label className="small" style={{ display: "inline-flex", gap: 6 }}>
                <input type="checkbox" checked={!!ldap.clear_bind_password}
                  onChange={(e) => setLdap({ ...ldap, clear_bind_password: e.target.checked, bind_password: "" })} />
                Remove the stored password
              </label>
            )}
          </div>
        </div>
        )}
        <div className="form-row">
          <label className="field">
            <span className="label">User search base{ldap.bind_mode === "direct" ? " (optional)" : ""}</span>
            <input className="input mono" value={ldap.user_search_base} onChange={(e) => set("user_search_base", e.target.value)}
              placeholder="OU=Users,DC=corp,DC=example,DC=com" />
          </label>
          <div className="field">
            <label className="field">
              <span className="label">User filter</span>
              <input className="input mono" value={ldap.user_filter} aria-describedby="ldap-filter-hint"
                onChange={(e) => set("user_filter", e.target.value)} />
            </label>
            <small className="hint" id="ldap-filter-hint">{"{username}"} is replaced by the (escaped) name the user typed.</small>
          </div>
        </div>
        <details>
          <summary className="small">Attribute names</summary>
          <div className="form-row-3 form-row" style={{ marginTop: 10 }}>
            {(["username_attribute", "display_name_attribute", "email_attribute", "group_attribute"] as const).map((k) => (
              <label className="field" key={k}>
                <span className="label">{k.replace("_attribute", "").replace("_", " ")}</span>
                <input className="input mono" value={ldap[k]} onChange={(e) => set(k, e.target.value)} />
              </label>
            ))}
          </div>
        </details>
        <Switch label="Include nested groups" checked={ldap.nested_groups} onChange={(v) => set("nested_groups", v)} />
        <div className="page-actions">
          <button type="button" className="btn" disabled={busy}
            onClick={() => run(async () => setTest(await api.authSettings.testConnection(payload())))}>
            <Plug size={16} aria-hidden="true" /> Test connection
          </button>
        </div>
      </section>

      <section className="form-section">
        <div className="form-section-title">Groups and roles</div>
        <p className="small muted" style={{ margin: 0 }}>
          Checked at every sign-in. The highest matching role wins. Enter a group's full DN or just its name (CN). An
          administrator can still set a user's role by hand in Users; that pins it.
        </p>
        <div className="kv-list">
          {ldap.role_mappings.map((m, i) => (
            <div className="mapping-row" key={i}>
              <input className="input mono" aria-label={`Group ${i + 1}`} value={m.group} placeholder="CN=LLM Admins,OU=Groups,DC=corp,DC=example,DC=com"
                onChange={(e) => setMapping(i, { group: e.target.value })} />
              <select className="input" aria-label={`Role for group ${i + 1}`} value={m.role} onChange={(e) => setMapping(i, { role: e.target.value as Role })}>
                <option value="viewer">viewer</option>
                <option value="editor">editor</option>
                <option value="admin">admin</option>
              </select>
              <button type="button" className="icon-btn" aria-label={`Remove mapping ${i + 1}`}
                onClick={() => set("role_mappings", ldap.role_mappings.filter((_, j) => j !== i))}>
                <Trash2 size={16} />
              </button>
            </div>
          ))}
        </div>
        <button type="button" className="btn btn-sm" style={{ justifySelf: "start" }}
          onClick={() => set("role_mappings", [...ldap.role_mappings, { group: "", role: "viewer" }])}>
          <Plus size={14} aria-hidden="true" /> Add mapping
        </button>
        <div className="form-row">
          <label className="field">
            <span className="label">Allowed groups (one per line; empty = any directory user)</span>
            <textarea className="input mono" rows={2} value={allowed} onChange={(e) => setAllowed(e.target.value)} />
          </label>
          <label className="field">
            <span className="label">Users without a matching group</span>
            <select className="input" value={ldap.default_role} onChange={(e) => set("default_role", e.target.value as LdapSettingsInput["default_role"])}>
              <option value="viewer">Sign in as viewer</option>
              <option value="editor">Sign in as editor</option>
              <option value="deny">Refuse sign-in</option>
            </select>
          </label>
        </div>
        <div className="toolbar" style={{ padding: 0 }}>
          <input className="input" aria-label="Test username" placeholder="Username to test" value={testUser.username}
            onChange={(e) => setTestUser({ ...testUser, username: e.target.value })} style={{ maxWidth: 220 }} />
          <input className="input" type="password" aria-label="Test password" placeholder={ldap.bind_mode === "direct" ? "Password" : "Password (optional)"} autoComplete="off"
            value={testUser.password} onChange={(e) => setTestUser({ ...testUser, password: e.target.value })} style={{ maxWidth: 220 }} />
          <button type="button" className="btn" disabled={busy || !testUser.username.trim()}
            onClick={() => run(async () => setTest(await api.authSettings.testUser(payload(), testUser.username, testUser.password || null)))}>
            <UserSearch size={16} aria-hidden="true" /> Test user
          </button>
        </div>
        {test && <ResultCallout result={test} />}
      </section>

      <section className="form-section">
        <div className="form-section-title">Windows single sign-on (Kerberos)</div>
        <p className="small muted" style={{ margin: 0 }}>
          Optional. Domain-joined browsers sign in without a password. Needs a service principal (SPN) for this site's
          host name and its keytab; roles still come from the directory groups above.
        </p>
        {!saved.kerberos.library_available && (
          <Callout tone="warn">The Kerberos libraries are not installed in this API image, so single sign-on cannot work.</Callout>
        )}
        <Switch label="Enable Windows single sign-on" checked={kerberos.enabled} onChange={(v) => setKerberos({ ...kerberos, enabled: v })} />
        <div className="form-row">
          <label className="field">
            <span className="label">Service principal</span>
            <input className="input mono" value={kerberos.service_principal ?? ""} placeholder="HTTP/openllm.corp.example.com@CORP.EXAMPLE.COM"
              onChange={(e) => setKerberos({ ...kerberos, service_principal: e.target.value })} />
          </label>
          <div className="field">
            <label className="field">
              <span className="label">Keytab file</span>
              <input className="input" type="file" accept=".keytab,application/octet-stream" aria-describedby="keytab-hint"
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  setKeytabName(file.name);
                  setKerberos({ ...kerberos, keytab_base64: await fileToBase64(file), clear_keytab: false });
                }} />
            </label>
            <small className="hint" id="keytab-hint">
              {keytabName ? `${keytabName} will be uploaded on save.` : saved.kerberos.keytab_set ? "A keytab is stored (choose a file to replace it)." : "No keytab stored."}
            </small>
          </div>
        </div>
      </section>

      <div className="page-actions">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>
          <Save size={16} aria-hidden="true" /> Save authentication settings
        </button>
        {saved.updated_at && (
          <span className="small muted" style={{ alignSelf: "center" }}>
            Last saved {new Date(saved.updated_at).toLocaleString()} by {saved.updated_by ?? "—"}
          </span>
        )}
      </div>
    </div>
  );
}
