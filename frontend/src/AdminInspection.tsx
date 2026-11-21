import { useEffect, useState } from "react";
import { api } from "./api";
import type { AuditRow } from "./types";

export interface InspectionPage<T> { total: number; offset: number; limit: number; rows: T[] }
export interface DatabaseTable { name: string; label: string; columns: { name: string; type: string }[] }
type RecordRow = Record<string, unknown>;
const pageSize = 50;
const actionLabels: Record<string, string> = {
  product_updated: "Артикул изменён", product_created: "Артикул создан", product_restored: "Артикул восстановлен",
  product_deleted: "Артикул удалён", product_blocked: "Артикул заблокирован", product_active: "Артикул активирован",
  capability_updated: "Мощность артикула изменена", catalog_analogue_applied: "Параметры заполнены по аналогу",
  plan_imported: "План загружен", reference_imported: "Справочник загружен", plan_recalculated: "План пересчитан",
  schedule_item_updated: "Задание изменено", schedule_item_deleted: "Задание удалено", manual_task_created: "Задание создано",
  schedule_items_bulk_deleted: "Задания удалены за период", source_deleted: "Источник удалён",
  user_access_updated: "Права пользователя изменены", user_access_created: "Доступ добавлен", user_access_deleted: "Доступ отозван",
  line_schedule_updated: "График линии изменён", execution_status_updated: "Исполнение изменено",
  mail_configuration_updated: "Настройки почты изменены", portal_configuration_updated: "Настройки портала изменены",
  mail_action_updated: "Изменено правило рассылки",
  plan_data_deleted: "Планы удалены", production_plan_emailed: "План отправлен по почте",
};
const entityLabels: Record<string, string> = { product: "Артикул", line_capability: "Мощность SKU", production_line: "Линия", production_plan: "План", schedule_item: "Задание", user: "Пользователь", imported_order: "Источник", portal_setting: "Настройка" };
const formatValue = (value: unknown): string => value == null ? "—" : typeof value === "object" ? JSON.stringify(value, null, 2) : typeof value === "boolean" ? (value ? "Да" : "Нет") : String(value);
const localDate = (value: string) => new Date(value).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" });

function Pagination({ total, offset, busy, onOffset }: { total: number; offset: number; busy: boolean; onOffset: (value: number) => void }) {
  return <footer className="inspection-pagination"><span>{total ? `${offset + 1}–${Math.min(offset + pageSize, total)} из ${total.toLocaleString("ru-RU")}` : "Записей нет"}</span><div className="inline-actions"><button className="button secondary" disabled={busy || offset === 0} onClick={() => onOffset(Math.max(0, offset - pageSize))}>Назад</button><button className="button secondary" disabled={busy || offset + pageSize >= total} onClick={() => onOffset(offset + pageSize)}>Далее</button></div></footer>;
}

export function AuditLog() {
  const [filters, setFilters] = useState({ q: "", action: "", username: "", entity_type: "", start: "", end: "" });
  const [applied, setApplied] = useState(filters);
  const [options, setOptions] = useState<{ actions: string[]; users: string[]; entities: string[] }>({ actions: [], users: [], entities: [] });
  const [data, setData] = useState<InspectionPage<AuditRow> | null>(null);
  const [offset, setOffset] = useState(0); const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(true); const [error, setError] = useState("");
  useEffect(() => { const controller = new AbortController(); api.auditFilters(controller.signal).then(setOptions).catch(e => { if (!controller.signal.aborted) setError(e.message); }); return () => controller.abort(); }, []);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setError(""); setData(null);
    const params = new URLSearchParams({ offset: String(offset), limit: String(pageSize) });
    Object.entries(applied).forEach(([key, value]) => { if (value) params.set(key, value); });
    api.auditLog(params.toString(), controller.signal).then(setData).catch(e => { if (!controller.signal.aborted) setError(e.message); }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [applied, offset, reload]);
  return <section className="card audit-card inspection"><header><h2>Журнал изменений</h2><p>Вся сохранённая история. Поиск по артикулу, названию, пользователю и содержимому записи. Даты — московское время.</p></header>
    <form className="inspection-filters" onSubmit={e => { e.preventDefault(); setOffset(0); setApplied({ ...filters }); }}>
      <label className="inspection-search">Поиск<input value={filters.q} onChange={e => setFilters({ ...filters, q: e.target.value })} placeholder="Артикул, название или текст изменения" /></label>
      <label>Действие<select aria-label="Действие" value={filters.action} onChange={e => setFilters({ ...filters, action: e.target.value })}><option value="">Все действия</option>{options.actions.map(v => <option key={v} value={v}>{actionLabels[v] || v}</option>)}</select></label>
      <label>Пользователь<select aria-label="Пользователь" value={filters.username} onChange={e => setFilters({ ...filters, username: e.target.value })}><option value="">Все пользователи</option>{options.users.map(v => <option key={v}>{v}</option>)}</select></label>
      <label>Объект<select aria-label="Объект" value={filters.entity_type} onChange={e => setFilters({ ...filters, entity_type: e.target.value })}><option value="">Все объекты</option>{options.entities.map(v => <option key={v} value={v}>{entityLabels[v] || v}</option>)}</select></label>
      <label>С даты<input type="date" value={filters.start} onChange={e => setFilters({ ...filters, start: e.target.value })} /></label>
      <label>По дату<input type="date" value={filters.end} onChange={e => setFilters({ ...filters, end: e.target.value })} /></label>
      <div className="inline-actions"><button className="button primary" disabled={busy}>Найти</button><button type="button" className="button secondary" disabled={busy} onClick={() => { const empty = { q: "", action: "", username: "", entity_type: "", start: "", end: "" }; setFilters(empty); setApplied(empty); setOffset(0); }}>Сбросить</button><button type="button" className="button secondary" disabled={busy} onClick={() => setReload(v => v + 1)}>Обновить</button></div>
    </form>
    {error && <p className="inline-error" role="alert">{error}</p>}{busy && <p className="inspection-state" role="status">Загружаем журнал…</p>}
    {data && <><div className="table-scroll"><table><thead><tr><th>Дата (МСК)</th><th>Пользователь</th><th>Действие</th><th>Объект</th><th>Подробности</th></tr></thead><tbody>{data.rows.map(row => <tr key={row.id}><td>{localDate(row.created_at)}<small>№ {row.id}</small></td><td>{row.username}</td><td>{actionLabels[row.action] || row.action}<small>{row.action}</small></td><td>{entityLabels[row.entity_type] || row.entity_type} {row.entity_id}</td><td><details><summary>Показать изменения</summary><pre>{JSON.stringify(row.details, null, 2)}</pre></details></td></tr>)}</tbody></table></div>{!data.rows.length && <p className="inspection-state">По выбранным условиям изменений не найдено.</p>}<Pagination total={data.total} offset={offset} busy={busy} onOffset={setOffset} /></>}
  </section>;
}

export function DatabaseExplorer() {
  const [tables, setTables] = useState<DatabaseTable[]>([]); const [table, setTable] = useState("products");
  const [filters, setFilters] = useState({ q: "", column: "", value: "" }); const [applied, setApplied] = useState(filters);
  const [sort, setSort] = useState("id"); const [direction, setDirection] = useState("desc");
  const [offset, setOffset] = useState(0); const [reload, setReload] = useState(0);
  const [data, setData] = useState<InspectionPage<RecordRow> | null>(null); const [selected, setSelected] = useState<RecordRow | null>(null);
  const [busy, setBusy] = useState(true); const [error, setError] = useState("");
  useEffect(() => { const controller = new AbortController(); api.databaseTables(controller.signal).then(v => setTables(v.tables)).catch(e => { if (!controller.signal.aborted) setError(e.message); }); return () => controller.abort(); }, []);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setError(""); setData(null); setSelected(null);
    const params = new URLSearchParams({ ...applied, sort, direction, offset: String(offset), limit: String(pageSize) });
    api.databaseRows(table, params.toString(), controller.signal).then(setData).catch(e => { if (!controller.signal.aborted) setError(e.message); }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [table, applied, sort, direction, offset, reload]);
  const current = tables.find(t => t.name === table);
  return <section className="card audit-card inspection"><header><h2>Просмотр базы данных</h2><p>Таблицы планирования и справочника · только чтение. Откройте запись, чтобы увидеть все значения.</p></header>
    <form className="inspection-filters" onSubmit={e => { e.preventDefault(); setOffset(0); setApplied({ ...filters }); }}>
      <label className="inspection-search">Таблица<select aria-label="Таблица" value={table} onChange={e => { setTable(e.target.value); setOffset(0); setSort("id"); setDirection("desc"); const empty = { q: "", column: "", value: "" }; setFilters(empty); setApplied(empty); }}>{tables.map(t => <option key={t.name} value={t.name}>{t.label} · {t.name}</option>)}</select></label>
      <label className="inspection-search">Поиск по таблице<input value={filters.q} onChange={e => setFilters({ ...filters, q: e.target.value })} placeholder="Артикул, ID или текст" /></label>
      <label>Точное условие: столбец<select aria-label="Точное условие: столбец" value={filters.column} onChange={e => setFilters({ ...filters, column: e.target.value, value: "" })}><option value="">Без условия</option>{current?.columns.map(c => <option key={c.name}>{c.name}</option>)}</select></label>
      <label>Значение<input disabled={!filters.column} value={filters.value} onChange={e => setFilters({ ...filters, value: e.target.value })} placeholder="Например, product_id = 588" /></label>
      <div className="inline-actions"><button className="button primary" disabled={busy}>Найти</button><button type="button" className="button secondary" disabled={busy} onClick={() => { const empty = { q: "", column: "", value: "" }; setFilters(empty); setApplied(empty); setOffset(0); }}>Сбросить</button><button type="button" className="button secondary" disabled={busy} onClick={() => setReload(v => v + 1)}>Обновить</button></div>
    </form>
    {error && <p className="inline-error" role="alert">{error}</p>}{busy && <p className="inspection-state" role="status">Загружаем таблицу…</p>}
    {selected && <section className="inspection-record"><header><h3>Запись № {String(selected.id)}</h3><button className="button secondary" onClick={() => setSelected(null)}>Закрыть запись</button></header><dl>{current?.columns.map(c => <div key={c.name}><dt>{c.name}<small>{c.type}</small></dt><dd><pre>{formatValue(selected[c.name])}</pre></dd></div>)}</dl></section>}
    {data && <><div className="table-scroll"><table><thead><tr><th>Запись</th>{current?.columns.map(c => <th key={c.name} aria-sort={sort === c.name ? direction === "asc" ? "ascending" : "descending" : "none"}><button className="inspection-sort" title={`Сортировать: ${c.name} (${c.type})`} onClick={() => { setOffset(0); setSort(c.name); setDirection(sort === c.name && direction === "desc" ? "asc" : "desc"); }}>{c.name} {sort === c.name ? direction === "asc" ? "↑" : "↓" : ""}</button></th>)}</tr></thead><tbody>{data.rows.map(row => <tr key={String(row.id)}><td><button className="button secondary" onClick={() => setSelected(row)}>Открыть #{String(row.id)}</button></td>{current?.columns.map(c => <td key={c.name}><span className="inspection-cell">{formatValue(row[c.name])}</span></td>)}</tr>)}</tbody></table></div>{!data.rows.length && <p className="inspection-state">Записей по выбранным условиям нет.</p>}<Pagination total={data.total} offset={offset} busy={busy} onOffset={setOffset} /></>}
  </section>;
}
