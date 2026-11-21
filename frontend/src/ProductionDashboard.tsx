import { useEffect, useId, useRef, useState } from "react";
import type { ProductionReport } from "./ProductionFact";
import "./ProductionDashboard.css";

const number = (value: number | null | undefined, digits = 2) => value == null ? "—" : value.toLocaleString("ru-RU", { maximumFractionDigits: digits });
const dayLabel = (value: string) => new Date(`${value}T12:00:00Z`).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
const fullDate = (value: string) => new Date(`${value}T12:00:00Z`).toLocaleDateString("ru-RU");
const kg = (value: number | null | undefined) => `${number(value)} кг`;

// Include zero and preserve signed corrections; identical/all-zero values still have a finite scale.
function scale(values: (number | null)[]) {
  const known = values.filter((value): value is number => value != null && Number.isFinite(value));
  const low = Math.min(0, ...known), high = Math.max(0, ...known);
  const rough = (high - low || 1) / 4;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map(value => value * power).find(value => value >= rough)!;
  const min = Math.floor(low / step) * step, max = Math.ceil(high / step) * step || step;
  const ticks = Array.from({ length: Math.round((max - min) / step) + 1 }, (_, i) => min + i * step);
  return { min, max, ticks };
}

function Legend() {
  return <div className="eol-chart-legend"><span><i className="eol-day-swatch" />День</span><span><i className="eol-night-swatch" />Ночь</span></div>;
}

function ProductionTasks({ tasks, shipment }: { tasks: ProductionReport["by_task"]; shipment: boolean }) {
  const dateCaption = "Плановая дата задания";
  const [line, setLine] = useState("");
  const [order, setOrder] = useState("");
  const [sku, setSku] = useState("");
  const [date, setDate] = useState("");
  const [bounds, setBounds] = useState({ planMin: "", planMax: "", factMin: "", factMax: "", percentMin: "", percentMax: "" });
  const [page, setPage] = useState(0);
  const panelRef = useRef<HTMLElement>(null);
  const [pageSize, setPageSize] = useState(6);
  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    const resize = () => {
      if (!window.matchMedia("(min-width: 1401px) and (min-height: 700px)").matches) { setPageSize(6); return; }
      const reserved = [".eol-chart-header", ".eol-task-filters", "thead", ".eol-task-pagination"]
        .reduce((sum, selector) => sum + (panel.querySelector(selector)?.getBoundingClientRect().height || 0), 0);
      setPageSize(Math.max(1, Math.min(6, Math.floor((panel.clientHeight - reserved - 16) / 80))));
    };
    const observer = new ResizeObserver(resize);
    observer.observe(panel);
    window.addEventListener("resize", resize);
    resize();
    return () => { observer.disconnect(); window.removeEventListener("resize", resize); };
  }, []);
  const centers = [...new Map(tasks.map(task => [task.center, task.center_name])).entries()].sort(([a], [b]) => a.localeCompare(b));
  const dates = [...new Set(tasks.flatMap(task => task.fact_by_day.map(day => day.date)))].sort().reverse();
  const needle = sku.trim().toLocaleLowerCase();
  const inRange = (value: number | null, min: string, max: string) =>
    (!min || value != null && value >= Number(min)) && (!max || value != null && value <= Number(max));
  const rangeFilter = (label: string, min: keyof typeof bounds, max: keyof typeof bounds) => <div className="eol-column-range">
    <input type="number" step="any" aria-label={`${label}: от`} placeholder="От" value={bounds[min]} onChange={event => { setBounds({ ...bounds, [min]: event.target.value }); setPage(0); }} />
    <input type="number" step="any" aria-label={`${label}: до`} placeholder="До" value={bounds[max]} onChange={event => { setBounds({ ...bounds, [max]: event.target.value }); setPage(0); }} />
  </div>;
  const resetFilters = () => { setLine(""); setOrder(""); setSku(""); setDate(""); setBounds({ planMin: "", planMax: "", factMin: "", factMax: "", percentMin: "", percentMax: "" }); setPage(0); };
  const hasFilters = Boolean(line || order || sku || date || Object.values(bounds).some(Boolean));
  const filtered = tasks.filter(task => (!line || task.center === line) &&
    (!order.trim() || task.order_number.includes(order.trim())) &&
    (!needle || [task.sku, task.product_name].some(value => value.toLocaleLowerCase().includes(needle))))
    .flatMap(task => {
      if (!date) return [task];
      const day = task.fact_by_day.find(day => day.date === date);
      return day ? [{ ...task, ...day }] : [];
    }).filter(task => inRange(task.plan_kg, bounds.planMin, bounds.planMax) &&
      inRange(task.fact_kg, bounds.factMin, bounds.factMax) && inRange(task.progress_percent, bounds.percentMin, bounds.percentMax));
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const currentPage = Math.min(page, pages - 1);
  return <section ref={panelRef} className="card eol-tasks-panel">
    <header className="eol-chart-header"><div><h2>{shipment ? "Задания на отгрузку" : "Производственные задания"}</h2><p>План задания · {date ? `факт за ${fullDate(date)}` : "факт за выбранный период"}</p></div></header>
    <div className="eol-task-filters">
      <label>Линия<select aria-label="Линия заданий" value={line} onChange={event => { setLine(event.target.value); setPage(0); }}><option value="">Все линии отчёта</option>{centers.map(([code, name]) => <option key={code} value={code}>{code} · {name}</option>)}</select></label>
      <button className="button secondary" disabled={!hasFilters} onClick={resetFilters}>Сбросить фильтры</button>
    </div>
    <div className="eol-task-table"><table><colgroup><col style={{width:"13%"}} /><col style={{width:"13%"}} /><col style={{width:"32%"}} /><col style={{width:"14%"}} /><col style={{width:"14%"}} /><col style={{width:"14%"}} /></colgroup><thead><tr>
      <th scope="col">{dateCaption}<select aria-label={`${dateCaption} заданий`} value={date} onChange={event => { setDate(event.target.value); setPage(0); }}><option value="">Все даты</option>{dates.map(day => <option key={day} value={day}>{fullDate(day)}</option>)}</select></th>
      <th scope="col">Номер задания<input aria-label="Номер задания: фильтр" placeholder="Номер" maxLength={120} value={order} onChange={event => { setOrder(event.target.value); setPage(0); }} /></th>
      <th scope="col">Артикул / наименование<input aria-label="Артикул заданий" placeholder="Артикул или наименование" maxLength={120} value={sku} onChange={event => { setSku(event.target.value); setPage(0); }} /></th>
      <th scope="col">План, кг{rangeFilter("План, кг", "planMin", "planMax")}</th>
      <th scope="col">Факт, кг{rangeFilter("Факт, кг", "factMin", "factMax")}</th>
      <th scope="col">Выполнение, %{rangeFilter("Выполнение, %", "percentMin", "percentMax")}</th>
    </tr></thead><tbody>
      {filtered.slice(currentPage * pageSize, (currentPage + 1) * pageSize).map(task => <tr key={JSON.stringify([task.order_number, task.order_line, task.sku, task.center])} className={`eol-task-${task.status}`}>
        <td className="eol-task-dates"><span title={task.fact_by_day.map(day => fullDate(day.date)).join(", ")}>{date ? fullDate(date) : task.fact_by_day.map(day => fullDate(day.date)).join(", ")}</span></td>
        <td title={`${task.center} · ${task.center_name}`}><b>{task.order_number || "—"}</b><small>Поз. {task.order_line} · МЗ {task.center}</small></td>
        <td><b>{task.sku || "—"}</b><small className="eol-task-product" title={task.product_name}>{task.product_name || "Наименование не указано"}</small></td><td>{number(task.plan_kg)}</td>
        <td><b>{number(task.fact_kg)}</b></td><td><b>{task.progress_percent == null ? "—" : `${number(task.progress_percent)}%`}</b><small className="eol-task-status" title={task.status_reason || undefined}>{task.status === "completed" ? "Выполнено" : task.status === "pending" ? "Не выполнено" : task.status_reason}</small></td>
      </tr>)}
      {!filtered.length && <tr><td colSpan={6} className="eol-task-empty">По выбранным фильтрам заданий нет.</td></tr>}
    </tbody></table></div>
    <footer className="eol-task-pagination"><span>Заданий: {number(filtered.length, 0)}</span>{pages > 1 && <><button className="button secondary" aria-label="Предыдущая страница заданий" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>←</button><span>{currentPage + 1} / {pages}</span><button className="button secondary" aria-label="Следующая страница заданий" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>→</button></>}</footer>
  </section>;
}

export function ProductionDashboard({ data, start, end, onDay, shipment = false }: {
  data: ProductionReport; start: string; end: string; shipment?: boolean;
  onDay: (date: string) => void;
}) {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const [chartSize, setChartSize] = useState({ width: 440, height: 200 });
  useEffect(() => {
    if (!chartRef.current) return;
    const observer = new ResizeObserver(entries => setChartSize({ width: Math.floor(entries[0].contentRect.width), height: Math.floor(entries[0].contentRect.height) }));
    observer.observe(chartRef.current);
    return () => observer.disconnect();
  }, [data.total_count]);
  const titleId = useId();
  const days = Math.round((Date.parse(end) - Date.parse(start)) / 86400000) + 1;
  const byDate = new Map(data.by_day.map(row => [row.date, row]));
  const calendar = Array.from({ length: days }, (_, index) => {
    const date = new Date(Date.parse(`${start}T12:00:00Z`) + index * 86400000).toISOString().slice(0, 10);
    return { date, row: byDate.get(date) };
  });
  const chosen = calendar.find(item => item.date === selectedDate) || calendar[calendar.length - 1];
  const dailyScale = scale(data.by_day.flatMap(row => [row.day_kg, row.night_kg]));
  const width = Math.max(240, chartSize.width), height = Math.max(200, chartSize.height);
  const left = Math.max(...dailyScale.ticks.map(tick => number(tick, 1).length)) * 7 + 12;
  const right = 20, top = 20, bottom = 34;
  const plotHeight = height - top - bottom;
  const y = (value: number) => top + (dailyScale.max - value) / (dailyScale.max - dailyScale.min) * plotHeight;
  const slot = (width - left - right) / days;
  const barWidth = Math.min(24, slot * .34);
  const barGap = Math.min(1, slot * .08);
  const interval = Math.max(1, Math.ceil(days / Math.max(1, Math.floor((width - left - right) / 52))));
  if (!data.total_count) return <section className="card eol-dashboard-empty"><h2>{shipment ? "Дашборд отгрузки" : "Дашборд выпуска"}</h2><p>Для выбранного периода и фильтров нет данных. Измените период или поиск.</p></section>;
  return <section className="eol-dashboard" aria-label={shipment ? "Дашборд отгрузки" : "Дашборд производства"}>
    <section className="card eol-trend-panel">
      <header className="eol-chart-header"><div><h2>{shipment ? "Динамика отгрузки" : "Динамика выпуска"}</h2><p>{fullDate(start)} — {fullDate(end)} · кг по плановой дате задания</p></div><Legend /></header>
      <div ref={chartRef} className="eol-trend-chart">
        <svg viewBox={`0 0 ${width} ${height}`} className="eol-trend-svg" role="img" aria-labelledby={titleId}>
          <title id={titleId}>{shipment ? "Отгрузка" : "Выпуск"} по датам: дневная и ночная смены в килограммах. Точные значения доступны в выборе даты под графиком и в таблице заданий.</title>
          {dailyScale.ticks.map(tick => <g key={tick}><line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className={tick === 0 ? "eol-zero-line" : "eol-grid-line"} /><text x={left - 12} y={y(tick) + 4} textAnchor="end" className="eol-axis-label">{number(tick, 1)}</text></g>)}
          {calendar.map(({ date, row }, index) => {
            const x = left + (index + .5) * slot;
            const description = `${fullDate(date)}: ${row ? `день ${kg(row.day_kg)}, ночь ${kg(row.night_kg)}, итог ${kg(row.total_kg)}${row.missing_weight_rows ? ', неполный вес' : ''}` : "нет записей в источнике"}`;
            return <g key={date} onClick={() => setSelectedDate(date)} className="eol-chart-day">
              <title>{description}</title>
              <rect x={left + index * slot} y={top} width={slot} height={plotHeight} fill={days > 1 && chosen.date === date ? "var(--eol-selection)" : "transparent"} />
              {row ? (["day_kg", "night_kg"] as const).map((field, shift) => {
                const value = row[field];
                return value == null ? <text key={field} x={x + (shift ? barWidth / 2 + barGap : -barWidth / 2 - barGap)} y={y(0) - 5} textAnchor="middle" className="eol-axis-label">?</text>
                  : <rect key={field} x={x + (shift ? barGap : -barWidth - barGap)} y={value >= 0 ? y(value) : y(0)} width={barWidth} height={value === 0 ? 1 : Math.max(.8, Math.abs(y(value) - y(0)))} fill={shift ? "var(--eol-night)" : "var(--eol-day)"} />;
              }) : <circle cx={x} cy={y(0)} r={2} className="eol-no-records" />}
              {(index % interval === 0 || index === days - 1 && index % interval > interval / 2) && <text x={x} y={height - 10} textAnchor="middle" className="eol-axis-label">{dayLabel(date)}</text>}
            </g>;
          })}
        </svg>
      </div>
      <div className="eol-chart-inspector"><label>Дата на графике<select value={chosen.date} onChange={event => setSelectedDate(event.target.value)}>{calendar.map(item => <option key={item.date} value={item.date}>{fullDate(item.date)}{!item.row ? " · нет записей" : ""}</option>)}</select></label>
        <dl><div><dt>День</dt><dd>{kg(chosen.row?.day_kg)}</dd></div><div><dt>Ночь</dt><dd>{kg(chosen.row?.night_kg)}</dd></div><div><dt>Всего</dt><dd>{kg(chosen.row?.total_kg)}{Boolean(chosen.row?.missing_weight_rows) && <small>Неполные данные</small>}</dd></div></dl>
        <button className="button secondary" disabled={!chosen.row || days === 1} onClick={() => onDay(chosen.date)}>Открыть этот день</button>
      </div>
    </section>
      <ProductionTasks tasks={data.by_task} shipment={shipment} />
  </section>;
}
