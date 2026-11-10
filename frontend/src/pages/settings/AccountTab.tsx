import { KeyRound, Plus, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useAuth } from "../../auth/AuthContext";
import { Callout, ConfirmDialog, formatRelative } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { ApiError, api } from "../../services/api";
import type { ApiToken, ApiTokenCreated } from "../../types/auth";
import { ChangePasswordForm } from "../auth/ChangePasswordPage";

function tokenStatus(t: ApiToken): { label: string; tone: string } {
  if (t.revoked_at) return { label: "revoked", tone: "badge-error" };
  if (t.expires_at && new Date(t.expires_at) <= new Date()) return { label: "expired", tone: "badge-warn" };
  return { label: "active", tone: "badge-ok" };
}

function TokensSection() {
  const tokens = useAsync(() => api.auth.tokens(), []);
  const [name, setName] = useState("");
  const [days, setDays] = useState("90");
  const [created, setCreated] = useState<ApiTokenCreated | null>(null);
  const [revoking, setRevoking] = useState<ApiToken | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setCreated(await api.auth.createToken(name.trim(), days === "never" ? null : Number(days)));
      setName("");
      await tokens.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="form-section">
      <div className="form-section-title"><KeyRound size={16} aria-hidden="true" /> API tokens</div>
      <p className="small muted" style={{ margin: 0 }}>
        For the command line and scripts: <code>OPENLLM_API_TOKEN=…</code> or <code>openllm-eval auth login</code>.
        A token acts with your current role.
      </p>
      {error && <Callout tone="error" role="alert">{error}</Callout>}
      {created && (
        <Callout tone="ok" role="status">
          <div>Copy the token <strong>{created.name}</strong> now. It will not be shown again.</div>
          <div className="secret-box" aria-label="New API token" style={{ marginTop: 8 }}>{created.token}</div>
          <button type="button" className="btn btn-sm" style={{ marginTop: 8 }} onClick={() => setCreated(null)}>
            Done
          </button>
        </Callout>
      )}
      <form onSubmit={create} className="toolbar" style={{ padding: 0 }}>
        <input className="input" aria-label="Token name" placeholder="Token name, e.g. ci-pipeline" required
          maxLength={100} value={name} onChange={(e) => setName(e.target.value)} style={{ maxWidth: 280 }} />
        <select className="input" aria-label="Token lifetime" value={days} onChange={(e) => setDays(e.target.value)} style={{ maxWidth: 160 }}>
          <option value="7">7 days</option>
          <option value="30">30 days</option>
          <option value="90">90 days</option>
          <option value="365">1 year</option>
          <option value="never">Never expires</option>
        </select>
        <button type="submit" className="btn btn-primary" disabled={busy || !name.trim()}>
          <Plus size={16} aria-hidden="true" /> Create token
        </button>
      </form>
      {tokens.data && tokens.data.length > 0 ? (
        <div className="table-wrap">
          <table className="table" aria-label="Your API tokens">
            <thead>
              <tr><th scope="col">Name</th><th scope="col">Token</th><th scope="col">Status</th><th scope="col">Created</th><th scope="col">Expires</th><th scope="col">Last used</th><th scope="col"><span className="sr-only">Actions</span></th></tr>
            </thead>
            <tbody>
              {tokens.data.map((t) => {
                const status = tokenStatus(t);
                return (
                  <tr key={t.id}>
                    <td>{t.name}</td>
                    <td><code>{t.prefix}…</code></td>
                    <td><span className={`badge ${status.tone}`}>{status.label}</span></td>
                    <td>{formatRelative(t.created_at)}</td>
                    <td>{t.expires_at ? new Date(t.expires_at).toLocaleDateString() : "never"}</td>
                    <td>{formatRelative(t.last_used_at)}</td>
                    <td>
                      {!t.revoked_at && (
                        <button type="button" className="icon-btn" aria-label={`Revoke ${t.name}`} onClick={() => setRevoking(t)}>
                          <Trash2 size={16} />
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        !tokens.loading && <p className="small muted" style={{ margin: 0 }}>No tokens yet.</p>
      )}
      {revoking && (
        <ConfirmDialog
          title={`Revoke "${revoking.name}"?`}
          confirmLabel="Revoke"
          onCancel={() => setRevoking(null)}
          onConfirm={async () => {
            try {
              await api.auth.revokeToken(revoking.id);
            } catch (err) {
              setError(err instanceof ApiError ? err.message : String(err));
            }
            setRevoking(null);
            await tokens.reload();
          }}
        >
          Scripts using this token will stop working immediately.
        </ConfirmDialog>
      )}
    </section>
  );
}

export function AccountTab() {
  const { me } = useAuth();
  const [changed, setChanged] = useState(false);
  if (!me) return null;
  const user = me.user;
  return (
    <div className="form" style={{ paddingTop: 8 }}>
      <section className="form-section">
        <div className="form-section-title">Your account</div>
        <dl className="kv">
          <dt>Username</dt><dd>{me.username}</dd>
          {user?.display_name && (<><dt>Name</dt><dd>{user.display_name}</dd></>)}
          {user?.email && (<><dt>Email</dt><dd>{user.email}</dd></>)}
          <dt>Role</dt><dd>{me.role}{user?.role_pinned ? " (set by an administrator)" : ""}</dd>
          <dt>Account type</dt><dd>{user?.source === "ldap" ? "Windows / Active Directory" : user?.source === "service" ? "Service account" : user ? "Local" : "—"}</dd>
          {user?.source === "ldap" && user.groups.length > 0 && (<><dt>Directory groups</dt><dd>{user.groups.length}</dd></>)}
        </dl>
      </section>
      {me.auth_enabled && user?.source === "local" && (
        <section className="form-section">
          <div className="form-section-title">Change password</div>
          {changed && <Callout tone="ok" role="status">Password changed. Your other sessions were signed out.</Callout>}
          <ChangePasswordForm onDone={() => setChanged(true)} />
        </section>
      )}
      {me.auth_enabled && user?.source === "ldap" && (
        <p className="small muted">Your password is managed in Windows / Active Directory.</p>
      )}
      {me.auth_enabled && user && <TokensSection />}
    </div>
  );
}
