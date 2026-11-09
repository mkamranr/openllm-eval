import { Plus, Trash2 } from "lucide-react";

export interface KeyValue {
  key: string;
  value: string;
}

export function toPairs(record: Record<string, unknown> | undefined): KeyValue[] {
  return Object.entries(record ?? {}).map(([key, value]) => ({
    key,
    value: typeof value === "string" ? value : JSON.stringify(value),
  }));
}

/** Rows with an empty key or value are dropped. */
export function fromPairs(pairs: KeyValue[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const { key, value } of pairs) {
    if (key.trim() && value.trim()) out[key.trim()] = value.trim();
  }
  return out;
}

export function KeyValueEditor({
  label,
  pairs,
  onChange,
  keyPlaceholder,
  valuePlaceholder,
  addLabel = "Add",
  error,
  hint,
}: {
  label: string;
  pairs: KeyValue[];
  onChange: (pairs: KeyValue[]) => void;
  keyPlaceholder: string;
  valuePlaceholder: string;
  addLabel?: string;
  error?: string;
  hint?: string;
}) {
  const update = (index: number, patch: Partial<KeyValue>) =>
    onChange(pairs.map((p, i) => (i === index ? { ...p, ...patch } : p)));
  return (
    <div className="field" role="group" aria-label={label}>
      <span className="label">{label}</span>
      {hint && <span className="hint">{hint}</span>}
      <div className="kv-list">
        {pairs.map((pair, index) => (
          <div className="kv-row" key={index}>
            <input
              className="input"
              placeholder={keyPlaceholder}
              aria-label={`${label} name ${index + 1}`}
              value={pair.key}
              onChange={(e) => update(index, { key: e.target.value })}
            />
            <input
              className="input"
              placeholder={valuePlaceholder}
              aria-label={`${label} value ${index + 1}`}
              value={pair.value}
              onChange={(e) => update(index, { value: e.target.value })}
            />
            <button
              type="button"
              className="icon-btn danger"
              aria-label={`Remove ${label} row ${index + 1}`}
              onClick={() => onChange(pairs.filter((_, i) => i !== index))}
            >
              <Trash2 size={16} />
            </button>
          </div>
        ))}
      </div>
      <div>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => onChange([...pairs, { key: "", value: "" }])}
        >
          <Plus size={14} /> {addLabel}
        </button>
      </div>
      {error && <span className="field-error">{error}</span>}
    </div>
  );
}
