import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { PFPreview, PFTaskOptions } from "./PFExpansion";
import type { ImportPFOffer } from "./types";
import "./ImportPFReview.css";

type Candidate = ImportPFOffer["candidates"][number];
type Line = { id: number; name: string; workshop_name?: string; status?: string };
type Item = PFPreview["items"][number];
type Result = { preview: PFPreview; items: Item[] } | { error: string };
const pageSize = 100;
const number = (value: number) => Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 6 });
const date = (value: string) => new Date(value + "T00:00:00").toLocaleDateString("ru-RU");
const message = (e: unknown) => e instanceof Error ? e.message : String(e);

function actionFor(error: string) {
  if (error.includes("нет положительной потребности")) return "Проверьте норму расхода ПФ в спецификации BOM. Если ПФ для этого выпуска не нужен, снимите выбор с ГП.";
  if (error.includes("отсутствует в расчёте BOM")) return "Сверьте артикул ПФ в спецификации с настройкой «ГП → ПФ» для этой линии. Исправьте связь или спецификацию и повторите расчёт.";
  return "";
}

function itemIssue(row: Item, parentDate: string) {
  return row.errors.find(reason => reason.includes("заблокирован")) ||
    (!row.line_id ? "Выберите линию ПФ" : "") ||
    (!(Number(row.speed_kg_hour) > 0 && Number(row.speed_kg_hour) <= 1000000) ? "Укажите положительную скорость до 1 000 000 кг/ч" : "") ||
    (!row.production_date || row.production_date > parentDate ? "Укажите дату не позже даты ГП" : "");
}

export function ImportPFReview({ initial, fileName, lines, onDone }: {
  initial: ImportPFOffer; fileName: string; lines: Line[]; onDone: (created: number) => Promise<void>;
}) {
  const [offer, setOffer] = useState(initial);
  const [selected, setSelected] = useState<number[]>([]);
  const [results, setResults] = useState<Record<number, Result>>({});
  const requests = useRef(new Map<number, AbortController>());
  const [page, setPage] = useState(0);
  const [fullHeight, setFullHeight] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const tableBox = useRef<HTMLDivElement>(null);

  // A large upload may contain hundreds of GPs. Limit simultaneous calls to BOM.
  useEffect(() => {
    const active = requests.current;
    return () => { active.forEach(request => request.abort()); active.clear(); };
  }, []);
  useEffect(() => {
    for (const id of selected) {
      if (requests.current.size >= 4) break;
      if (results[id] || requests.current.has(id)) continue;
      const controller = new AbortController();
      requests.current.set(id, controller);
      const finish = (result: Result) => {
        if (controller.signal.aborted) return;
        requests.current.delete(id);
        setResults(previous => ({ ...previous, [id]: result }));
      };
      api.previewImportPF(offer.plan_id, offer.order_id, id, controller.signal)
        .then(preview => finish(preview.items.length ? { preview, items: preview.items } : { error: "Действующие связи ПФ отсутствуют. Обновите список." }))
        .catch(e => finish({ error: message(e) }));
    }
  }, [selected, results, offer]);

  function select(ids: number[]) {
    const keep = new Set(ids);
    for (const [id, request] of requests.current) {
      if (!keep.has(id)) { request.abort(); requests.current.delete(id); }
    }
    setSelected(ids);
    setResults(previous => Object.fromEntries(Object.entries(previous).filter(([id]) => keep.has(Number(id)))));
  }
  function retry(id: number) {
    setResults(previous => { const next = { ...previous }; delete next[id]; return next; });
  }
  function update(id: number, sku: string, changes: Partial<PFTaskOptions>) {
    setResults(previous => {
      const result = previous[id];
      if (!result || !("items" in result)) return previous;
      return { ...previous, [id]: { ...result, items: result.items.map(item => item.sku === sku ? { ...item, ...changes } : item) } };
    });
  }
  const failed = selected.filter(id => results[id] && "error" in results[id]);
  const pending = selected.filter(id => !results[id]).length;
  const ready = selected.length > 0 && selected.every(id => {
    const result = results[id];
    return result && "items" in result && result.items.every(item => !itemIssue(item, result.preview.production_date));
  });
  const count = selected.reduce((n, id) => { const r = results[id]; return n + (r && "items" in r ? r.items.length : 0); }, 0);
  const refresh = async () => {
    setBusy(true); setError("");
    try {
      const current = await api.importPFOffer(offer.plan_id, offer.order_id);
      select([]); setOffer(current); setPage(0);
    } catch (e) { setError(message(e)); }
    finally { setBusy(false); }
  };
  const submit = async () => {
    if (!ready || busy) return;
    setBusy(true); setError("");
    try {
      const payload = selected.map(id => {
        const r = results[id];
        if (!("items" in r)) throw new Error(r.error);
        return { task_id: id, pf_tasks: r.items.map(({ sku, line_id, production_date, speed_kg_hour }) => ({ sku, line_id, production_date, speed_kg_hour })),
          pf_mapping_revision: r.preview.mapping_revision, pf_calculation_revision: r.preview.calculation_revision };
      });
      const result = await api.expandImportPF(offer, payload);
      await onDone(result.created);
    } catch (e) { setError(message(e)); }
    finally { setBusy(false); }
  };

  // The preview may pick up a changed mapping after a retry.
  const rows = offer.candidates.flatMap((gp: Candidate) => {
    const result = results[gp.task_id];
    const items = result && "items" in result ? result.items : undefined;
    return (items || Object.entries(gp.components).map(([sku, name]) => ({ sku, name })))
      .map((component, index) => ({ gp, component, item: items?.[index], result }));
  });
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const currentPage = Math.min(page, pages - 1);
  function go(next: number) { setPage(next); if (tableBox.current) tableBox.current.scrollTop = 0; }
  return <section className="card preview-card import-pf-review">
    <header><div><small>ПЛАН ЗАГРУЖЕН</small><h2>Задания на полуфабрикаты</h2><p>{fileName}</p></div></header>
    <p>Каждая строка — один ПФ. Выбор ГП включает все его полуфабрикаты.</p>
    {rows.length ? <fieldset disabled={busy} className="import-pf-fields">
      <div className="import-pf-toolbar">
        <label className="check-label"><input type="checkbox" checked={selected.length === offer.candidates.length}
          onChange={e => select(e.target.checked ? offer.candidates.map(row => row.task_id) : [])} />Выбрать все задания ГП ({offer.candidates.length})</label>
        <span role="status">Выбрано ГП: {selected.length}{pending > 0 && ` · В расчёте: ${pending}`}{failed.length > 0 && ` · Ошибок расчёта: ${failed.length}`}</span>
        {failed.length > 0 && <button className="button secondary" onClick={() => select(selected.filter(id => !failed.includes(id)))}>Снять выбор с ГП с ошибкой расчёта ({failed.length})</button>}
        <button className="button secondary" aria-pressed={fullHeight} onClick={() => setFullHeight(!fullHeight)}>{fullHeight ? "Свернуть таблицу" : "На всю высоту"}</button>
      </div>
      <div ref={tableBox} className={`table-scroll pf-review-table${fullHeight ? " expanded" : ""}`}><table aria-label="Задания на полуфабрикаты"><thead><tr>
        <th>Готовая продукция</th><th>ПФ · SKU / наименование</th><th>План ПФ</th><th>Дата ПФ</th><th>Линия ПФ</th><th>Скорость, кг/ч</th><th>Контроль</th>
      </tr></thead><tbody>
        {rows.slice(currentPage * pageSize, (currentPage + 1) * pageSize).map(({ gp, component, item, result }) => {
          const id = gp.task_id, checked = selected.includes(id);
          const errorText = result && "error" in result ? result.error : "";
          const issue = item ? itemIssue(item, gp.production_date) : "";
          return <tr key={id + "-" + component.sku}>
            <td><label className="check-label"><input type="checkbox" checked={checked} aria-label={`Выбрать ГП ${gp.sku}, задание ${id}`}
              onChange={e => select(e.target.checked ? [...selected, id] : selected.filter(value => value !== id))} /><span><b>{gp.sku}</b><small>{gp.name}</small></span></label>
              <small>{number(gp.quantity_kg)} кг · {date(gp.production_date)} · {gp.line_name} · {gp.shift === "night" ? "Ночь" : "День"}</small>
              {result && "preview" in result && result.preview.quantity_units != null && <small>В BOM передано {number(result.preview.quantity_units)} шт. ГП</small>}
              {result && "preview" in result && result.preview.gp_speed_override && <small>ГП по настройке связи · {number(result.preview.gp_speed_kg_hour)} кг/ч · {number(result.preview.gp_required_hours)} ч</small>}</td>
            <td><b>{component.sku}</b><small>{component.name}</small></td>
            <td>{item ? <><b>{number(item.quantity_kg)} кг</b>{item.quantity_units != null && <small>{number(item.quantity_units)} шт.</small>}</> : "—"}</td>
            <td>{item ? <input aria-label="Дата ПФ" type="date" value={item.production_date} max={gp.production_date} onChange={e => update(id, item.sku, { production_date: e.target.value })} /> : "—"}
              {item?.date_defaulted && <small>Предложена по умолчанию</small>}</td>
            <td>{item ? <select aria-label="Линия ПФ" value={item.line_id || ""} onChange={e => update(id, item.sku, { line_id: Number(e.target.value) || null, speed_kg_hour: null })}>
              <option value="">Выберите линию</option>{lines.filter(l => !l.status || l.status === "active").map(l => <option key={l.id} value={l.id}>{l.workshop_name} · {l.name}</option>)}</select> : "—"}</td>
            <td>{item ? <><input aria-label="Скорость ПФ, кг/ч" type="number" min="0.01" max="1000000" step="0.01" value={item.speed_kg_hour ?? ""}
              onChange={e => update(id, item.sku, { speed_kg_hour: e.target.value ? Number(e.target.value) : null })} />
              {Number(item.speed_kg_hour) > 0 && <small>{Math.max(1, Math.ceil(Number(item.quantity_kg) / Number(item.speed_kg_hour) * 60))} мин</small>}</> : "—"}</td>
            <td>{errorText ? <><span className="bad">{errorText}</span><small>{actionFor(errorText)}</small></> : item ? <><span className={issue ? "warn" : "good"}>{issue || "✓ Готово"}</span>{item.note && <small>{item.note}</small>}</> : checked ? "Расчёт…" : "Выберите ГП"}
              {checked && result && <button className="pf-retry" type="button" onClick={() => retry(id)}>Обновить расчёт</button>}</td>
          </tr>;
        })}
      </tbody></table></div>
      {pages > 1 && <div className="inline-actions import-pagination"><button className="button secondary" disabled={!currentPage} onClick={() => go(currentPage - 1)}>Назад</button><span>{currentPage + 1} / {pages}</span><button className="button secondary" disabled={currentPage + 1 >= pages} onClick={() => go(currentPage + 1)}>Далее</button></div>}
    </fieldset> : <p>В этом файле больше нет размещённых заданий ГП для раскрутки.</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    <footer><button className="button secondary" disabled={busy} onClick={() => void onDone(0)}>Завершить без новых ПФ</button>
      <button className="button secondary" disabled={busy} onClick={() => void refresh()}>Обновить список</button>
      <button className="button primary" disabled={!ready || busy} onClick={() => void submit()}>Создать задания ПФ{count ? ` (${count})` : ""}</button></footer>
  </section>;
}
