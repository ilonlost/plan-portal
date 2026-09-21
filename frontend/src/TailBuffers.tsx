import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { ProductionFactData, TailBufferData } from "./types";

const num = (value: number) => value.toLocaleString("ru-RU");
const today = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow" }).format(new Date());
const shiftDay = (day: string, delta: number) => new Date(new Date(`${day}T12:00:00Z`).getTime() + delta * 86400000).toISOString().slice(0, 10);
const stamp = (value: string | null) => value ? new Date(value).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" }) : "—";
const statusLabels = { connected: "DWH подключён", partial: "Неполные данные", not_configured: "Подключение не настроено", invalid_configuration: "Ошибка настроек", unavailable: "DWH недоступен" };

export function TailBuffers({ onError }: { onError: (text: string) => void }) {
  const [start, setStart] = useState(today); const [end, setEnd] = useState(today);
  const [summary, setSummary] = useState<ProductionFactData | null>(null);
  const [data, setData] = useState<TailBufferData | null>(null);
  const [loading, setLoading] = useState(false); const [loadingRows, setLoadingRows] = useState(false);
  const [error, setError] = useState(""); const [rowError, setRowError] = useState("");
  const [refresh, setRefresh] = useState(0); const [center, setCenter] = useState("");
  const [search, setSearch] = useState(""); const [query, setQuery] = useState("");
  const [page, setPage] = useState(0); const [exporting, setExporting] = useState(false);
  const detail = useRef<HTMLElement>(null);
  const days = Math.round((new Date(`${end}T00:00:00Z`).getTime() - new Date(`${start}T00:00:00Z`).getTime()) / 86400000) + 1;
  const valid = !!start && !!end && days >= 1 && days <= 184;
  useEffect(() => { const timer = setTimeout(() => { setQuery(search.trim()); setPage(0); }, 400); return () => clearTimeout(timer); }, [search]);
  useEffect(() => {
    let cancelled = false;
    setSummary(null); setError(""); setPage(0);
    if (!valid) { setLoading(false); return; }
    setLoading(true);
    api.productionFact(start, end).then(value => { if (!cancelled) setSummary(value); })
      .catch(reason => { if (!cancelled) setError(String(reason.message || reason)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [start, end, refresh, valid]);
  useEffect(() => {
    let cancelled = false;
    setData(null); setRowError("");
    if (!valid) { setLoadingRows(false); return; }
    setLoadingRows(true);
    api.tailBuffers(start, end, center, query, page * 100).then(value => {
      if (!cancelled) {
        if (page && !value.rows.length && value.status === "connected") setPage(0);
        else setData(value);
      }
    }).catch(reason => { if (!cancelled) setRowError(String(reason.message || reason)); })
      .finally(() => { if (!cancelled) setLoadingRows(false); });
    return () => { cancelled = true; };
  }, [start, end, center, query, page, refresh, valid]);
  const setDays = (count: number) => { const last = today(); setStart(shiftDay(last, 1 - count)); setEnd(last); setPage(0); };
  const selectCenter = (value: string) => { setCenter(value); setPage(0); };
  const available = summary?.status === "connected" || summary?.status === "partial";
  const exportXlsx = async () => {
    setExporting(true);
    try {
      const response = await fetch(api.productionFactExportUrl(start, end, center, query), { credentials: "include" });
      if (!response.ok) { const body = await response.json().catch(() => null); throw new Error(body?.detail || "Не удалось выгрузить XLSX"); }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = `Факт буферов ${start} — ${end}.xlsx`;
      document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) { onError(reason instanceof Error ? reason.message : "Ошибка выгрузки"); }
    finally { setExporting(false); }
  };
  const rangeStart = new Date(`${start}T00:00:00+03:00`).getTime(); const rangeMs = days * 86400000;
  const ruler = [0, 25, 50, 75, 100].map(percent => ({ percent, label: days === 1 ? `${String(percent * 24 / 100).padStart(2, "0")}:00` : new Date(rangeStart + (rangeMs - 86400000) * percent / 100).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", timeZone: "Europe/Moscow" }) }));
  return <div className="fact-page stack real-fact">
    <section className="card fact-hero"><div><span className="fact-hero-icon">◷</span><div><small>ФАКТ ПРОИЗВОДСТВА</small><h2>Хвостовые буферы</h2><p>Проводки КЦ и ПЦ в буфер коробов · CSB DWH</p></div></div><span className={`real-fact-status ${summary?.status || ""}`} role="status">{loading ? "Обновляем данные…" : summary ? statusLabels[summary.status] : "Нет данных"}</span></section>
    <section className="card real-fact-toolbar"><div className="fact-range"><label>С<input aria-label="Начало периода" type="date" value={start} max={end} onChange={event => { setStart(event.target.value); setPage(0); }} /></label><label>По<input aria-label="Конец периода" type="date" value={end} min={start} onChange={event => { setEnd(event.target.value); setPage(0); }} /></label><div className="real-fact-presets">{[[1, "День"], [7, "Неделя"], [31, "Месяц"], [92, "3 месяца"], [184, "Полгода"]].map(([count, label]) => <button className={days === count ? "active" : ""} key={count} onClick={() => setDays(Number(count))}>{label}</button>)}</div></div><div className="real-fact-actions"><button className="button secondary" disabled={loading || loadingRows || !valid} onClick={() => setRefresh(value => value + 1)}>↻ Обновить</button><button className="button primary" disabled={exporting || loading || summary?.status !== "connected" || !valid || query !== search.trim()} onClick={() => void exportXlsx()}>{exporting ? "Формируем XLSX…" : "Выгрузить XLSX"}</button></div></section>
    {!valid && <p role="alert" className="tail-buffer-notice">Выберите период от 1 до 184 дней.</p>}
    {error && <p role="alert" className="inline-error">{error}</p>}
    {!!summary?.issues.length && <div className="tail-buffer-notice" role="alert">{summary.issues.map(issue => <p key={issue}>{issue}</p>)}{summary.status === "partial" && <p>Итоги включают только доступные месяцы. Выгрузка заблокирована до восстановления полного периода.</p>}</div>}
    <section className="metric-grid fact-metrics">{[
      ["Проводки", summary?.total_count, "Весь выбранный период"], ["Уникальные SSCC", summary?.sscc_count, "Не приравнивается к числу коробов"],
      ["МЗ с проводками", summary?.active_centers, "Из 12 подключённых МЗ"], ["Дней в периоде", valid ? days : undefined, "Время проводок — московское"],
    ].map(([label, value, note]) => <article className="card real-fact-metric" key={label}><small>{label}</small><strong>{available && typeof value === "number" ? num(value) : "—"}</strong><small>{note}</small></article>)}</section>
    <p className="fact-source-note">Шкала показывает количество проводок за {summary?.bucket_minutes === 15 ? "15 минут" : summary?.bucket_minutes === 60 ? "час" : "день"}. Отсутствие проводок не означает простой. Часы работы и объём продукции в этом источнике не определены.</p>
    <section className="fact-split">{(["PC", "KC"] as const).map(workshop => <article className={`card real-fact-workshop ${workshop.toLowerCase()}`} key={workshop}>
      <header><div><small>{workshop === "PC" ? "ПЕКАРНЫЙ ЦЕХ" : "КУЛИНАРНЫЙ ЦЕХ"}</small><h2>{workshop === "PC" ? "ПЦ" : "КЦ"}</h2></div><span>Буфер <b>{summary?.buffers[workshop] || (workshop === "PC" ? "5898" : "5498")}</b><small>{available ? `${num(summary!.centers.filter(item => item.workshop_code === workshop).reduce((sum, item) => sum + item.postings, 0))} проводок` : "Нет подтверждённых данных"}</small></span></header>
      <div className="real-fact-ruler">{valid && ruler.map(item => <span key={item.percent} style={{ left: `${item.percent}%` }}>{item.label}</span>)}</div>
      <div className="real-fact-centers">{summary?.centers.filter(item => item.workshop_code === workshop).map(item => {
        const maximum = Math.max(1, ...item.buckets.map(bucket => bucket.postings));
        return <button className={`real-fact-center ${center === item.code ? "active" : ""}`} key={item.code} aria-pressed={center === item.code} onClick={() => { selectCenter(item.code); detail.current?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>
          <span className="real-fact-center-heading"><span><b>{item.name}</b><small>МЗ {item.code}</small></span><span><b>{available ? num(item.postings) : "—"}</b><small>{available ? `${num(item.sscc_count)} SSCC` : "Нет данных"}</small></span></span>
          <span className="real-fact-track">{available && item.buckets.map(bucket => <i key={bucket.at} style={{ left: `${(new Date(bucket.at).getTime() - rangeStart) / rangeMs * 100}%`, width: `${summary.bucket_minutes * 60000 / rangeMs * 100}%`, height: `${20 + bucket.postings / maximum * 80}%` }} title={`${stamp(bucket.at)} · ${num(bucket.postings)} проводок`} />)}</span>
          <span className="real-fact-center-period">{available && item.first_at ? `${stamp(item.first_at)} — ${stamp(item.last_at)}` : available ? "Проводок в периоде нет" : "Данные недоступны"}</span>
        </button>;
      })}</div><footer>Нажмите на МЗ, чтобы открыть проводки</footer></article>)}</section>
    <section className="card tail-buffers" ref={detail}><header><div><small>ДЕТАЛИЗАЦИЯ</small><h2>{center ? `МЗ ${center} · ${summary?.centers.find(item => item.code === center)?.name || ""}` : "Все проводки"}</h2><p>Таблица и XLSX учитывают выбранное МЗ и поиск. Итоги выше — за весь период.</p></div>{center && <button className="button secondary" onClick={() => selectCenter("")}>Все МЗ</button>}</header>
      <div className="tail-buffer-body"><div className="tail-buffer-controls"><label>Место затрат<select value={center} onChange={event => selectCenter(event.target.value)}><option value="">Все МЗ</option>{summary?.centers.map(item => <option key={item.code} value={item.code}>{item.code} · {item.name}</option>)}</select></label><label>Поиск<input placeholder="Артикул, продукция или SSCC" value={search} onChange={event => setSearch(event.target.value)} /></label><span role="status">{loadingRows ? "Загрузка…" : data && (data.status === "connected" || data.status === "partial") ? `${num(data.total_count)} проводок` : "Данные недоступны"}</span></div>
        {rowError && <p role="alert" className="inline-error">{rowError}</p>}
        {!!data?.issues.length && <div className="tail-buffer-notice">{data.issues.map(issue => <p key={issue}>{issue}</p>)}</div>}
        <div className="tail-buffer-table"><table><thead><tr><th>Проводка, МСК</th><th>Цех / МЗ</th><th>Артикул / продукция</th><th>Создание SSCC</th><th>Буфер</th><th>SSCC</th><th>Первое движение в выбранных месяцах</th></tr></thead><tbody>{data?.rows.map(row => <tr key={row.id}><td><b>{stamp(row.moved_at)}</b><small>№ {row.record_id} · {row.source_month}</small></td><td>{row.workshop_code === "KC" ? "КЦ" : "ПЦ"} · {row.source_center}<small>{row.line_name}</small></td><td><b>{row.sku || "—"}</b><small>{row.product_name || "Наименование отсутствует"}</small>{row.warning && <small className="tail-row-warning">{row.warning}</small>}</td><td>{row.created_date ? new Date(`${row.created_date}T12:00:00Z`).toLocaleDateString("ru-RU") : "—"}</td><td>{row.target_buffer}<small>{row.buffer_name}</small>{row.card_buffer != null && String(row.card_buffer) !== row.target_buffer && <small>В карточке: {row.card_buffer}</small>}</td><td className="tail-sscc">{row.sscc || "—"}</td><td>{stamp(row.first_moved_at)}</td></tr>)}</tbody></table></div>
        {!loadingRows && data?.status === "connected" && !data.rows.length && <p>По выбранному периоду и фильтрам проводок нет.</p>}
        {!!data?.total_count && <footer><button className="button secondary" disabled={!page || loadingRows} onClick={() => { setPage(value => value - 1); detail.current?.scrollIntoView({ block: "start" }); }}>Назад</button><span>Страница {page + 1} из {Math.ceil(data.total_count / 100)}</span><button className="button secondary" disabled={(page + 1) * 100 >= data.total_count || loadingRows} onClick={() => { setPage(value => value + 1); detail.current?.scrollIntoView({ block: "start" }); }}>Далее</button></footer>}
        <p className="tail-buffer-note">{summary?.first_movement_scope}. Дата создания SSCC может быть раньше выбранного периода.</p>
      </div>
    </section>
  </div>;
}
