import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "./api";
import { ProductionDashboard } from "./ProductionDashboard";

type Totals = { day_kg: number | null; night_kg: number | null; total_kg: number | null; rows: number; orders: number; missing_weight_rows: number };
type ProductionRow = {
  production_date: string; center: string; center_name: string; sku: string; product_name: string;
  order_number: string; order_line: number; variant: number; lot: string; plan_date: string | null;
  planned_start_date: string | null; plan_quantity: number | null; plan_kg: number | null;
  quantity_unit: string; storage_unit: string; kg_per_unit: number | null;
  day_quantity: number | null; night_quantity: number | null; day_storage: number | null; night_storage: number | null;
  day_kg: number | null; night_kg: number | null; total_kg: number | null; warning: string;
};
export type ProductionReport = {
  source: string; generated_at: string; note: string; total_count: number; summary: Totals;
  centers: { code: string; name: string }[];
  by_center: (Totals & { code: string; name: string })[];
  by_day: (Totals & { date: string })[]; rows: ProductionRow[];
  by_task: { order_number: string; order_line: number; sku: string; center: string; center_name: string;
    product_name: string; plan_kg: number | null; fact_kg: number | null;
    progress_percent: number | null;
    fact_by_day: { date: string; fact_kg: number | null; progress_percent: number | null;
      status: "completed" | "pending" | "unknown"; status_reason: string }[];
    status: "completed" | "pending" | "unknown"; status_reason: string }[];
};
const num = (value: number | null | undefined, digits = 2) => value == null ? "—" : value.toLocaleString("ru-RU", { maximumFractionDigits: digits });
const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" }).format(new Date());

export function ProductionFact({ onError, shipment = false }: { onError: (message: string) => void; shipment?: boolean }) {
  const reportTitle = shipment ? "Факт отгрузки" : "Факт производства";
  const [start, setStart] = useState(today); const [end, setEnd] = useState(today);
  const [center, setCenter] = useState(""); const [search, setSearch] = useState(""); const [query, setQuery] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [data, setData] = useState<ProductionReport | null>(null);
  const [loading, setLoading] = useState(false); const [error, setError] = useState(""); const [exporting, setExporting] = useState(false);
  const [statusTarget, setStatusTarget] = useState<HTMLElement | null>(null);
  useEffect(() => { setStatusTarget(document.getElementById("report-status")); }, []);
  const days = Math.round((Date.parse(end) - Date.parse(start)) / 86400000) + 1;
  const valid = !!start && !!end && days >= 1 && days <= 184;
  const params = new URLSearchParams({ start, end, center, search: query, limit: "1" }).toString();
  useEffect(() => { const timer = setTimeout(() => { setQuery(search.trim()); }, 400); return () => clearTimeout(timer); }, [search]);
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError(""); setLoading(valid);
    if (valid) (shipment ? api.shipmentFact : api.productionEol)(params, controller.signal).then(value => {
      if (!controller.signal.aborted) {
        setData(value);
      }
    }).catch(reason => { if (!controller.signal.aborted) setError(reason.message || "Не удалось загрузить отчёт"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [params, refresh, valid, shipment]);
  const selectCenter = (value: string) => { setCenter(value); };
  const preset = (count: number) => {
    const last = today(); setEnd(last);
    setStart(new Date(Date.parse(`${last}T12:00:00Z`) - (count - 1) * 86400000).toISOString().slice(0, 10));
  };
  const exportFile = async () => {
    setExporting(true);
    try {
      const response = await fetch((shipment ? api.shipmentFactExportUrl : api.productionEolExportUrl)(params), { credentials: "include" });
      if (!response.ok) { const body = await response.json().catch(() => null); throw new Error(body?.detail || "Не удалось выгрузить XLSX"); }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = `${reportTitle} ${start} — ${end}.xlsx`;
      document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) { onError(reason instanceof Error ? reason.message : "Ошибка выгрузки"); }
    finally { setExporting(false); }
  };
  return <div className="fact-page stack real-fact eol-fact">
    {statusTarget && createPortal(<span className="report-updated" role="status">{loading ? "Загрузка…" : error ? "Источник недоступен" : data ? `Обновлено ${new Date(data.generated_at).toLocaleTimeString("ru-RU", { timeZone: "Europe/Moscow" })}` : "Нет данных"}</span>, statusTarget)}
    <section className="card real-fact-toolbar"><div className="fact-range"><label>С<input aria-label="Начало периода" type="date" value={start} max={end} onChange={event => { setStart(event.target.value); }} /></label><label>По<input aria-label="Конец периода" type="date" value={end} min={start} onChange={event => { setEnd(event.target.value); }} /></label><div className="real-fact-presets">{[[1, "Сегодня"], [7, "7 дней"], [31, "31 день"]].map(([count, label]) => <button key={count} className={days === count ? "active" : ""} onClick={() => preset(Number(count))}>{label}</button>)}</div></div><div className="eol-toolbar-filters"><label>Линия / место затрат<select value={center} onChange={event => selectCenter(event.target.value)}><option value="">Все линии</option>{center && !data?.centers.some(item => item.code === center) && <option value={center}>МЗ {center}</option>}{data?.centers.map(item => <option key={`${item.code}-${item.name}`} value={item.code}>{item.code} · {item.name}</option>)}</select></label><label>Поиск<input maxLength={120} placeholder="Артикул, продукция, заказ или партия" value={search} onChange={event => setSearch(event.target.value)} /></label></div><div className="real-fact-actions"><button className="button secondary" disabled={loading || !valid} onClick={() => setRefresh(value => value + 1)}>↻ Обновить</button><button className="button primary" disabled={!data || loading || exporting || !valid || query !== search.trim()} onClick={() => void exportFile()}>{exporting ? "Формируем…" : "Выгрузить XLSX"}</button></div></section>
    {!valid && <p className="tail-buffer-notice" role="alert">Выберите период от 1 до 184 дней.</p>}
    {error && <p className="inline-error" role="alert">{error}</p>}
    <section className="metric-grid fact-metrics">{[[shipment ? "Отгрузка за период, кг" : "Выпуск за период, кг", data?.summary.total_kg], ["Дневная смена, кг", data?.summary.day_kg], ["Ночная смена, кг", data?.summary.night_kg], [shipment ? "Задания на отгрузку" : "Производственные задания", data?.summary.orders]].map(([label, value]) => <article className="card real-fact-metric" key={label}><small>{label}</small><strong>{num(value as number | null | undefined)}</strong><small>С учётом выбранных фильтров</small></article>)}</section>
    {!!data?.summary.missing_weight_rows && <p className="tail-buffer-notice" role="alert">Для {num(data.summary.missing_weight_rows)} строк не задан вес единицы. Итоги в кг неполные.</p>}
    {data && <>
      {valid && <ProductionDashboard shipment={shipment} data={data} start={start} end={end} onDay={day => { setStart(day); setEnd(day); }} />}
    </>}
  </div>;
}
