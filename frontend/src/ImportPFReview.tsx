import { Fragment, useCallback, useState } from "react";
import { api } from "./api";
import { PFExpansion, type PFSelection } from "./PFExpansion";
import type { ImportPFOffer } from "./types";

type Lines = { id: number; name: string; workshop_name?: string; status?: string }[];

export function ImportPFReview({ initial, fileName, lines, onDone }: {
  initial: ImportPFOffer; fileName: string; lines: Lines; onDone: (created: number) => Promise<void>;
}) {
  const [offer, setOffer] = useState(initial);
  const [selected, setSelected] = useState<number[]>([]);
  const [details, setDetails] = useState<Record<string, PFSelection>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const changed = useCallback((value: PFSelection) => setDetails(previous => ({ ...previous, [value.key]: value })), []);
  const select = (ids: number[]) => {
    setSelected(ids);
    setDetails(previous => Object.fromEntries(ids.filter(id => previous[id]).map(id => [id, previous[id]])));
  };
  const ready = selected.length > 0 && selected.every(id => details[id]?.ready && details[id].items.length);
  const count = selected.reduce((n, id) => n + (details[id]?.items.length || 0), 0);
  const refresh = async () => {
    setBusy(true); setError("");
    try {
      const current = await api.importPFOffer(offer.plan_id, offer.order_id);
      select([]); setOffer(current);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const submit = async () => {
    if (!ready || busy) return;
    setBusy(true); setError("");
    try {
      const result = await api.expandImportPF(offer, selected.map(id => ({ task_id: id, pf_tasks: details[id].items,
        pf_mapping_revision: details[id].mapping_revision, pf_calculation_revision: details[id].calculation_revision })));
      await onDone(result.created);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return <section className="card preview-card import-pf-review">
    <header><div><small>ПЛАН ЗАГРУЖЕН</small><h2>Задания на полуфабрикаты</h2><p>{fileName}</p></div></header>
    <p>Выберите задания ГП для расчёта по BOM. Для каждого ПФ можно уточнить линию, дату и скорость.</p>
    {offer.candidates.length ? <fieldset disabled={busy} className="import-pf-fields">
      <label className="check-label"><input type="checkbox" checked={selected.length === offer.candidates.length}
        onChange={e => select(e.target.checked ? offer.candidates.map(row => row.task_id) : [])} />Выбрать все задания ГП ({offer.candidates.length})</label>
      <div className="table-scroll"><table><thead><tr><th>Готовая продукция</th><th>Линия и смена</th><th>Объём ГП</th><th>Связанные ПФ</th></tr></thead>
        <tbody>{offer.candidates.map(row => <Fragment key={row.task_id}>
          <tr><td><label className="check-label"><input type="checkbox" checked={selected.includes(row.task_id)}
            onChange={e => select(e.target.checked ? [...selected, row.task_id] : selected.filter(id => id !== row.task_id))} />
            <span><b>{row.sku}</b><small>{row.name}</small></span></label></td>
            <td>{row.line_name}<small>{new Date(row.production_date + "T00:00:00").toLocaleDateString("ru-RU")} · {row.shift === "night" ? "Ночь" : "День"}</small></td>
            <td>{Number(row.quantity_kg).toLocaleString("ru-RU")} кг</td>
            <td>{Object.entries(row.components).map(([sku, name]) => <div key={sku}>{name} <small>{sku}</small></div>)}</td></tr>
          {selected.includes(row.task_id) && <tr><td colSpan={4}><PFExpansion planId={offer.plan_id} orderId={offer.order_id}
            requestKey={String(row.task_id)} lines={lines} onChange={changed} /></td></tr>}
        </Fragment>)}</tbody>
      </table></div>
    </fieldset> : <p>В этом файле больше нет размещённых заданий ГП для раскрутки.</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    <footer><button className="button secondary" disabled={busy} onClick={() => void onDone(0)}>Завершить без новых ПФ</button>
      <button className="button secondary" disabled={busy} onClick={() => void refresh()}>Обновить список</button>
      <button className="button primary" disabled={!ready || busy} onClick={() => void submit()}>Создать задания ПФ{count ? ` (${count})` : ""}</button></footer>
  </section>;
}
