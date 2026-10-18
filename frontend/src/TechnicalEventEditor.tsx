import { useState } from "react";
import { api } from "./api";
import type { ScheduleItem } from "./types";

export function TechnicalEventEditor({ item, planId, version, onSaved }: { item: ScheduleItem; planId: number; version: number; onSaved: (message: string) => Promise<void> }) {
  const initialMinutes = Math.round(Number(item.duration_hours ?? item.required_hours) * 60);
  const [hours, setHours] = useState(Math.floor(initialMinutes / 60)); const [minutes, setMinutes] = useState(initialMinutes % 60);
  const [sequence, setSequence] = useState(item.sequence); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const total = hours * 60 + minutes;
  const save = async () => {
    setBusy(true); setError("");
    try { await api.updateItem(planId, item.id, { duration_minutes: total, sequence, locked: true, comment: "Изменение длительности и порядка события" }, version); await onSaved("Длительность и порядок события сохранены"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось сохранить событие"); }
    finally { setBusy(false); }
  };
  return <form onSubmit={event => { event.preventDefault(); void save(); }}><div className="form-grid"><label>Часы<input type="number" min={0} max={24} step={1} value={hours} onChange={event => setHours(Number(event.target.value))} /></label><label>Минуты<input type="number" min={0} max={59} step={1} value={minutes} onChange={event => setMinutes(Number(event.target.value))} /></label><label className="full">Порядковый номер в дне<input type="number" min={1} step={1} value={sequence} onChange={event => setSequence(Number(event.target.value))} /></label></div>{item.workshop_code === "PC" && <p>После правки мойки этого дня управляются вручную и не пересоздаются автоматически.</p>}{error && <p className="inline-error" role="alert">{error}</p>}<button className="button primary wide" disabled={busy || !Number.isInteger(total) || hours < 0 || minutes < 0 || minutes > 59 || total < 1 || total > 1440 || !Number.isInteger(sequence) || sequence < 1}>{busy ? "Сохранение…" : "Сохранить событие"}</button></form>;
}
