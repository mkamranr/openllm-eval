import { Pencil, Plus, SlidersHorizontal, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { Callout, ConfirmDialog, Drawer, EmptyState, PageHeader, formatRelative } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { ApiError, api } from "../../services/api";
import type { Profile } from "../../types/runs";
import { EVALUATION_TYPES, IMPLEMENTED_TYPES, evaluationType } from "../evaluation/evaluationTypes";
import {
  DEFAULT_PERFORMANCE,
  PerformanceSettingsForm,
  fromRunConfig,
  toRunConfig,
  validatePerformance,
} from "../evaluation/performanceSettings";
import {
  DEFAULT_QUALITY,
  QUALITY_TYPES,
  QualitySettingsForm,
  fromEvaluationConfig,
  toEvaluationConfig,
  validateQuality,
} from "../evaluation/qualitySettings";

function ProfileForm({
  profile,
  onSaved,
  onClose,
}: {
  profile: Profile | null;
  onSaved: (p: Profile) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(profile?.name ?? "");
  const [description, setDescription] = useState(profile?.description ?? "");
  const [suites, setSuites] = useState<string[]>(profile?.suites ?? ["performance"]);
  const [performance, setPerformance] = useState(
    profile ? fromRunConfig(profile.load, profile.generation) : DEFAULT_PERFORMANCE,
  );
  const [quality, setQuality] = useState(profile ? fromEvaluationConfig(profile.evaluation) : DEFAULT_QUALITY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const endpoints = useAsync(() => api.endpoints.list(), []);

  const perfErrors = validatePerformance(performance);
  const qualityTypes = suites.filter((s) => QUALITY_TYPES.includes(s));
  const valid =
    name.trim().length > 0 &&
    suites.length > 0 &&
    Object.keys(perfErrors).length === 0 &&
    Object.keys(validateQuality(quality)).length === 0;

  function toggle(key: string) {
    setSuites((s) => (s.includes(key) ? s.filter((x) => x !== key) : [...s, key]));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!valid) return;
    setSaving(true);
    setErrors({});
    setFormError(undefined);
    const body = {
      name: name.trim(),
      description: description.trim() || null,
      suites,
      evaluation: toEvaluationConfig(quality),
      ...toRunConfig(performance),
    };
    try {
      onSaved(profile ? await api.profiles.update(profile.id, body) : await api.profiles.create(body));
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
      title={profile ? `Edit ${profile.name}` : "New profile"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" form="profile-form" className="btn btn-primary" disabled={saving || !valid}>
            {saving ? "Saving…" : profile ? "Save changes" : "Create profile"}
          </button>
        </>
      }
    >
      <form id="profile-form" className="form" onSubmit={submit} noValidate>
        {formError && <Callout tone="error" role="alert">{formError}</Callout>}
        <div className="field">
          <label htmlFor="pf-name">Name</label>
          <input id="pf-name" className="input" value={name} required maxLength={200}
            onChange={(e) => setName(e.target.value)} aria-invalid={errors.name ? true : undefined} />
          {errors.name && <span className="field-error">{errors.name}</span>}
        </div>
        <div className="field">
          <label htmlFor="pf-description">Description</label>
          <textarea id="pf-description" className="input" rows={2} value={description}
            onChange={(e) => setDescription(e.target.value)} />
        </div>
        <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="label">Evaluation types</legend>
          {EVALUATION_TYPES.filter((t) => IMPLEMENTED_TYPES.includes(t.key)).map((t) => (
            <label key={t.key} className="checkbox-row">
              <input type="checkbox" checked={suites.includes(t.key)} onChange={() => toggle(t.key)} />
              <span>{t.title}</span>
            </label>
          ))}
          {suites.length === 0 && <span className="field-error">Select at least one type.</span>}
          {errors.suites && <span className="field-error">{errors.suites}</span>}
        </fieldset>
        {qualityTypes.length > 0 && (
          <QualitySettingsForm value={quality} types={qualityTypes} endpoints={endpoints.data?.items ?? []}
            onChange={(patch) => setQuality((q) => ({ ...q, ...patch }))} />
        )}
        <PerformanceSettingsForm value={performance} errors={perfErrors}
          onChange={(patch) => setPerformance((p) => ({ ...p, ...patch }))} />
      </form>
    </Drawer>
  );
}

export function ProfilesPage() {
  const profiles = useAsync(() => api.profiles.list(), []);
  const [editing, setEditing] = useState<Profile | "new" | null>(null);
  const [deleting, setDeleting] = useState<Profile | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string }>();
  const items = profiles.data?.items ?? [];

  async function remove(p: Profile) {
    try {
      await api.profiles.remove(p.id);
      setNotice({ tone: "ok", text: `Deleted ${p.name}.` });
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof Error ? err.message : String(err) });
    }
    await profiles.reload();
  }

  return (
    <div className="page">
      <PageHeader
        title="Evaluation Profiles"
        description="Reusable evaluation types and settings. Runs copy a profile's settings when they start, so editing a profile never changes past runs."
        actions={
          <button type="button" className="btn btn-primary" onClick={() => setEditing("new")}>
            <Plus size={16} /> New profile
          </button>
        }
      />
      {notice && <Callout tone={notice.tone} role={notice.tone === "error" ? "alert" : "status"}>{notice.text}</Callout>}
      {profiles.error && <Callout tone="error" role="alert">Could not load profiles: {profiles.error.message}</Callout>}

      {profiles.loading && !profiles.data ? (
        <p className="muted" role="status">Loading profiles…</p>
      ) : items.length === 0 ? (
        <div className="card">
          <EmptyState icon={SlidersHorizontal} title="No profiles yet"
            action={
              <button type="button" className="btn btn-primary btn-sm" onClick={() => setEditing("new")}>
                <Plus size={16} /> Create a profile
              </button>
            }>
            Save a combination of evaluation types, load and quality settings, then pick it in{" "}
            <Link to="/new">New Evaluation</Link> or reference it by name from the CLI.
          </EmptyState>
        </div>
      ) : (
        <ul className="grid grid-3" aria-label="Profiles" style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {items.map((p) => (
            <li key={p.id} className="card project-card">
              <h2 style={{ fontSize: "1.05rem", margin: 0 }}>{p.name}</h2>
              {p.description && <p className="muted" style={{ margin: 0 }}>{p.description}</p>}
              <div className="tags">
                {p.suites.map((s) => <span className="tag" key={s}>{evaluationType(s)?.title ?? s}</span>)}
              </div>
              <div className="meta">
                <span>{p.load.profile ?? "smoke"} load · concurrency {p.load.concurrency ?? 1}</span>
                {p.suites.includes("tool_calling") && (
                  <span>tools {p.evaluation.tool_mode === "generation_only" ? "inspected only" : "simulated"}</span>
                )}
                <span>Updated {formatRelative(p.updated_at)}</span>
              </div>
              <div className="actions">
                <button type="button" className="btn btn-sm" onClick={() => setEditing(p)} aria-label={`Edit ${p.name}`}>
                  <Pencil size={14} /> Edit
                </button>
                <button type="button" className="icon-btn danger" aria-label={`Delete ${p.name}`} title="Delete"
                  onClick={() => setDeleting(p)}>
                  <Trash2 size={16} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <ProfileForm
          profile={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(p) => {
            setNotice({ tone: "ok", text: `${editing === "new" ? "Created" : "Saved"} ${p.name}.` });
            setEditing(null);
            void profiles.reload();
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title={`Delete ${deleting.name}?`}
          confirmLabel="Delete profile"
          onCancel={() => setDeleting(null)}
          onConfirm={() => {
            const target = deleting;
            setDeleting(null);
            void remove(target);
          }}
        >
          Runs that used this profile keep their copied settings.
        </ConfirmDialog>
      )}
    </div>
  );
}
