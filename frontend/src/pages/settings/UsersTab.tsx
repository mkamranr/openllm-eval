import { KeyRound, LogOut, Pin, Plus, Search, Trash2, UserCog } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useAuth } from "../../auth/AuthContext";
import { Callout, ConfirmDialog, Drawer, Switch, formatRelative } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { ApiError, api } from "../../services/api";
import type { Role, User } from "../../types/auth";

const ROLES: Role[] = ["viewer", "editor", "admin"];
const SOURCE_LABEL: Record<User["source"], string> = {
  local: "Local",
  ldap: "Windows / AD",
  kerberos: "Windows / AD",
  service: "Service",
};

type Notice = { tone: "ok" | "error"; text: string; secret?: string };

function NewUserDrawer({ onClose, onCreated }: { onClose: () => void; onCreated: (n: Notice) => void }) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.users.create({
        username: username.trim().toLowerCase(),
        display_name: displayName.trim() || null,
        email: email.trim() || null,
        role,
      });
      onCreated({
        tone: "ok",
        text: `Created ${created.username} (${created.role}). Give them this one-time password; they must change it at first sign-in.`,
        secret: created.initial_password ?? undefined,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Drawer
      title="New local user"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" form="new-user" className="btn btn-primary" disabled={busy || !username.trim()}>Create user</button>
        </>
      }
    >
      <form id="new-user" onSubmit={submit} className="form-grid">
        {error && <Callout tone="error" role="alert">{error}</Callout>}
        <p className="small muted" style={{ margin: 0 }}>
          Windows / Active Directory users do not need to be created: they appear here after their first sign-in.
        </p>
        <div className="field">
          <label className="field">
            <span className="label">Username *</span>
            <input className="input" required pattern="[a-z0-9][a-z0-9._\-]{0,63}" value={username}
              aria-describedby="new-user-username-hint" onChange={(e) => setUsername(e.target.value.toLowerCase())} />
          </label>
          <small className="hint" id="new-user-username-hint">Lower-case letters, digits, dot, dash, underscore.</small>
        </div>
        <label className="field">
          <span className="label">Display name</span>
          <input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </label>
        <label className="field">
          <span className="label">Email</span>
          <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="field">
          <span className="label">Role</span>
          <select className="input" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        <p className="small muted" style={{ margin: 0 }}>A random password is generated and shown once.</p>
      </form>
    </Drawer>
  );
}

export function UsersTab() {
  const { me } = useAuth();
  const [q, setQ] = useState("");
  const users = useAsync(() => api.users.list(q || undefined), [q]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<User | null>(null);

  const act = async (fn: () => Promise<unknown>, ok: string, showSecret = false) => {
    try {
      const result = await fn();
      setNotice({ tone: "ok", text: ok, secret: showSecret ? (result as { temporary_password?: string | null }).temporary_password ?? undefined : undefined });
      await users.reload();
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof ApiError ? err.message : String(err) });
    }
  };

  return (
    <div className="form-grid" style={{ paddingTop: 8 }}>
      {notice && (
        <Callout tone={notice.tone} role={notice.tone === "error" ? "alert" : "status"}>
          <div>{notice.text}</div>
          {notice.secret && <div className="secret-box" aria-label="One-time password" style={{ marginTop: 8 }}>{notice.secret}</div>}
        </Callout>
      )}
      <div className="toolbar" style={{ padding: 0 }}>
        <div className="search">
          <Search size={16} aria-hidden="true" />
          <input className="input" type="search" placeholder="Search users" aria-label="Search users"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
          <Plus size={16} aria-hidden="true" /> New local user
        </button>
      </div>
      {users.error && <Callout tone="error" role="alert">Could not load users: {users.error.message}</Callout>}
      <div className="table-wrap">
        <table className="table" aria-label="Users">
          <thead>
            <tr>
              <th scope="col">User</th><th scope="col">Type</th><th scope="col">Role</th>
              <th scope="col">Status</th><th scope="col">Last sign-in</th><th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {(users.data?.items ?? []).map((u) => {
              const self = u.id === me?.user?.id;
              return (
                <tr key={u.id}>
                  <td>
                    <div><strong>{u.username}</strong>{self && <span className="small muted"> (you)</span>}</div>
                    {u.display_name && <div className="small muted">{u.display_name}</div>}
                  </td>
                  <td>{SOURCE_LABEL[u.source]}</td>
                  <td>
                    <select className="input" aria-label={`Role of ${u.username}`} value={u.role} disabled={self}
                      onChange={(e) => act(() => api.users.update(u.id, { role: e.target.value as Role }), `${u.username} is now ${e.target.value}.`)}
                      style={{ maxWidth: 120 }}>
                      {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                    {u.source === "ldap" && (
                      <div className="small muted" style={{ marginTop: 4 }}>
                        {u.role_pinned ? (
                          <button type="button" className="btn-link" onClick={() => act(() => api.users.update(u.id, { role_pinned: false }), `${u.username}'s role follows the group mapping again.`)}>
                            <Pin size={12} aria-hidden="true" /> pinned · use group mapping
                          </button>
                        ) : "from group mapping"}
                      </div>
                    )}
                  </td>
                  <td>
                    <Switch label={`${u.username} enabled`} hideLabel checked={!u.disabled} disabled={self}
                      onChange={(on) => act(() => api.users.update(u.id, { disabled: !on }), `${u.username} ${on ? "enabled" : "disabled and signed out"}.`)} />
                    {u.must_change_password && <div className="small muted">must change password</div>}
                  </td>
                  <td>{formatRelative(u.last_login_at)}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {u.source === "local" && (
                      <button type="button" className="icon-btn" aria-label={`Reset password of ${u.username}`} title="Reset password"
                        onClick={() => act(() => api.users.resetPassword(u.id), `New one-time password for ${u.username}; they must change it at sign-in.`, true)}>
                        <KeyRound size={16} />
                      </button>
                    )}
                    <button type="button" className="icon-btn" aria-label={`Sign out ${u.username} everywhere`} title="Sign out everywhere"
                      onClick={() => act(() => api.users.signOutEverywhere(u.id), `${u.username} was signed out of every browser.`)}>
                      <LogOut size={16} />
                    </button>
                    {!self && (
                      <button type="button" className="icon-btn" aria-label={`Delete ${u.username}`} title="Delete" onClick={() => setDeleting(u)}>
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
      {users.data && users.data.items.length === 0 && (
        <p className="muted"><UserCog size={14} aria-hidden="true" /> No users match.</p>
      )}
      {creating && (
        <NewUserDrawer
          onClose={() => setCreating(false)}
          onCreated={async (n) => {
            setCreating(false);
            setNotice(n);
            await users.reload();
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title={`Delete ${deleting.username}?`}
          confirmLabel="Delete user"
          onCancel={() => setDeleting(null)}
          onConfirm={async () => {
            const target = deleting;
            setDeleting(null);
            await act(() => api.users.remove(target.id), `Deleted ${target.username}.`);
          }}
        >
          Their sessions and API tokens stop working. Runs they started are kept. A Windows user can sign in again if
          their groups still allow it; disable them instead to block access.
        </ConfirmDialog>
      )}
    </div>
  );
}
