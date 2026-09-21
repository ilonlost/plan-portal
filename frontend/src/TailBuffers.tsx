import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { TailBufferData } from "./types";

const dateTime = (value: string | null) => value ? new Date(value).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" }) : "—";

export function TailBuffers({ start, end }: { start: string; end: string }) {
  const [data, setData] = useState<TailBufferData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [center, setCenter] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setBusy(true); setError(""); setData(null); setPage(0);
    api.tailBuffers(start, end).then(value => { if (!cancelled) setData(value); })
      .catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Ошибка загрузки буферов"); })
      .finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, [start, end, refresh]);
  const rows = useMemo(() => (data?.rows || []).filter(row => (!center || row.source_center === center) &&
    `${row.sku || ""} ${row.product_name || ""} ${row.sscc || ""}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())), [data, center, search]);
  const available = data?.status === "connected" || data?.status === "partial";
  const status = !data ? "" : ({ connected: "DWH подключён", partial: "Данные доступны частично", not_configured: "Ожидается подключение DWH", invalid_configuration: "Проверьте настройки", unavailable: "Источник недоступен" }[data.status]);
  return <section className="card tail-buffers">
    <header><div><small>ПРОВОДКИ В БУФЕР КОРОБОВ</small><h2>Хвостовые буферы</h2><p>Факт перемещений из DWH за выбранный период. Проводки не определяют часы работы линии.</p></div><button className="button secondary" disabled={busy} onClick={() => setRefresh(value => value + 1)}>{busy ? "Загрузка…" : "↻ Обновить"}</button></header>
    <div className="tail-buffer-body">
      {error && <p className="inline-error" role="alert">{error}</p>}
      {busy && <p role="status">Читаем проводки хвостовых буферов…</p>}
      {data && <><p className="tail-buffer-status">{status} · {start} — {end}</p>
        {!!data.issues.length && <div className="tail-buffer-notice">{data.issues.map(issue => <p key={issue}>{issue}</p>)}</div>}
        {data.status === "not_configured" && <p>Подключение задаётся на сервере в <code>PRODUCTION_FACT_DATABASE_URL</code>. Секреты вводятся только в ENV.</p>}
        <div className="tail-buffer-workshops">{(["KC", "PC"] as const).map(workshop => <section key={workshop}><h3>{workshop === "KC" ? "КЦ · Кулинарный цех" : "ПЦ · Пекарный цех"}<small>Буфер назначения: {data.buffers[workshop] || "не задан"}</small></h3>
          <div className="tail-center-list">{data.centers.filter(item => item.workshop_code === workshop).map(item => {
            const items = data.rows.filter(row => row.source_center === item.code);
            const sscc = new Set(items.map(row => row.sscc).filter(Boolean)).size;
            const known = available && !!data.buffers[workshop];
            return <button className={center === item.code ? "active" : ""} key={item.code} onClick={() => { setCenter(center === item.code ? "" : item.code); setPage(0); }}><span><b>{item.code}</b> {item.name}</span><small>{known ? `${items.length} проводок · ${sscc} SSCC` : "Нет подтверждённых данных"}</small></button>;
          })}</div></section>)}</div>
        {data.truncated && <p className="tail-buffer-notice">Из {data.total_count.toLocaleString("ru-RU")} проводок загружены последние {data.rows.length}. Счётчики МЗ рассчитаны по загруженным строкам. Сократите период для полной детализации.</p>}
        <div className="tail-buffer-controls"><label>Место затрат<select value={center} onChange={event => { setCenter(event.target.value); setPage(0); }}><option value="">Все МЗ</option>{data.centers.map(item => <option key={item.code} value={item.code}>{item.code} · {item.name}</option>)}</select></label><label>Поиск<input placeholder="Артикул, продукция или SSCC" value={search} onChange={event => { setSearch(event.target.value); setPage(0); }} /></label><span>{rows.length} строк</span></div>
        <p className="tail-buffer-note">{data.first_movement_scope}. Дата создания берётся из карточки SSCC и может находиться вне периода проводок. SSCC не приравнивается к количеству коробов.</p>
        <div className="tail-buffer-table"><table><thead><tr><th>Проводка</th><th>Цех / МЗ</th><th>Артикул / продукция</th><th>Дата создания</th><th>Буфер назначения</th><th>SSCC</th><th>Первое движение в периоде месяцев</th></tr></thead><tbody>{rows.slice(page * 100, (page + 1) * 100).map(row => <tr key={row.id}><td><b>{dateTime(row.moved_at)}</b><small>№ {row.record_id} · {row.source_month}</small></td><td>{row.workshop_code === "KC" ? "КЦ" : "ПЦ"} · {row.source_center}<small>{row.line_name}</small></td><td><b>{row.sku || "—"}</b><small>{row.product_name || "Наименование отсутствует"}</small>{row.warning && <small className="tail-row-warning">{row.warning}</small>}</td><td>{row.created_date ? new Date(row.created_date).toLocaleDateString("ru-RU") : "—"}</td><td>{row.target_buffer}<small>{row.buffer_name || "—"}</small>{row.card_buffer != null && String(row.card_buffer) !== row.target_buffer && <small>В карточке SSCC: {row.card_buffer}</small>}</td><td className="tail-sscc">{row.sscc || "—"}</td><td>{dateTime(row.first_moved_at)}</td></tr>)}</tbody></table></div>
        {!rows.length && <p>{available ? "В загруженных данных нет проводок по выбранным фильтрам." : "После подключения здесь появятся реальные проводки."}</p>}
        {rows.length > 100 && <footer><button className="button secondary" disabled={!page} onClick={() => setPage(value => value - 1)}>Назад</button><span>{page + 1} / {Math.ceil(rows.length / 100)}</span><button className="button secondary" disabled={(page + 1) * 100 >= rows.length} onClick={() => setPage(value => value + 1)}>Далее</button></footer>}
      </>}
    </div>
  </section>;
}
