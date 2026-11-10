import { BrainCircuit, LogIn, MonitorSmartphone } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";

import { useAuth } from "../../auth/AuthContext";
import { Callout } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { ApiError, api } from "../../services/api";
import type { Me } from "../../types/auth";

function safeNext(value: string | null): string {
  // Only same-site paths: never an absolute URL (open redirect).
  return value && value.startsWith("/") && !value.startsWith("//") ? value : "/";
}

export function LoginPage() {
  const { me, setMe } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const providers = useAsync(() => api.auth.providers(), []);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Sign in · OpenLLM-Eval";
    return () => {
      document.title = "OpenLLM-Eval";
    };
  }, []);

  if (me && !me.must_change_password) return <Navigate to={next} replace />;
  if (me?.must_change_password) return <Navigate to="/change-password" replace />;

  const done = (result: Me) => {
    setMe(result);
    navigate(result.must_change_password ? "/change-password" : next, { replace: true });
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      done(await api.auth.login(username, password));
    } catch (err) {
      setPassword("");
      setError(err instanceof ApiError ? err.message : "Could not reach the server.");
    } finally {
      setBusy(false);
    }
  };

  const windowsSignIn = async () => {
    setBusy(true);
    setError(null);
    try {
      done(await api.auth.negotiate());
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "Windows sign-in did not work on this computer. Sign in with your username and password."
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setBusy(false);
    }
  };

  const ldap = providers.data?.ldap;
  return (
    <div className="auth-screen">
      <div className="auth-card card">
        <div className="brand auth-brand">
          <div className="brand-logo" aria-hidden="true">
            <BrainCircuit size={24} />
          </div>
          <div>
            <div className="brand-name">OpenLLM-Eval</div>
            <div className="brand-tagline">LLM Evaluation Framework</div>
          </div>
        </div>
        <h1>Sign in</h1>
        {error && <Callout tone="error" role="alert">{error}</Callout>}
        {providers.data?.kerberos && (
          <>
            <button type="button" className="btn btn-block" onClick={windowsSignIn} disabled={busy}>
              <MonitorSmartphone size={16} aria-hidden="true" /> Sign in with Windows
            </button>
            <div className="auth-divider"><span>or</span></div>
          </>
        )}
        <form onSubmit={submit} className="form-grid">
          <label className="field">
            <span className="label">Username</span>
            <input
              className="input"
              name="username"
              autoComplete="username"
              autoFocus
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder={ldap ? "name or DOMAIN\\name" : undefined}
            />
          </label>
          <label className="field">
            <span className="label">Password</span>
            <input
              className="input"
              type="password"
              name="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <button type="submit" className="btn btn-primary btn-block" disabled={busy || !username || !password}>
            <LogIn size={16} aria-hidden="true" /> {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p className="small muted" style={{ marginBottom: 0 }}>
          {ldap
            ? "Use your OpenLLM-Eval account or your Windows (Active Directory) account."
            : "Use the account an administrator created for you."}
        </p>
      </div>
    </div>
  );
}
