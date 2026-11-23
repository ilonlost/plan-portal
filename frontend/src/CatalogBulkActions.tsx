import { useState } from "react";
import { api, type CatalogChange } from "./api";
import type { CatalogProduct, LineData } from "./types";

type Props = {
  products: CatalogProduct[];
  lines: LineData[];
  onSaved: (notice: string) => Promise<void>;
};

export function CatalogBulkActions({ products, lines, onSaved }: Props) {
  const [action, setAction] = useState("");
  const [lineId, setLineId] = useState("");
  const [speed, setSpeed] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const line = lines.find(item => item.status === "active" && item.id === Number(lineId));
  const kgPerHour = Number(speed.replace(",", "."));
  let change: CatalogChange | null = null;
  let affected: CatalogProduct[] = [];
  let description = "";
  if (action === "line" && line && kgPerHour > 0) {
    change = { action: "add_line", line_id: line.id, units_per_hour: kgPerHour };
    affected = products.filter(p => !p.capabilities.some(link => link.line_id === line.id));
    description = `Назначить линию «${line.name}» со скоростью ${kgPerHour} кг/ч`;
  } else if (action === "active" || action === "blocked") {
    const active = action === "active";
    change = { action: "set_status", status: action };
    affected = products.filter(p => p.catalog_status !== action || p.active !== active ||
      (active ? /блок/i.test(p.fk_status || "") : p.fk_status !== "Блокирован"));
    description = active ? "Активировать артикулы" : "Заблокировать артикулы";
  } else if (action === "az" || action === "plan") {
    const status = action === "az" ? "АЗ" : "По графику";
    change = { action: "set_date_status", advance_status: status };
    affected = products.filter(p => p.advance_status !== status);
    description = `Установить статус даты «${action === "az" ? "АЗ" : "План (по графику)"}»`;
  }

  async function apply() {
    if (!change || !affected.length || busy) return;
    if (!window.confirm(`${description}?\nБудет изменено карточек: ${affected.length} из ${products.length}.`)) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.updateCatalogSelection(affected.map(p => p.product_id), change);
      const notice = `Изменено карточек: ${result.changed}.${result.plan_recalculated ? " Активный план пересчитан." : ""}`;
      try { await onSaved(notice); }
      catch { setError(`${notice} Не удалось обновить таблицу. Обновите страницу.`); }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось применить изменения");
    } finally { setBusy(false); }
  }

  async function download() {
    setBusy(true);
    setError("");
    try { await api.exportCatalogSelection(products.map(p => p.product_id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось скачать файл"); }
    finally { setBusy(false); }
  }

  return <form className="catalog-bulk-form" aria-label="Массовые действия" onSubmit={event => { event.preventDefault(); void apply(); }}>
      <button type="button" className="button secondary" disabled={busy} onClick={() => void download()}>Скачать выбранные в XLSX</button>
      <select aria-label="Действие" disabled={busy} value={action} onChange={event => { setAction(event.target.value); setError(""); }}>
        <option value="">Выберите действие</option>
        <option value="line">Назначить линию</option>
        <option value="blocked">Заблокировать артикулы</option>
        <option value="active">Активировать артикулы</option>
        <option value="az">Статус даты — АЗ</option>
        <option value="plan">Статус даты — План (по графику)</option>
      </select>
      {action === "line" && <>
        <select aria-label="Линия" required disabled={busy} value={lineId} onChange={event => setLineId(event.target.value)}>
          <option value="">Выберите линию</option>
          {lines.filter(item => item.status === "active").map(item => <option key={item.id} value={item.id}>{item.workshop_name} · {item.name}</option>)}
        </select>
        <input aria-label="Скорость новых привязок, кг/ч" placeholder="Скорость, кг/ч" type="number" min="0.01" max="9999999999.99" step="0.01" required disabled={busy} value={speed} onChange={event => setSpeed(event.target.value)} />
      </>}
      {action && <button type="submit" className="button primary" disabled={busy || !affected.length}>Применить{change && ` (${affected.length})`}</button>}
      {change && !affected.length && <span role="status">У всех выбранных артикулов уже задано это значение.</span>}
      {error && <span role="alert" className="bad">{error}</span>}
  </form>;
}
