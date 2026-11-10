import { Archive, ArchiveRestore, Database, FolderKanban, Plus, Server, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import {
  Callout,
  ConfirmDialog,
  Drawer,
  EmptyState,
  PageHeader,
  Switch,
  formatRelative,
} from "../../components/ui";
import { useCanEdit } from "../../auth/AuthContext";
import { useAsync } from "../../hooks/useAsync";
import { ApiError, api } from "../../services/api";
import type { Project } from "../../types/api";

function ProjectForm({ onSaved, onClose }: { onSaved: (p: Project) => void; onClose: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string>();
  const [saving, setSaving] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setErrors({});
    setFormError(undefined);
    try {
      const project = await api.projects.create({
        name: name.trim(),
        description: description.trim() || null,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      });
      onSaved(project);
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(err.fieldErrors);
        setFormError(Object.keys(err.fieldErrors).length ? undefined : err.message);
      } else {
        setFormError(String(err));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <Drawer
      title="New project"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="project-form" className="btn btn-primary" disabled={saving}>
            {saving ? "Creating…" : "Create project"}
          </button>
        </>
      }
    >
      <form id="project-form" className="form" onSubmit={submit} noValidate>
        {formError && (
          <Callout tone="error" role="alert">
            {formError}
          </Callout>
        )}
        <div className="field">
          <label htmlFor="pr-name">Name</label>
          <input id="pr-name" className="input" value={name} required
            onChange={(e) => setName(e.target.value)} aria-invalid={errors.name ? true : undefined} />
          {errors.name && <span className="field-error">{errors.name}</span>}
        </div>
        <div className="field">
          <label htmlFor="pr-description">Description</label>
          <textarea id="pr-description" className="input" rows={3} value={description}
            onChange={(e) => setDescription(e.target.value)} />
          {errors.description && <span className="field-error">{errors.description}</span>}
        </div>
        <div className="field">
          <label htmlFor="pr-tags">Tags</label>
          <input id="pr-tags" className="input" value={tags} placeholder="customer-support, rag"
            onChange={(e) => setTags(e.target.value)} />
          <span className="hint">Comma-separated</span>
          {errors.tags && <span className="field-error">{errors.tags}</span>}
        </div>
      </form>
    </Drawer>
  );
}

export function ProjectsPage() {
  const canEdit = useCanEdit();
  const [showArchived, setShowArchived] = useState(false);
  const projects = useAsync(
    () => api.projects.list({ include_archived: showArchived }),
    [showArchived],
  );
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Project | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string }>();

  async function run(action: () => Promise<unknown>, success: string) {
    try {
      await action();
      setNotice({ tone: "ok", text: success });
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof Error ? err.message : String(err) });
    }
    await projects.reload();
  }

  const items = projects.data?.items ?? [];

  return (
    <div className="page">
      <PageHeader
        title="Projects"
        description="Group endpoints, datasets, and runs by use case. Endpoints can also be global."
        actions={
          <>
            <Switch checked={showArchived} onChange={setShowArchived} label="Show archived" />
            {canEdit && (
              <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
                <Plus size={16} /> New project
              </button>
            )}
          </>
        }
      />

      {notice && (
        <Callout tone={notice.tone} role={notice.tone === "error" ? "alert" : "status"}>
          {notice.text}
        </Callout>
      )}
      {projects.error && (
        <Callout tone="error" role="alert">
          Could not load projects: {projects.error.message}
        </Callout>
      )}

      {projects.loading && !projects.data ? (
        <p className="muted" role="status">
          Loading projects…
        </p>
      ) : items.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={FolderKanban}
            title="No projects yet"
            action={
              <button type="button" className="btn btn-primary btn-sm" onClick={() => setCreating(true)}>
                <Plus size={16} /> Create a project
              </button>
            }
          >
            Projects keep evaluations for one use case together, such as a support bot or a RAG
            assistant.
          </EmptyState>
        </div>
      ) : (
        <ul className="grid grid-3" aria-label="Projects" style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {items.map((p) => (
            <li key={p.id} className="card project-card">
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <h2 style={{ fontSize: "1.05rem", margin: 0 }}>{p.name}</h2>
                {p.archived_at && <span className="badge badge-neutral">Archived</span>}
              </div>
              {p.description && <p className="muted" style={{ margin: 0 }}>{p.description}</p>}
              {p.tags.length > 0 && (
                <div className="tags">
                  {p.tags.map((t) => (
                    <span className="tag" key={t}>
                      {t}
                    </span>
                  ))}
                </div>
              )}
              <div className="meta">
                <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <Server size={14} aria-hidden="true" /> {p.endpoint_count} endpoint
                  {p.endpoint_count === 1 ? "" : "s"}
                </span>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                  <Database size={14} aria-hidden="true" /> {p.dataset_count} dataset
                  {p.dataset_count === 1 ? "" : "s"}
                </span>
                <span>Created {formatRelative(p.created_at)}</span>
              </div>
              <div className="actions">
                {p.archived_at ? (
                  <button type="button" className="btn btn-sm"
                    onClick={() => void run(() => api.projects.update(p.id, { archived: false }), `Restored ${p.name}.`)}>
                    <ArchiveRestore size={14} /> Restore
                  </button>
                ) : (
                  <button type="button" className="btn btn-sm"
                    onClick={() => void run(() => api.projects.update(p.id, { archived: true }), `Archived ${p.name}.`)}>
                    <Archive size={14} /> Archive
                  </button>
                )}
                <button type="button" className="icon-btn danger" aria-label={`Delete ${p.name}`}
                  title="Delete" onClick={() => setDeleting(p)}>
                  <Trash2 size={16} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {creating && (
        <ProjectForm
          onClose={() => setCreating(false)}
          onSaved={(p) => {
            setCreating(false);
            setNotice({ tone: "ok", text: `Created ${p.name}.` });
            void projects.reload();
          }}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title={`Delete ${deleting.name}?`}
          confirmLabel="Delete project"
          onCancel={() => setDeleting(null)}
          onConfirm={() => {
            const target = deleting;
            setDeleting(null);
            void run(() => api.projects.remove(target.id), `Deleted ${target.name}.`);
          }}
        >
          {deleting.endpoint_count + deleting.dataset_count > 0
            ? `This also deletes its ${deleting.endpoint_count} project-scoped endpoint(s) and ${deleting.dataset_count} dataset(s). Archive instead to keep them.`
            : "This cannot be undone. Archive instead to hide it but keep its history."}
        </ConfirmDialog>
      )}
    </div>
  );
}
