/** Small presentational building blocks shared across pages. */
import { AlertTriangle, CheckCircle2, ChevronLeft, Info, X, XCircle, type LucideIcon } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";
import { Link } from "react-router-dom";

export function PageHeader({
  title,
  description,
  actions,
  icon: Icon,
  back,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  icon?: LucideIcon;
  back?: { to: string; label: string };
}) {
  return (
    <header className="page-header">
      <div>
        {back && (
          <Link to={back.to} className="back-link">
            <ChevronLeft size={14} aria-hidden="true" /> {back.label}
          </Link>
        )}
        <div className="title-row">
          {Icon && (
            <span className="page-icon" aria-hidden="true">
              <Icon size={20} />
            </span>
          )}
          <div>
            <h1>{title}</h1>
            {description && <p>{description}</p>}
          </div>
        </div>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export interface TabItem<K extends string> {
  key: K;
  label: ReactNode;
  count?: number;
}

/** Underline tabs (WAI-ARIA tabs pattern, arrow-key navigation). */
export function Tabs<K extends string>({
  items,
  value,
  onChange,
  label,
}: {
  items: TabItem<K>[];
  value: K;
  onChange: (key: K) => void;
  label: string;
}) {
  const onKeyDown = (e: React.KeyboardEvent, index: number) => {
    const delta = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!delta) return;
    e.preventDefault();
    const next = items[(index + delta + items.length) % items.length]!;
    onChange(next.key);
    const buttons = (e.currentTarget.parentElement?.querySelectorAll("button") ?? []) as NodeListOf<HTMLButtonElement>;
    buttons[(index + delta + items.length) % items.length]?.focus();
  };
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {items.map((item, index) => (
        <button
          key={item.key}
          type="button"
          role="tab"
          id={`tab-${item.key}`}
          aria-selected={value === item.key}
          aria-controls={`panel-${item.key}`}
          tabIndex={value === item.key ? 0 : -1}
          onClick={() => onChange(item.key)}
          onKeyDown={(e) => onKeyDown(e, index)}
        >
          {item.label}
          {item.count !== undefined && <span className="count">{item.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function TabPanel({ id, children }: { id: string; children: ReactNode }) {
  return (
    <div className="tab-panel" role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`}>
      {children}
    </div>
  );
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function Card({
  title,
  icon: Icon,
  action,
  children,
  flush = false,
  className = "",
}: {
  title?: ReactNode;
  icon?: LucideIcon;
  action?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
}) {
  return (
    <section className={`card ${flush ? "card-flush" : ""} ${className}`}>
      {(title || action) && (
        <div className="card-header">
          <div className="card-title">
            {Icon && <Icon size={20} aria-hidden="true" />}
            {title && <h2>{title}</h2>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function StatCard({
  label,
  value,
  unit,
  note,
  icon: Icon,
  tone,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  note?: ReactNode;
  icon: LucideIcon;
  tone: "violet" | "green" | "orange" | "blue";
}) {
  return (
    <div className="card stat">
      <div className={`stat-icon ${tone}`} aria-hidden="true">
        <Icon size={24} />
      </div>
      <div>
        <div className="stat-label">{label}</div>
        <div className="stat-value">
          {value}
          {unit && <span className="unit">{unit}</span>}
        </div>
        {note && <div className="stat-note">{note}</div>}
      </div>
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
}: {
  icon: LucideIcon;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon" aria-hidden="true">
        <Icon size={26} />
      </div>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

const CALLOUT_ICONS = { error: XCircle, warn: AlertTriangle, info: Info, ok: CheckCircle2 };

export function Callout({
  tone,
  children,
  role,
}: {
  tone: "error" | "warn" | "info" | "ok";
  children: ReactNode;
  role?: "alert" | "status";
}) {
  const Icon = CALLOUT_ICONS[tone];
  return (
    <div className={`callout callout-${tone}`} role={role}>
      <Icon size={18} aria-hidden="true" />
      <div>{children}</div>
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
  hideLabel = false,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  disabled?: boolean;
  hideLabel?: boolean;
}) {
  return (
    <label className="switch">
      <input
        type="checkbox"
        role="switch"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        aria-label={hideLabel ? label : undefined}
      />
      <span className="switch-track" aria-hidden="true" />
      {!hideLabel && <span>{label}</span>}
    </label>
  );
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);
}

/** Right-side panel for forms and detail views. */
export function Drawer({
  title,
  onClose,
  children,
  footer,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  useEscape(onClose);
  useEffect(() => {
    panel.current?.querySelector<HTMLElement>("input, select, textarea, button")?.focus();
  }, []);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="drawer" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={panel}>
        <div className="drawer-header">
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer && <div className="drawer-footer">{footer}</div>}
      </div>
    </div>
  );
}

export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  onConfirm,
  onCancel,
  busy,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy?: boolean;
}) {
  const titleId = useId();
  useEscape(onCancel);
  return (
    <div
      className="modal-backdrop center"
      onMouseDown={(e) => e.target === e.currentTarget && onCancel()}
    >
      <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby={titleId}>
        <h2 id={titleId}>{title}</h2>
        <div className="muted">{children}</div>
        <div className="page-actions" style={{ justifyContent: "flex-end" }}>
          <button type="button" className="btn" onClick={onCancel} autoFocus>
            Cancel
          </button>
          <button type="button" className="btn btn-danger" onClick={onConfirm} disabled={busy}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`;
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "never";
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}
