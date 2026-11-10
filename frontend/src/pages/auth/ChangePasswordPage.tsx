import { KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { useAuth } from "../../auth/AuthContext";
import { Callout } from "../../components/ui";
import { ApiError, api } from "../../services/api";

export const PASSWORD_RULES =
  "At least 12 characters and 5 different characters; not containing your username; different from the current password.";

/** Shared form: used on the forced first-sign-in page and in Settings > Account. */
export function ChangePasswordForm({ onDone }: { onDone: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mismatch = confirm.length > 0 && next !== confirm;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (next !== confirm) return;
    setBusy(true);
    setError(null);
    try {
      await api.auth.changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="form-grid" style={{ maxWidth: 420 }}>
      {error && <Callout tone="error" role="alert">{error}</Callout>}
      <label className="field">
        <span className="label">Current password</span>
        <input className="input" type="password" autoComplete="current-password" required
          value={current} onChange={(e) => setCurrent(e.target.value)} />
      </label>
      <div className="field">
        <label className="field">
          <span className="label">New password</span>
          <input className="input" type="password" autoComplete="new-password" required minLength={12}
            value={next} onChange={(e) => setNext(e.target.value)} aria-describedby="password-rules" />
        </label>
        <small id="password-rules" className="hint">{PASSWORD_RULES}</small>
      </div>
      <div className="field">
        <label className="field">
          <span className="label">Confirm new password</span>
          <input className="input" type="password" autoComplete="new-password" required
            value={confirm} onChange={(e) => setConfirm(e.target.value)} aria-invalid={mismatch}
            aria-describedby={mismatch ? "password-mismatch" : undefined} />
        </label>
        {mismatch && <small className="field-error" id="password-mismatch">The passwords do not match.</small>}
      </div>
      <button type="submit" className="btn btn-primary" disabled={busy || !current || next.length < 12 || mismatch}>
        <KeyRound size={16} aria-hidden="true" /> {busy ? "Saving…" : "Change password"}
      </button>
    </form>
  );
}

export function ChangePasswordPage() {
  const { me, refresh, signOut } = useAuth();
  const navigate = useNavigate();
  if (me === null) return <Navigate to="/login" replace />;
  if (me && !me.must_change_password) return <Navigate to="/" replace />;
  return (
    <div className="auth-screen">
      <div className="auth-card card">
        <h1>Choose a new password</h1>
        <p className="muted">
          {me?.username ? <strong>{me.username}</strong> : "Your account"} must change its password before continuing.
        </p>
        <ChangePasswordForm
          onDone={async () => {
            await refresh();
            navigate("/", { replace: true });
          }}
        />
        <button type="button" className="btn btn-link" onClick={() => void signOut()} style={{ marginTop: 12 }}>
          Sign out
        </button>
      </div>
    </div>
  );
}
