import { useState } from "react";
import { api } from "./api";
import { ProductionImport } from "./ProductionImport";
import type { PlannerAdministration } from "./types";

export function LineTaskManagement({ data, onChanged }: { data: PlannerAdministration; onChanged: () => Promise<void> }) {
  const [lineId, setLineId] = useState("");
  const [mode, setMode] = useState("delete");
  const [start, setStart] = useState(data.active_plan?.start || new Date().toLocaleDateString("sv-SE"));
  const [end, setEnd] = useState(data.active_plan?.end || start);
  const [preview, setPreview] = useState<{ plan_id: number; version: number; count: number } | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [notice, setNotice] = useState("");
  const reset = () => { setPreview(null); setError(""); setNotice(""); };
  const line = data.lines.find(row => row.id === Number(lineId));
  const inspect = async () => {
    setBusy(true); reset();
    try { setPreview(await api.plannerLineTasks(Number(lineId), start, end)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось проверить задания"); }
    finally { setBusy(false); }
  };
  const remove = async () => {
    if (!preview || !line) return;
    setBusy(true); setError("");
    try {
      const result = await api.bulkDeleteItems(preview.plan_id, start, end, preview.version, line.id);
      setPreview(null); setNotice(`Линия «${line.name}»: исключено заданий — ${result.deleted_count}`); await onChanged();
    } catch (reason) { setPreview(null); setError(reason instanceof Error ? reason.message : "Не удалось удалить задания"); }
    finally { setBusy(false); }
  };
  return <div className="stack">
    <section className="card admin-panel"><header><h2>Задания по линии</h2><p>Массовое удаление за день или период и загрузка обычных файлов на одну производственную линию.</p></header>
      {!data.active_plan ? <p>Сначала загрузите общий производственный план.</p> : <>
        <div className="form-grid"><label className="full">Производственная линия<select disabled={busy} value={lineId} onChange={event => { setLineId(event.target.value); reset(); }}><option value="">Выберите линию</option>{data.lines.map(row => <option key={row.id} value={row.id}>{row.workshop_name} · {row.name}</option>)}</select></label></div>
        <div className="inline-actions"><button disabled={busy} className={`button ${mode === "delete" ? "primary" : "secondary"}`} onClick={() => { setMode("delete"); reset(); }}>Удалить задания</button><button disabled={busy} className={`button ${mode === "import" ? "primary" : "secondary"}`} onClick={() => { setMode("import"); reset(); }}>Загрузить файл на линию</button></div>
        {line && mode === "delete" && <>
          <p>Удаляются производственные задания выбранной линии, включая созданные вручную. ТО и ручные мойки сохраняются. Для одного дня укажите одинаковые даты.</p>
          <div className="form-grid"><label>С<input disabled={busy} type="date" value={start} onChange={event => { setStart(event.target.value); reset(); }} /></label><label>По<input disabled={busy} type="date" min={start} value={end} onChange={event => { setEnd(event.target.value); reset(); }} /></label></div>
          <button disabled={busy || !start || !end || end < start} className="button secondary" onClick={() => void inspect()}>Показать количество заданий</button>
          {preview && <div className="card admin-panel" role="status"><p>Линия «{line.name}» · {start.split("-").reverse().join(".")} — {end.split("-").reverse().join(".")}</p><p><b>{preview.count ? `Будет исключено заданий: ${preview.count}` : "За этот период заданий нет"}</b></p>{preview.count > 0 && <button disabled={busy} className="button danger" onClick={() => void remove()}>Подтвердить удаление {preview.count} заданий</button>}</div>}
        </>}
      </>}
      {error && <p className="inline-error" role="alert">{error}</p>}{notice && <p className="good" role="status">{notice}</p>}
    </section>
    {data.active_plan && line && mode === "import" && <ProductionImport key={line.id} fixedLineId={line.id} lines={data.lines} onImported={async text => { setNotice(text); await onChanged(); }} />}
  </div>;
}
