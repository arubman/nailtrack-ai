import { useEffect, useState } from "react";
import type { NailTrackClient } from "../api/client";
import { FINGERS, type FingerCode, type Treatment, type TreatmentCategory, type TreatmentDraft } from "../api/types";

const CATEGORIES: { value: TreatmentCategory; label: string }[] = [
  { value: "SUPPLEMENT_BIOTIN", label: "Biotin supplement" },
  { value: "TOPICAL_OIL", label: "Topical oil" },
  { value: "STRUCTURAL_REPAIR_WRAP_GLUE", label: "Wrap or glue repair" },
  { value: "BUILDER_GEL_OVERLAY", label: "Builder gel overlay" },
  { value: "GROOMING_TRIM", label: "Trim" },
  { value: "TRAUMA_NOTE", label: "Trauma note" },
];

export function TreatmentLogger({ client, finger, onChanged }: { client: NailTrackClient; finger: FingerCode; onChanged: () => void }) {
  const [items, setItems] = useState<Treatment[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function reload() {
    const page = await client.treatments();
    setItems(page.items);
  }

  useEffect(() => {
    void reload().catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not load treatments"));
  }, [client]);

  async function save(draft: TreatmentDraft, id: string | null) {
    setError(null);
    if (id) await client.updateTreatment(id, draft);
    else await client.createTreatment(draft);
    setEditing(null);
    await reload();
    onChanged();
  }

  async function end(item: Treatment) {
    await client.updateTreatment(item.id, { ended_at: new Date().toISOString() });
    await reload();
    onChanged();
  }

  return (
    <section className="panel" aria-labelledby="treatment-heading">
      <h2 id="treatment-heading">Treatments</h2>
      <p className="hint">Velocity modifiers are numbers you set. The app does not claim a treatment changes growth.</p>
      {error && <p role="alert">{error}</p>}
      <ul className="treatments">
        {items.map((item) => (
          <li key={item.id}>
            <div>
              <strong>{CATEGORIES.find((category) => category.value === item.category)?.label ?? item.category}</strong>
              <span>{item.target_fingers.join(", ") || "all fingers"} · ×{item.velocity_modifier}</span>
              <span>{item.ended_at ? "ended" : "active"}</span>
            </div>
            <div className="row">
              <button type="button" onClick={() => setEditing(item.id)}>Edit</button>
              {!item.ended_at && <button type="button" onClick={() => void end(item)}>End</button>}
            </div>
            {editing === item.id && (
              <TreatmentForm initial={item} finger={finger} onSubmit={(draft) => save(draft, item.id)} />
            )}
          </li>
        ))}
      </ul>
      <h3>New entry</h3>
      <TreatmentForm finger={finger} onSubmit={(draft) => save(draft, null)} />
    </section>
  );
}

function TreatmentForm({
  finger,
  initial,
  onSubmit,
}: {
  finger: FingerCode;
  initial?: Treatment;
  onSubmit: (draft: TreatmentDraft) => Promise<void>;
}) {
  const [category, setCategory] = useState<TreatmentCategory>(initial?.category ?? "TOPICAL_OIL");
  const [modifier, setModifier] = useState(String(initial?.velocity_modifier ?? 1));
  const [notes, setNotes] = useState(initial?.notes ?? "");
  const [targets, setTargets] = useState<string[]>(initial?.target_fingers ?? [finger]);
  const [busy, setBusy] = useState(false);

  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        setBusy(true);
        void onSubmit({
          started_at: initial?.started_at ?? new Date().toISOString(),
          category,
          target_fingers: targets,
          velocity_modifier: Number(modifier),
          notes: notes || null,
        }).finally(() => setBusy(false));
      }}
    >
      <label>
        Category
        <select value={category} onChange={(event) => setCategory(event.target.value as TreatmentCategory)}>
          {CATEGORIES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
        </select>
      </label>
      <fieldset>
        <legend>Fingers</legend>
        <div className="finger-grid">
          {FINGERS.map((code) => (
            <label key={code}>
              <input
                type="checkbox"
                checked={targets.includes(code)}
                onChange={(event) => {
                  setTargets(event.target.checked ? [...targets, code] : targets.filter((item) => item !== code));
                }}
              />
              {code.replace("_", " ")}
            </label>
          ))}
        </div>
      </fieldset>
      <label>
        Velocity modifier
        <input type="number" min={0.05} max={5} step={0.05} value={modifier} onChange={(event) => setModifier(event.target.value)} required />
      </label>
      <label>
        Notes
        <textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={2} />
      </label>
      <button type="submit" className="primary" disabled={busy}>{busy ? "Saving…" : initial ? "Update treatment" : "Add treatment"}</button>
    </form>
  );
}
