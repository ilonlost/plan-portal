import { useEffect, useState } from "react";
import { api } from "./api";
import type { LineData } from "./types";

export interface PFTaskOptions {
  sku: string; line_id: number | null; production_date: string; speed_kg_hour: number | null;
}
interface PFRow extends PFTaskOptions {
  name: string; quantity_kg: number; quantity_units: number | null; date_defaulted: boolean; note: string | null; errors: string[];
}
export interface PFPreview {
  mapping_revision?: string;
  calculation_revision?: string; quantity_units: number | null;
  items: PFRow[]; production_date: string; quantity_kg: number;
  gp_speed_kg_hour: number; gp_required_hours: number; gp_speed_override: boolean;
}
export interface PFSelection { key: string; ready: boolean; items: PFTaskOptions[]; mapping_revision?: string; calculation_revision?: string; }

export function PFExpansion({ planId, requestKey, lines, onChange }: {
  planId: number; requestKey: string; lines: LineData[]; onChange: (selection: PFSelection) => void;
}) {
  const [preview, setPreview] = useState<PFPreview | null>(null);
  const [rows, setRows] = useState<PFRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadedKey, setLoadedKey] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let current = true;
    setLoading(true); setError("");
    onChange({ key: requestKey, ready: false, items: [] });
    const timer = window.setTimeout(() => {
      api.previewPF(planId, JSON.parse(requestKey)).then(data => {
        if (!current) return;
        setPreview(data); setRows(data.items); setLoadedKey(requestKey); setLoading(false);
      }).catch(reason => { if (current) { setError(reason instanceof Error ? reason.message : String(reason)); setLoading(false); setPreview(null); } });
    }, 250);
    return () => { current = false; window.clearTimeout(timer); };
  }, [planId, requestKey, onChange, retry]);
  useEffect(() => {
    const ready = loadedKey === requestKey && !loading && !!preview && !error && rows.every(row => !!row.line_id && Number(row.speed_kg_hour) > 0 && !!row.production_date && row.production_date <= preview.production_date && !row.errors.some(value => value.includes("заблокирован")));
    onChange({ key: requestKey, ready, mapping_revision: preview?.mapping_revision, calculation_revision: preview?.calculation_revision, items: rows.map(({ sku, line_id, production_date, speed_kg_hour }) => ({ sku, line_id, production_date, speed_kg_hour })) });
  }, [rows, loading, loadedKey, preview, error, requestKey, onChange]);
  const update = (sku: string, values: Partial<PFRow>) => setRows(current => current.map(row => row.sku === sku ? { ...row, ...values } : row));
  return <section className="pf-expansion full" aria-label="Задания ПФ">
    <h3>Связанные задания ПФ</h3>
    <p>Объём ПФ рассчитывается по спецификации BOM на количество штук ГП после округления по кванту. Проверьте предложенные задания перед созданием.</p>
    {!loading && <button type="button" className="button secondary" onClick={() => setRetry(value => value + 1)}>Обновить расчёт ПФ</button>}
    {loading ? <p role="status">Расчёт ПФ…</p> : error ? <p className="inline-error" role="alert">{error}</p> : <>
      {preview?.quantity_units != null && <p>ГП после округления: <b>{Number(preview.quantity_kg).toLocaleString("ru-RU")} кг → {Number(preview.quantity_units).toLocaleString("ru-RU")} шт.</b> Эти штуки переданы в BOM.</p>}
      {preview?.gp_speed_override && <p>Этап ГП: {Number(preview.gp_speed_kg_hour).toLocaleString("ru-RU")} кг/ч · {Number(preview.gp_required_hours).toLocaleString("ru-RU")} ч (по настройкам связей).</p>}
      {!rows.length && <p>Для этого артикула связей ПФ в списке нет. Будет создано только ГП.</p>}
      {rows.map(row => <fieldset key={row.sku}>
        <legend>ПФ · {row.sku}</legend><p>{row.name}</p>
        <div className="form-grid">
          <label className="full">Линия ПФ<select value={row.line_id || ""} onChange={event => update(row.sku, { line_id: Number(event.target.value) || null, speed_kg_hour: null })}><option value="">Выберите линию</option>{lines.filter(line => line.status === "active").map(line => <option key={line.id} value={line.id}>{line.workshop_name} · {line.name}</option>)}</select></label>
          <label>Дата ПФ<input type="date" max={preview?.production_date} value={row.production_date} onChange={event => update(row.sku, { production_date: event.target.value })} /></label>
          <label>Скорость ПФ, кг/ч<input type="number" min="0.01" max="1000000" step="0.01" value={row.speed_kg_hour ?? ""} onChange={event => update(row.sku, { speed_kg_hour: event.target.value ? Number(event.target.value) : null })} /></label>
        </div>
        <p><b>{Number(row.quantity_kg).toLocaleString("ru-RU", { maximumFractionDigits: 6 })} кг</b> · {row.quantity_units == null ? "шт. — нет веса штуки в справочнике" : `${Number(row.quantity_units).toLocaleString("ru-RU", { maximumFractionDigits: 6 })} шт.`} · {row.speed_kg_hour ? `${Math.max(1, Math.ceil(Number(row.quantity_kg) / Number(row.speed_kg_hour) * 60))} мин` : "нужна скорость"}</p>
        {row.note && <small>{row.note}</small>}
        {row.date_defaulted && <p className="field-hint">Срок ПФ не указан в таблице. Предварительно: за 2 дня; при необходимости измените дату.</p>}
        {row.errors.filter(value => value.includes("заблокирован")).map(value => <p className="inline-error" key={value}>{value}</p>)}
      </fieldset>)}
      {!!rows.length && <small>Новые артикулы ПФ и недостающие связи с линиями будут добавлены в справочник при создании задания. Указанная скорость сохранится в задании.</small>}
    </>}
  </section>;
}
