import { FileUp, ShieldCheck, Upload } from "lucide-react";
import { useRef, useState, type DragEvent, type FormEvent } from "react";

import { Callout, formatBytes } from "../../components/ui";
import { ApiError, api } from "../../services/api";
import type { Dataset, DatasetType, DatasetVersion, Project, ValidationResult } from "../../types/api";
import { DATASET_TYPES, DATASET_TYPE_META } from "./datasetMeta";
import { ValidationReport } from "./ValidationReport";

type Source = "file" | "paste";

interface Props {
  /** Present when uploading a new version of an existing dataset. */
  dataset?: Dataset;
  projects?: Project[];
  onCreated?: (dataset: Dataset) => void;
  onVersionCreated?: (version: DatasetVersion) => void;
}

export function ImportPanel({ dataset, projects = [], onCreated, onVersionCreated }: Props) {
  const [name, setName] = useState("");
  const [datasetType, setDatasetType] = useState<DatasetType>("qa");
  const [projectId, setProjectId] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [label, setLabel] = useState("");
  const [source, setSource] = useState<Source>("file");
  const [file, setFile] = useState<File | null>(null);
  const [pasted, setPasted] = useState("");
  const [dragging, setDragging] = useState(false);
  const [report, setReport] = useState<ValidationResult>();
  const [error, setError] = useState<string>();
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<"validate" | "import" | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const type = dataset?.dataset_type ?? datasetType;

  async function readContent(): Promise<{ content: string; filename: string | null } | null> {
    if (source === "file") {
      if (!file) return null;
      return { content: await file.text(), filename: file.name };
    }
    return pasted.trim() ? { content: pasted, filename: null } : null;
  }

  function resetResult() {
    setReport(undefined);
    setError(undefined);
  }

  function handleApiError(err: unknown) {
    if (err instanceof ApiError) {
      const details = (err.body as { error?: { details?: unknown } } | undefined)?.error?.details;
      if (err.code === "dataset_invalid" && details && typeof details === "object") {
        setReport(details as ValidationResult);
        setError(err.message);
        return;
      }
      setFieldErrors(err.fieldErrors);
      setError(err.message);
      return;
    }
    setError(String(err));
  }

  async function validate() {
    resetResult();
    const data = await readContent();
    if (!data) {
      setError(source === "file" ? "Choose a file first." : "Paste some JSONL or CSV first.");
      return;
    }
    setBusy("validate");
    try {
      setReport(await api.datasets.validate({ ...data, dataset_type: type }));
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(null);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    resetResult();
    setFieldErrors({});
    const data = await readContent();
    if (!data) {
      setError(source === "file" ? "Choose a file first." : "Paste some JSONL or CSV first.");
      return;
    }
    setBusy("import");
    try {
      if (dataset) {
        onVersionCreated?.(await api.datasets.addVersion(dataset.id, { ...data, version_label: label.trim() || null }));
      } else {
        onCreated?.(
          await api.datasets.create({
            ...data,
            name: name.trim(),
            dataset_type: datasetType,
            project_id: projectId || null,
            description: description.trim() || null,
            tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
            version_label: label.trim() || null,
          }),
        );
      }
    } catch (err) {
      handleApiError(err);
    } finally {
      setBusy(null);
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) {
      setFile(dropped);
      resetResult();
    }
  };

  return (
    <form className="form" onSubmit={submit} noValidate aria-label={dataset ? "Upload new version" : "Import dataset"}>
      {!dataset && (
        <>
          <div className="form-row">
            <div className="field">
              <label htmlFor="ds-name">Name</label>
              <input id="ds-name" className="input" value={name} onChange={(e) => setName(e.target.value)}
                placeholder="support-questions" aria-invalid={fieldErrors.name ? true : undefined} />
              {fieldErrors.name && <span className="field-error">{fieldErrors.name}</span>}
            </div>
            <div className="field">
              <label htmlFor="ds-type">Dataset type</label>
              <select id="ds-type" className="input" value={datasetType}
                onChange={(e) => { setDatasetType(e.target.value as DatasetType); resetResult(); }}>
                {DATASET_TYPES.map((t) => (
                  <option key={t} value={t}>{DATASET_TYPE_META[t].label}</option>
                ))}
              </select>
              <span className="hint">Fields: {DATASET_TYPE_META[datasetType].hint}</span>
            </div>
          </div>
          <div className="form-row">
            <div className="field">
              <label htmlFor="ds-project">Project</label>
              <select id="ds-project" className="input" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                <option value="">Global (all projects)</option>
                {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="ds-tags">Tags</label>
              <input id="ds-tags" className="input" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="support, english" />
              <span className="hint">Comma-separated</span>
            </div>
          </div>
          <div className="field">
            <label htmlFor="ds-description">Description</label>
            <textarea id="ds-description" className="input" rows={2} value={description}
              onChange={(e) => setDescription(e.target.value)} />
          </div>
        </>
      )}

      <div className="field">
        <span className="label">Content</span>
        <div className="segmented" role="group" aria-label="Content source" style={{ justifySelf: "start" }}>
          <button type="button" aria-pressed={source === "file"} onClick={() => { setSource("file"); resetResult(); }}>Upload file</button>
          <button type="button" aria-pressed={source === "paste"} onClick={() => { setSource("paste"); resetResult(); }}>Paste</button>
        </div>
      </div>

      {source === "file" ? (
        <label
          className={`dropzone ${dragging ? "active" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".jsonl,.ndjson,.csv,application/x-ndjson,text/csv"
            aria-label="Dataset file"
            onChange={(e) => { setFile(e.target.files?.[0] ?? null); resetResult(); }}
          />
          <FileUp size={26} aria-hidden="true" style={{ color: "var(--primary)" }} />
          {file ? (
            <span><strong>{file.name}</strong> · {formatBytes(file.size)}</span>
          ) : (
            <span><strong>Drop a .jsonl or .csv file</strong> or click to browse</span>
          )}
          <span className="small muted">UTF-8. One JSON object per line, or a CSV with a header row.</span>
        </label>
      ) : (
        <div className="field">
          <label htmlFor="ds-paste" className="sr-only">Dataset content</label>
          <textarea id="ds-paste" className="input code" rows={8} value={pasted}
            onChange={(e) => { setPasted(e.target.value); resetResult(); }}
            placeholder={'{"id": "case-001", "type": "qa", "input": "…", "reference_answer": "…"}'} spellCheck={false} />
        </div>
      )}

      <div className="field">
        <label htmlFor="ds-label">Version label</label>
        <input id="ds-label" className="input" value={label} onChange={(e) => setLabel(e.target.value)}
          placeholder={dataset ? "e.g. fixed typos in 3 answers" : "e.g. initial import"} />
      </div>

      {error && !report && <Callout tone="error" role="alert">{error}</Callout>}
      {report && <ValidationReport result={report} />}

      <div className="page-actions">
        <button type="button" className="btn" onClick={() => void validate()} disabled={busy !== null}>
          <ShieldCheck size={16} /> {busy === "validate" ? "Validating…" : "Validate"}
        </button>
        <button type="submit" className="btn btn-primary" disabled={busy !== null}>
          <Upload size={16} /> {busy === "import" ? "Importing…" : dataset ? "Upload version" : "Import dataset"}
        </button>
      </div>
    </form>
  );
}
