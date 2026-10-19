import { useState } from "react";
import { api } from "./api";
import { ProductionImport } from "./ProductionImport";
import { TaskPreviewTable } from "./TaskPreviewTable";
import type { PlannerAdministration, ScheduleItem } from "./types";

export function LineTaskManagement({ data, onChanged }: { data: PlannerAdministration; onChanged: () => Promise<void> }) {
  const [lineId, setLineId] = useState(""); const [mode, setMode] = useState("delete");
  const [start, setStart] = useState(new Date().toLocaleDateString("sv-SE")); const [end, setEnd] = useState(start);
  const [wholeFile, setWholeFile] = useState(false);
  const [preview, setPreview] = useState<{ plan_id: number; version: number; count: number; items: ScheduleItem[] } | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const reset = () => { setPreview(null); setError(""); setNotice(""); };
  const line = data.lines.find(row => row.id === Number(lineId));
  const valid = !!start && !!end && end >= start && (new Date(end).getTime() - new Date(start).getTime()) / 86400000 <= 92;
  const inspect = async () => { setBusy(true); reset(); try { setPreview(await api.plannerLineTasks(line?.id, start, end)); } catch (reason) { setError(String(reason instanceof Error ? reason.message : reason)); } finally { setBusy(false); } };
  const remove = async () => { if (!preview) return; setBusy(true); setError(""); try { const result = await api.bulkDeleteItems(preview.plan_id, start, end, preview.version, line?.id); setPreview(null); setNotice(`Исключено заданий: ${result.deleted_count}`); await onChanged(); } catch (reason) { setPreview(null); setError(String(reason instanceof Error ? reason.message : reason)); } finally { setBusy(false); } };
  return <div className="stack"><section className="card admin-panel"><header><h2>Удаление и прогрузка заданий</h2><p>Выберите область, проверьте список и подтвердите действие. Изменение фильтров сбрасывает предпросмотр.</p></header>
    <div className="inline-actions"><button className={`button ${mode === "delete" ? "primary" : "secondary"}`} disabled={busy} onClick={() => { setMode("delete"); reset(); }}>Удаление</button><button className={`button ${mode === "import" ? "primary" : "secondary"}`} disabled={busy} onClick={() => { setMode("import"); reset(); }}>Прогрузка из файла</button></div>
    <div className="form-grid"><label className="full">Линия<select disabled={busy} value={lineId} onChange={e => { setLineId(e.target.value); reset(); }}><option value="">{mode === "delete" ? "Все линии" : "Выберите одну линию"}</option>{data.lines.map(row => <option value={row.id} key={row.id}>{row.workshop_name} · {row.name}</option>)}</select></label>
      {mode === "import" && <label className="check-label full"><input type="checkbox" checked={wholeFile} disabled={busy} onChange={e => { setWholeFile(e.target.checked); reset(); }} />Все даты файла на выбранной линии</label>}
      {(mode === "delete" || !wholeFile) && <><label>С<input type="date" disabled={busy} value={start} onChange={e => { setStart(e.target.value); reset(); }} /></label><label>По<input type="date" disabled={busy} min={start} value={end} onChange={e => { setEnd(e.target.value); reset(); }} /></label></>}
    </div>
    {mode === "delete" && <><p>Исключаются производственные задания, включая ручные. ТО и ручные мойки сохраняются. Для одного дня укажите одинаковые даты.</p><button className="button secondary" disabled={busy || !valid || !data.active_plan} onClick={() => void inspect()}>Показать задания к удалению</button></>}
    {!data.active_plan && <p>Сначала загрузите общий производственный план.</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}{notice && <p className="good" role="status">{notice}</p>}
  </section>
    {preview && mode === "delete" && <><TaskPreviewTable key={`${preview.version}-${lineId}-${start}-${end}`} items={preview.items} title={`К удалению: ${line?.name || "Все линии"} · ${start} — ${end}`} />{preview.count > 0 && <button disabled={busy} className="button danger" onClick={() => void remove()}>Подтвердить исключение {preview.count} заданий</button>}</>}
    {data.active_plan && line && mode === "import" && (wholeFile || valid) && <ProductionImport key={`${line.id}-${wholeFile}-${start}-${end}`} fixedLineId={line.id} startDate={wholeFile ? undefined : start} endDate={wholeFile ? undefined : end} lines={data.lines} onImported={async text => { setNotice(text); await onChanged(); }} />}
  </div>;
}
