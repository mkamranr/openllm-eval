import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { AUTH_EVENT, ApiError, api, type AuthEventDetail } from "../services/api";
import type { Me, Role } from "../types/auth";

const RANK: Record<Role, number> = { viewer: 1, editor: 2, admin: 3 };

export interface AuthState {
  /** undefined while loading; null when nobody is signed in. */
  me: Me | null | undefined;
  error: string | null;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
  setMe: (me: Me | null) => void;
  /** True when the current role is at least ``role`` (the server enforces this anyway). */
  can: (role: Role) => boolean;
}

/**
 * Without a provider (unit tests rendering a single page), every action is allowed; in the app
 * the provider is always present and reflects the signed-in user's role.
 */
const AuthContext = createContext<AuthState>({
  me: undefined,
  error: null,
  refresh: async () => {},
  signOut: async () => {},
  setMe: () => {},
  can: () => true,
});

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

/** Shorthand for "may create, edit, or delete things" (editor or admin). */
export function useCanEdit(): boolean {
  return useAuth().can("editor");
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setMe(await api.auth.me());
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setMe(null);
        setError(null);
      } else {
        setMe(null);
        setError(err instanceof Error ? err.message : String(err));
      }
    }
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      setMe(null);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const onAuth = (event: Event) => {
      const detail = (event as CustomEvent<AuthEventDetail>).detail;
      if (detail === "signed-out") setMe(null);
      else setMe((current) => (current ? { ...current, must_change_password: true } : current));
    };
    window.addEventListener(AUTH_EVENT, onAuth);
    return () => window.removeEventListener(AUTH_EVENT, onAuth);
  }, [refresh]);

  const value = useMemo<AuthState>(
    () => ({
      me,
      error,
      refresh,
      signOut,
      setMe,
      can: (role) => (me ? RANK[me.role] >= RANK[role] : false),
    }),
    [me, error, refresh, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Routes behind sign-in: redirect to /login, or to /change-password when one is due. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { me, error } = useAuth();
  const location = useLocation();
  if (me === undefined) {
    return (
      <div className="auth-screen">
        <p className="muted" role="status">Checking your session…</p>
      </div>
    );
  }
  if (me === null) {
    const next = location.pathname + location.search;
    return (
      <Navigate
        to={next && next !== "/" ? `/login?next=${encodeURIComponent(next)}` : "/login"}
        replace
        state={{ error }}
      />
    );
  }
  if (me.must_change_password) return <Navigate to="/change-password" replace />;
  return <>{children}</>;
}
