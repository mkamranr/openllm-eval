import {
  BarChart3,
  BrainCircuit,
  Database,
  FileText,
  FolderKanban,
  GitCompare,
  LayoutDashboard,
  LogOut,
  PlayCircle,
  Server,
  Settings,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "../../auth/AuthContext";
import { useReadiness } from "../../hooks/useReadiness";
import type { Role } from "../../types/auth";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Milestone that implements the page; shown as a "soon" chip until then. */
  soon?: string;
  /** Hidden from users below this role (the server enforces permissions regardless). */
  minRole?: Role;
}

export const NAV_SECTIONS: { title: string; items: NavItem[] }[] = [
  {
    title: "Overview",
    items: [
      { to: "/", label: "Dashboard", icon: LayoutDashboard },
      { to: "/new", label: "New Evaluation", icon: PlayCircle, minRole: "editor" },
    ],
  },
  {
    title: "Evaluate",
    items: [
      { to: "/runs", label: "Benchmark Runs", icon: BarChart3 },
      { to: "/compare", label: "Compare Runs", icon: GitCompare },
      { to: "/reports", label: "Reports", icon: FileText },
    ],
  },
  {
    title: "Configure",
    items: [
      { to: "/projects", label: "Projects", icon: FolderKanban },
      { to: "/endpoints", label: "Models / Endpoints", icon: Server },
      { to: "/datasets", label: "Datasets", icon: Database },
      { to: "/profiles", label: "Evaluation Profiles", icon: SlidersHorizontal },
    ],
  },
  {
    title: "System",
    items: [{ to: "/settings", label: "Settings", icon: Settings }],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((s) => s.items);

const ROLE_LABEL: Record<Role, string> = { viewer: "Viewer", editor: "Editor", admin: "Administrator" };

function UserBox() {
  const { me, signOut } = useAuth();
  const navigate = useNavigate();
  if (!me) return null;
  if (!me.auth_enabled) {
    return <div className="user-box"><div className="user-meta">Sign-in disabled</div></div>;
  }
  const name = me.user?.display_name || me.username;
  return (
    <div className="user-box">
      <div className="user-name" title={me.username}>{name}</div>
      <div className="user-meta">
        {ROLE_LABEL[me.role]}
        {me.user?.source === "ldap" ? " · Windows account" : ""}
      </div>
      <button
        type="button"
        className="btn btn-sm btn-ghost-light"
        onClick={async () => {
          await signOut();
          navigate("/login", { replace: true });
        }}
      >
        <LogOut size={14} aria-hidden="true" /> Sign out
      </button>
    </div>
  );
}

export function Layout() {
  const readiness = useReadiness();
  const healthy = readiness.data?.status === "ok";
  const { me, can } = useAuth();
  const visible = (item: NavItem) => !item.minRole || !me || can(item.minRole);
  return (
    <div className="app-shell">
      <nav className="sidebar" aria-label="Main">
        <div className="brand">
          <div className="brand-logo" aria-hidden="true">
            <BrainCircuit size={24} />
          </div>
          <div>
            <div className="brand-name">OpenLLM-Eval</div>
            <div className="brand-tagline">LLM Evaluation Framework</div>
          </div>
        </div>
        {NAV_SECTIONS.map((section) => (
          <div className="nav-section" key={section.title}>
            <div className="nav-section-title">{section.title}</div>
            <ul className="nav-list">
              {section.items.filter(visible).map(({ to, label, icon: Icon, soon }) => (
                <li key={to}>
                  <NavLink to={to} end={to === "/"} className="nav-link">
                    <Icon size={18} aria-hidden="true" />
                    <span>{label}</span>
                    {soon && (
                      <span className="nav-soon" title={`Planned for ${soon}`}>
                        soon
                      </span>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
        <div className="sidebar-footer">
          <UserBox />
          <div className="status-line">
            <span
              className="dot"
              style={{
                color: readiness.loading ? "#9aa3c0" : healthy ? "#4ade80" : "#f87171",
              }}
              aria-hidden="true"
            />
            {readiness.loading ? "Checking system…" : healthy ? "System healthy" : "System degraded"}
          </div>
          <div style={{ color: "var(--nav-muted)" }}>
            Self-hosted · no telemetry
          </div>
        </div>
      </nav>
      <main className="content">
        {me && !me.auth_enabled && (
          <div className="banner banner-warn" role="status">
            Sign-in is disabled (AUTH_ENABLED=false): anyone who can open this page has full administrator
            access.
          </div>
        )}
        {me?.auth_enabled && me.role === "viewer" && (
          <div className="banner banner-info" role="status">
            You have read-only access. Ask an administrator for the editor role to run evaluations.
          </div>
        )}
        <Outlet />
      </main>
    </div>
  );
}
