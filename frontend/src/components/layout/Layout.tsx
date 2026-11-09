import {
  BarChart3,
  BrainCircuit,
  Database,
  FileText,
  FolderKanban,
  GitCompare,
  LayoutDashboard,
  PlayCircle,
  Server,
  Settings,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

import { useReadiness } from "../../hooks/useReadiness";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Milestone that implements the page; shown as a "soon" chip until then. */
  soon?: string;
}

export const NAV_SECTIONS: { title: string; items: NavItem[] }[] = [
  {
    title: "Overview",
    items: [
      { to: "/", label: "Dashboard", icon: LayoutDashboard },
      { to: "/new", label: "New Evaluation", icon: PlayCircle },
    ],
  },
  {
    title: "Evaluate",
    items: [
      { to: "/runs", label: "Benchmark Runs", icon: BarChart3 },
      { to: "/compare", label: "Compare Runs", icon: GitCompare, soon: "M6" },
      { to: "/reports", label: "Reports", icon: FileText, soon: "M6" },
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
    items: [{ to: "/settings", label: "Settings", icon: Settings, soon: "later" }],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((s) => s.items);

export function Layout() {
  const readiness = useReadiness();
  const healthy = readiness.data?.status === "ok";
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
              {section.items.map(({ to, label, icon: Icon, soon }) => (
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
        <Outlet />
      </main>
    </div>
  );
}
