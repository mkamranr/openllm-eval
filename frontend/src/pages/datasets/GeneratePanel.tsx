import { ScrollText } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Callout } from "../../components/ui";
import { ApiError, api } from "../../services/api";
import type { Dataset, Project } from "../../types/api";

interface Props {
  projects?: Project[];
  onCreated?: (dataset: Dataset) => void;
}

const parseList = (text: string, parse: (v: string) => number) =>
  text
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean)
    .map(parse);

/** Synthetic context-window datasets (needle-in-a-haystack, multi-fact retrieval). */
export function GeneratePanel({ projects = [], onCreated }: Props) {
  const [name, setName] = useState("");
  const [projectId, setProjectId] = useState("");
  const [lengths, setLengths] = useState("1024, 4096, 16384");
  const [depths, setDepths] = useState("0.1, 0.5, 0.9");
  const [facts, setFacts] = useState("1");
  const [repeats, setRepeats] = useState("1");
  const [seed, setSeed] = useState("0");
  const [error, setError] = useState<string>();
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const lengthList = parseList(lengths, Number);
  const depthList = parseList(depths, Number);
  const multi = Number(facts) > 1;
  const cells = lengthList.length * (multi ? 1 : depthList.length) * (Number(repeats) || 1);
  const tokens = lengthList.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0) * (multi ? 1 : depthList.length) * (Number(repeats) || 1);
  const invalid =
    !name.trim() ||
    lengthList.length === 0 ||
    lengthList.some((n) => !Number.isInteger(n)) ||
    depthList.some((d) => Number.isNaN(d));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    setFieldErrors({});
    try {
      const dataset = await api.datasets.generate({
        name: name.trim(),
        project_id: projectId || null,
        params: {
          context_lengths: lengthList,
          depths: depthList,
          needles_per_case: Number(facts),
          repeats: Number(repeats),
          seed: Number(seed),
        },
      });
      onCreated?.(dataset);
    } catch (err) {
      if (err instanceof ApiError) {
        setFieldErrors(err.fieldErrors);
        setError(err.message);
      } else {
        setError(String(err));
      }
    } finally {
      setBusy(false);
    }
  }

  const fieldError = (key: string) =>
    Object.entries(fieldErrors).find(([k]) => k === key || k.endsWith(`.${key}`))?.[1];

  return (
    <form className="form" onSubmit={submit} noValidate aria-label="Generate context-window dataset">
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
        <ScrollText size={20} aria-hidden="true" />
        <p className="small muted" style={{ margin: 0 }}>
          Builds deterministic needle-in-a-haystack cases: filler notes with an access code placed at each depth, and a
          question asking for it. Sizes are estimated at 4 characters per token; the actual input tokens are whatever the
          model server reports at run time. Run the result with the <strong>Context window</strong> evaluation type.
        </p>
      </div>
      {error && <Callout tone="error" role="alert">{error}</Callout>}
      <div className="form-row">
        <div className="field">
          <label htmlFor="gen-name">Name *</label>
          <input id="gen-name" className="input" value={name} onChange={(e) => setName(e.target.value)}
            placeholder="needle-1k-16k" required />
          {fieldError("name") && <span className="field-error">{fieldError("name")}</span>}
        </div>
        <div className="field">
          <label htmlFor="gen-project">Project</label>
          <select id="gen-project" className="input" value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            <option value="">Global (all projects)</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      </div>
      <div className="field">
        <label htmlFor="gen-lengths">Requested context lengths (tokens)</label>
        <input id="gen-lengths" className="input" value={lengths} onChange={(e) => setLengths(e.target.value)} />
        <span className="hint">Comma-separated, 128 to 524,288. Keep them within what your server allows (max_model_len), or the overflow is what you measure.</span>
        {fieldError("context_lengths") && <span className="field-error">{fieldError("context_lengths")}</span>}
      </div>
      <div className="form-row">
        <div className="field">
          <label htmlFor="gen-depths">Needle depths</label>
          <input id="gen-depths" className="input" value={depths} onChange={(e) => setDepths(e.target.value)}
            disabled={multi} />
          <span className="hint">0 = start, 1 = end. Ignored for multi-fact cases.</span>
          {fieldError("depths") && <span className="field-error">{fieldError("depths")}</span>}
        </div>
        <div className="field">
          <label htmlFor="gen-facts">Facts per case</label>
          <input id="gen-facts" className="input" type="number" min={1} max={10} value={facts}
            onChange={(e) => setFacts(e.target.value)} />
          <span className="hint">More than 1 = multi-fact retrieval (all facts asked at once).</span>
        </div>
      </div>
      <div className="form-row">
        <div className="field">
          <label htmlFor="gen-repeats">Repeats</label>
          <input id="gen-repeats" className="input" type="number" min={1} max={50} value={repeats}
            onChange={(e) => setRepeats(e.target.value)} />
          <span className="hint">Cases per length and depth, each with different facts.</span>
        </div>
        <div className="field">
          <label htmlFor="gen-seed">Seed</label>
          <input id="gen-seed" className="input" type="number" min={0} value={seed} onChange={(e) => setSeed(e.target.value)} />
          <span className="hint">Same settings and seed = identical dataset (same hash).</span>
        </div>
      </div>
      <p className="small muted" style={{ margin: 0 }} aria-live="polite">
        {Number.isFinite(tokens) && cells > 0
          ? `${cells} case${cells === 1 ? "" : "s"}, about ${tokens.toLocaleString()} tokens in total.`
          : "Enter at least one context length."}
      </p>
      <div>
        <button type="submit" className="btn btn-primary" disabled={busy || invalid}>
          {busy ? "Generating…" : "Generate dataset"}
        </button>
      </div>
    </form>
  );
}
