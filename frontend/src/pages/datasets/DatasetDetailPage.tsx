import { Copy, Database, Download, Search, Trash2, Upload } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

import {
  Callout,
  Card,
  ConfirmDialog,
  Drawer,
  EmptyState,
  PageHeader,
  TabPanel,
  Tabs,
  formatRelative,
} from "../../components/ui";
import { useCanEdit } from "../../auth/AuthContext";
import { useAsync } from "../../hooks/useAsync";
import { ApiError, api } from "../../services/api";
import type { Dataset, DatasetCase, DatasetVersion } from "../../types/api";
import { DATASET_TYPE_META, promptSnippet, shortHash, typeLabel } from "./datasetMeta";
import { ImportPanel } from "./ImportPanel";

type Tab = "records" | "versions" | "details";
const PAGE_SIZE = 25;

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="icon-btn"
      aria-label={label}
      title={copied ? "Copied" : label}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      <Copy size={14} />
    </button>
  );
}

function RecordsTab({ dataset, version }: { dataset: Dataset; version: DatasetVersion }) {
  const [tag, setTag] = useState("");
  const [caseType, setCaseType] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<DatasetCase | null>(null);
  const [full, setFull] = useState<DatasetCase | null>(null);

  useEffect(() => setOffset(0), [tag, caseType, search, version.id]);
  const page = useAsync(
    () =>
      api.datasets.preview(dataset.id, {
        version_id: version.id,
        tag: tag || undefined,
        case_type: caseType || undefined,
        q: search.trim() || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    [dataset.id, version.id, tag, caseType, search, offset],
  );

  useEffect(() => {
    setFull(null);
    if (open?.truncated) {
      void api.datasets.getCase(dataset.id, open.case_identifier, version.id).then(setFull);
    }
  }, [open, dataset.id, version.id]);

  const tags = Object.keys(version.tag_counts);
  const types = Object.keys(version.type_counts);
  const total = page.data?.total ?? 0;
  const shown = full ?? open;

  return (
    <>
      <div className="toolbar" style={{ padding: "0 0 14px" }}>
        <div className="page-actions">
          <select className="input" aria-label="Filter by tag" value={tag} onChange={(e) => setTag(e.target.value)}>
            <option value="">All tags</option>
            {tags.map((t) => <option key={t} value={t}>{t} ({version.tag_counts[t]})</option>)}
          </select>
          {types.length > 1 && (
            <select className="input" aria-label="Filter by case type" value={caseType} onChange={(e) => setCaseType(e.target.value)}>
              <option value="">All types</option>
              {types.map((t) => <option key={t} value={t}>{typeLabel(t)} ({version.type_counts[t]})</option>)}
            </select>
          )}
        </div>
        <div className="search">
          <Search size={16} aria-hidden="true" />
          <input className="input" type="search" placeholder="Search case ID" aria-label="Search case ID"
            value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
      </div>

      {page.error && <Callout tone="error" role="alert">{page.error.message}</Callout>}
      {!page.data ? (
        <p className="muted" role="status">Loading records…</p>
      ) : page.data.items.length === 0 ? (
        <EmptyState icon={Search} title="No matching records">Try a different tag or search.</EmptyState>
      ) : (
        <div className="table-wrap">
          <table className="table" aria-label="Records">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Case ID</th>
                <th scope="col">Prompt</th>
                <th scope="col">Type</th>
                <th scope="col">Tags</th>
              </tr>
            </thead>
            <tbody>
              {page.data.items.map((c) => (
                <tr key={c.position} className="clickable" onClick={() => setOpen(c)}>
                  <td className="muted">{c.position + 1}</td>
                  <td>
                    <button type="button" className="link-btn mono" onClick={(e) => { e.stopPropagation(); setOpen(c); }}>
                      {c.case_identifier}
                    </button>
                  </td>
                  <td>{promptSnippet(c.content)}</td>
                  <td><span className="badge badge-neutral">{typeLabel(c.case_type)}</span></td>
                  <td>
                    <div className="tags">{c.tags.map((t) => <span className="tag" key={t}>{t}</span>)}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {total > PAGE_SIZE && (
        <div className="toolbar" style={{ padding: "14px 0 0" }}>
          <span className="small muted">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <div className="page-actions">
            <button type="button" className="btn btn-sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
            <button type="button" className="btn btn-sm" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
          </div>
        </div>
      )}

      {open && shown && (
        <Drawer title={`Record ${open.case_identifier}`} onClose={() => setOpen(null)}>
          <div className="form">
            <div className="small muted">
              #{open.position + 1} · {typeLabel(open.case_type)} · version v{version.version_number}
              {open.truncated && !full && " · loading full record…"}
            </div>
            <pre className="code-block" aria-label="Record JSON">{JSON.stringify(shown.content, null, 2)}</pre>
          </div>
        </Drawer>
      )}
    </>
  );
}

function VersionsTab({
  dataset,
  versions,
  onChanged,
}: {
  dataset: Dataset;
  versions: DatasetVersion[];
  onChanged: (message: string) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string>();

  async function makeCurrent(v: DatasetVersion) {
    try {
      await api.datasets.update(dataset.id, { current_version_id: v.id });
      onChanged(`Version ${v.version_number} is now current.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <div className="toolbar" style={{ padding: "0 0 14px" }}>
        <span className="small muted">Versions are immutable. Uploading creates a new version and makes it current.</span>
        <button type="button" className="btn btn-sm btn-primary" onClick={() => setUploading(true)}>
          <Upload size={14} /> Upload new version
        </button>
      </div>
      {error && <Callout tone="error" role="alert">{error}</Callout>}
      <div className="table-wrap">
        <table className="table" aria-label="Versions">
          <thead>
            <tr>
              <th scope="col">Version</th>
              <th scope="col">SHA-256</th>
              <th scope="col">Records</th>
              <th scope="col">Source</th>
              <th scope="col">Created</th>
              <th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.id}>
                <td>
                  <div className="cell-main">
                    v{v.version_number} {v.is_current && <span className="badge badge-ok">Current</span>}
                  </div>
                  {v.version_label && <div className="cell-sub">{v.version_label}</div>}
                </td>
                <td>
                  <div style={{ display: "flex", alignItems: "center", gap: 4, maxWidth: 260 }}>
                    <span className="hash" title={v.content_hash}>{v.content_hash}</span>
                    <CopyButton text={v.content_hash} label={`Copy hash of v${v.version_number}`} />
                  </div>
                </td>
                <td>
                  {v.record_count.toLocaleString()}
                  {v.warning_count > 0 && <div className="cell-sub">{v.warning_count} warnings</div>}
                </td>
                <td>
                  <div>{v.source_format.toUpperCase()}</div>
                  {v.source_filename && <div className="cell-sub">{v.source_filename}</div>}
                </td>
                <td className="muted">{formatRelative(v.created_at)}</td>
                <td>
                  <div className="actions">
                    <a className="icon-btn" href={api.datasets.exportUrl(dataset.id, v.id)} download
                      aria-label={`Export v${v.version_number}`} title="Export JSONL">
                      <Download size={16} />
                    </a>
                    {!v.is_current && (
                      <button type="button" className="btn btn-sm" onClick={() => void makeCurrent(v)}>
                        Make current
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {uploading && (
        <Drawer title={`New version of ${dataset.name}`} onClose={() => setUploading(false)}>
          <ImportPanel
            dataset={dataset}
            onVersionCreated={(v) => {
              setUploading(false);
              onChanged(`Created version ${v.version_number} (${v.record_count} records).`);
            }}
          />
        </Drawer>
      )}
    </>
  );
}

function DetailsTab({ dataset, onSaved }: { dataset: Dataset; onSaved: (d: Dataset) => void }) {
  const projects = useAsync(() => api.projects.list(), []);
  const [name, setName] = useState(dataset.name);
  const [description, setDescription] = useState(dataset.description ?? "");
  const [tags, setTags] = useState(dataset.tags.join(", "));
  const [projectId, setProjectId] = useState(dataset.project_id ?? "");
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(undefined);
    try {
      onSaved(
        await api.datasets.update(dataset.id, {
          name: name.trim(),
          description: description.trim() || null,
          tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
          project_id: projectId || null,
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="form" onSubmit={submit} style={{ maxWidth: 720 }} aria-label="Dataset details">
      {error && <Callout tone="error" role="alert">{error}</Callout>}
      <div className="form-row">
        <div className="field">
          <label htmlFor="dd-name">Name</label>
          <input id="dd-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="dd-project">Project</label>
          <select id="dd-project" className="input" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            <option value="">Global (all projects)</option>
            {(projects.data?.items ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      </div>
      <div className="field">
        <label htmlFor="dd-description">Description</label>
        <textarea id="dd-description" className="input" rows={4} value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="dd-tags">Tags</label>
        <input id="dd-tags" className="input" value={tags} onChange={(e) => setTags(e.target.value)} />
      </div>
      <dl className="summary-list small">
        <div><dt>Type</dt><dd>{typeLabel(dataset.dataset_type)} (fixed once created)</dd></div>
        <div><dt>Source</dt><dd>{dataset.source === "sample" ? "Bundled sample" : "Imported"}</dd></div>
        {dataset.license && <div><dt>License</dt><dd>{dataset.license}</dd></div>}
        <div><dt>Created</dt><dd>{new Date(dataset.created_at).toLocaleString()}</dd></div>
      </dl>
      <div className="page-actions">
        <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? "Saving…" : "Save details"}</button>
      </div>
    </form>
  );
}

export function DatasetDetailPage() {
  const canEdit = useCanEdit();
  const { datasetId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const dataset = useAsync(() => api.datasets.get(datasetId), [datasetId]);
  const versions = useAsync(() => api.datasets.versions(datasetId), [datasetId]);
  const [tab, setTab] = useState<Tab>("records");
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string } | undefined>(
    (location.state as { created?: boolean } | null)?.created
      ? { tone: "ok", text: "Dataset imported." }
      : undefined,
  );
  const [confirmDelete, setConfirmDelete] = useState(false);

  const refresh = async (message?: string) => {
    if (message) setNotice({ tone: "ok", text: message });
    await Promise.all([dataset.reload(), versions.reload()]);
  };

  if (dataset.error) {
    return (
      <div className="page">
        <PageHeader title="Dataset" back={{ to: "/datasets", label: "Datasets" }} />
        <Callout tone="error" role="alert">{dataset.error.message}</Callout>
      </div>
    );
  }
  const d = dataset.data;
  if (!d) return <p className="muted" role="status">Loading dataset…</p>;
  const v = d.current_version;
  const Icon = DATASET_TYPE_META[d.dataset_type]?.icon ?? Database;

  async function duplicate() {
    try {
      const copy = await api.datasets.duplicate(d!.id);
      navigate(`/datasets/${copy.id}`, { state: { created: true } });
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof Error ? err.message : String(err) });
    }
  }

  return (
    <div className="page">
      <PageHeader
        icon={Icon}
        back={{ to: "/datasets", label: "Datasets" }}
        title={d.name}
        description={d.description ?? undefined}
        actions={
          <>
            {v && (
              <a className="btn" href={api.datasets.exportUrl(d.id)} download>
                <Download size={16} /> Export JSONL
              </a>
            )}
            {canEdit && v && (
              <button type="button" className="btn" onClick={() => void duplicate()}>
                <Copy size={16} /> Duplicate
              </button>
            )}
            {canEdit && (
              <button type="button" className="btn btn-danger-ghost" onClick={() => setConfirmDelete(true)} aria-label={`Delete ${d.name}`}>
                <Trash2 size={16} /> Delete
              </button>
            )}
          </>
        }
      />

      {notice && <Callout tone={notice.tone} role={notice.tone === "error" ? "alert" : "status"}>{notice.text}</Callout>}

      <Card>
        <div className="facts" aria-label="Dataset summary">
          <div><div className="k">Type</div><div className="v">{typeLabel(d.dataset_type)}</div></div>
          <div><div className="k">Records</div><div className="v">{v ? v.record_count.toLocaleString() : "—"}</div></div>
          <div>
            <div className="k">Current version</div>
            <div className="v">{v ? `v${v.version_number}` : "—"}<span className="muted small"> of {d.version_count}</span></div>
          </div>
          <div>
            <div className="k">Content hash (SHA-256)</div>
            <div className="v">
              {v ? (
                <>
                  <span className="hash" title={v.content_hash}>{shortHash(v.content_hash)}</span>
                  <CopyButton text={v.content_hash} label="Copy content hash" />
                </>
              ) : "—"}
            </div>
          </div>
        </div>
      </Card>

      <Card>
        <Tabs
          label="Dataset sections"
          value={tab}
          onChange={setTab}
          items={[
            { key: "records", label: "Records", count: v?.record_count },
            { key: "versions", label: "Versions", count: d.version_count },
            { key: "details", label: "Details" },
          ]}
        />
        {tab === "records" && (
          <TabPanel id="records">
            {v ? <RecordsTab dataset={d} version={v} /> : <EmptyState icon={Database} title="No versions yet" />}
          </TabPanel>
        )}
        {tab === "versions" && (
          <TabPanel id="versions">
            <VersionsTab dataset={d} versions={versions.data ?? []} onChanged={(m) => void refresh(m)} />
          </TabPanel>
        )}
        {tab === "details" && (
          <TabPanel id="details">
            <DetailsTab dataset={d} onSaved={() => void refresh("Details saved.")} />
          </TabPanel>
        )}
      </Card>

      {confirmDelete && (
        <ConfirmDialog
          title={`Delete ${d.name}?`}
          confirmLabel="Delete dataset"
          onCancel={() => setConfirmDelete(false)}
          onConfirm={() => {
            void api.datasets.remove(d.id).then(
              () => navigate("/datasets"),
              (err: Error) => {
                setConfirmDelete(false);
                setNotice({ tone: "error", text: err.message });
              },
            );
          }}
        >
          This permanently deletes all {d.version_count} version(s) and their records.
        </ConfirmDialog>
      )}
    </div>
  );
}
