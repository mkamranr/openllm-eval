import { Check, Database, Download, Plus, Search, Upload } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { useCanEdit } from "../../auth/AuthContext";
import { Callout, Card, EmptyState, PageHeader, TabPanel, Tabs, formatRelative } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { api } from "../../services/api";
import type { SampleDataset } from "../../types/api";
import { DATASET_TYPE_META, DATASET_TYPES, shortHash, typeLabel } from "./datasetMeta";
import { GeneratePanel } from "./GeneratePanel";
import { ImportPanel } from "./ImportPanel";

type Tab = "mine" | "samples" | "import" | "generate";

export function DatasetsPage() {
  const canEdit = useCanEdit();
  const [params, setParams] = useSearchParams();
  const tab = (["mine", "samples", "import", "generate"].includes(params.get("tab") ?? "") ? params.get("tab") : "mine") as Tab;
  const setTab = (t: Tab) => setParams(t === "mine" ? {} : { tab: t }, { replace: true });
  const navigate = useNavigate();

  const datasets = useAsync(() => api.datasets.list(), []);
  const samples = useAsync(() => api.datasets.samples(), []);
  const projects = useAsync(() => api.projects.list(), []);
  const [typeFilter, setTypeFilter] = useState("");
  const [search, setSearch] = useState("");
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string }>();
  const [installing, setInstalling] = useState<string | null>(null);

  const items = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (datasets.data?.items ?? []).filter(
      (d) =>
        (!typeFilter || d.dataset_type === typeFilter) &&
        (!q || [d.name, d.description ?? "", ...d.tags].some((v) => v.toLowerCase().includes(q))),
    );
  }, [datasets.data, typeFilter, search]);

  async function install(sample: SampleDataset) {
    setInstalling(sample.sample_id);
    try {
      const dataset = await api.datasets.installSample(sample.sample_id);
      setNotice({ tone: "ok", text: `Installed ${dataset.name}.` });
      await Promise.all([samples.reload(), datasets.reload()]);
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof Error ? err.message : String(err) });
    } finally {
      setInstalling(null);
    }
  }

  const projectName = (id: string | null) =>
    id ? (projects.data?.items.find((p) => p.id === id)?.name ?? "Unknown project") : "Global";

  return (
    <div className="page">
      <PageHeader
        icon={Database}
        title="Datasets"
        description="Versioned evaluation cases. Every version is immutable and identified by a SHA-256 content hash."
        actions={
          canEdit && tab !== "import" && (
            <button type="button" className="btn btn-primary" onClick={() => setTab("import")}>
              <Plus size={16} /> Import dataset
            </button>
          )
        }
      />

      {notice && (
        <Callout tone={notice.tone} role={notice.tone === "error" ? "alert" : "status"}>
          {notice.text}
        </Callout>
      )}

      <Card>
        <Tabs
          label="Dataset views"
          value={tab}
          onChange={setTab}
          items={[
            { key: "mine", label: "My datasets", count: datasets.data?.total },
            { key: "samples", label: "Built-in samples", count: samples.data?.length },
            { key: "import", label: "Import" },
            { key: "generate", label: "Generate" },
          ]}
        />

        {tab === "mine" && (
          <TabPanel id="mine">
            {datasets.error && (
              <Callout tone="error" role="alert">Could not load datasets: {datasets.error.message}</Callout>
            )}
            <div className="toolbar" style={{ padding: "0 0 14px" }}>
              <select className="input" aria-label="Filter by type" value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)} style={{ maxWidth: 240 }}>
                <option value="">All types</option>
                {DATASET_TYPES.map((t) => <option key={t} value={t}>{DATASET_TYPE_META[t].label}</option>)}
              </select>
              <div className="search">
                <Search size={16} aria-hidden="true" />
                <input className="input" type="search" placeholder="Search name, description, tag"
                  aria-label="Search datasets" value={search} onChange={(e) => setSearch(e.target.value)} />
              </div>
            </div>
            {datasets.loading && !datasets.data ? (
              <p className="muted" role="status">Loading datasets…</p>
            ) : items.length === 0 ? (
              <EmptyState
                icon={Database}
                title={datasets.data?.total ? "No datasets match" : "No datasets yet"}
                action={
                  !datasets.data?.total && (
                    <div className="page-actions" style={{ justifyContent: "center" }}>
                      <button type="button" className="btn btn-primary btn-sm" onClick={() => setTab("samples")}>
                        <Download size={16} /> Add a sample dataset
                      </button>
                      <button type="button" className="btn btn-sm" onClick={() => setTab("import")}>
                        <Upload size={16} /> Import your own
                      </button>
                    </div>
                  )
                }
              >
                {datasets.data?.total
                  ? "Try a different filter or search."
                  : "Import JSONL or CSV, or start from one of the bundled samples."}
              </EmptyState>
            ) : (
              <div className="table-wrap">
                <table className="table" aria-label="Datasets">
                  <thead>
                    <tr>
                      <th scope="col">Dataset</th>
                      <th scope="col">Type</th>
                      <th scope="col">Records</th>
                      <th scope="col">Version</th>
                      <th scope="col">Project</th>
                      <th scope="col">Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((d) => {
                      const v = d.current_version;
                      const Icon = DATASET_TYPE_META[d.dataset_type]?.icon ?? Database;
                      return (
                        <tr key={d.id} className="clickable" onClick={() => navigate(`/datasets/${d.id}`)}>
                          <td>
                            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                              <span className="sc-icon-sm" aria-hidden="true"><Icon size={16} /></span>
                              <div>
                                <Link to={`/datasets/${d.id}`} className="cell-main" onClick={(e) => e.stopPropagation()}>
                                  {d.name}
                                </Link>
                                {d.tags.length > 0 && (
                                  <div className="tags" style={{ marginTop: 4 }}>
                                    {d.tags.map((t) => <span className="tag" key={t}>{t}</span>)}
                                  </div>
                                )}
                              </div>
                            </div>
                          </td>
                          <td><span className="badge badge-violet">{typeLabel(d.dataset_type)}</span></td>
                          <td>{v ? v.record_count.toLocaleString() : "—"}</td>
                          <td>
                            {v ? (
                              <>
                                <div>v{v.version_number}{d.version_count > 1 && <span className="muted"> of {d.version_count}</span>}</div>
                                <div className="cell-sub hash" title={v.content_hash}>{shortHash(v.content_hash)}</div>
                              </>
                            ) : "—"}
                          </td>
                          <td>{projectName(d.project_id)}</td>
                          <td className="muted">{formatRelative(d.updated_at)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </TabPanel>
        )}

        {tab === "samples" && (
          <TabPanel id="samples">
            <p className="muted small" style={{ marginTop: 0 }}>
              Small, original synthetic datasets (CC0-1.0) that show each record format and give a quick smoke test.
              They are not benchmarks; don't use them to rank models.
            </p>
            {samples.error && <Callout tone="error" role="alert">{samples.error.message}</Callout>}
            <div className="select-grid" role="list" aria-label="Sample datasets">
              {(samples.data ?? []).map((s) => {
                const Icon = DATASET_TYPE_META[s.dataset_type]?.icon ?? Database;
                return (
                  <article key={s.sample_id} className="card sample-card" role="listitem">
                    <div className="head">
                      <span className="sc-icon" aria-hidden="true"><Icon size={18} /></span>
                      <div>
                        <h3 style={{ margin: 0, fontSize: "0.95rem" }}>{s.name.replace(/^Sample: /, "")}</h3>
                        <div className="small muted">{typeLabel(s.dataset_type)} · {s.record_count} cases · {s.license}</div>
                      </div>
                    </div>
                    <p className="small" style={{ margin: 0, color: "var(--text-secondary)" }}>{s.description}</p>
                    <div className="foot">
                      {s.installed_dataset_id ? (
                        <>
                          <span className="badge badge-ok"><Check size={12} /> Installed</span>
                          <Link className="btn btn-sm" to={`/datasets/${s.installed_dataset_id}`}>Open</Link>
                        </>
                      ) : (
                        <button type="button" className="btn btn-sm btn-primary" disabled={installing !== null}
                          onClick={() => void install(s)} aria-label={`Install ${s.name}`}>
                          <Download size={14} /> {installing === s.sample_id ? "Installing…" : "Install"}
                        </button>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          </TabPanel>
        )}

        {tab === "generate" && (
          <TabPanel id="generate">
            <div style={{ maxWidth: 820 }}>
              <GeneratePanel
                projects={projects.data?.items ?? []}
                onCreated={(d) => navigate(`/datasets/${d.id}`, { state: { created: true } })}
              />
            </div>
          </TabPanel>
        )}

        {tab === "import" && (
          <TabPanel id="import">
            <div style={{ maxWidth: 820 }}>
              <ImportPanel
                projects={projects.data?.items ?? []}
                onCreated={(d) => navigate(`/datasets/${d.id}`, { state: { created: true } })}
              />
            </div>
          </TabPanel>
        )}
      </Card>
    </div>
  );
}
