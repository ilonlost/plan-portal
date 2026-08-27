import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "./api";
import type {
  AdminOverview, CatalogData, CatalogRow, ImportPreview, LineData, MatrixCell,
  MatrixData, ScheduleItem, SessionMode, UserProfile, WorkshopData,
} from "./types";
import "./styles.css";

type Page = "plan" | "catalog" | "import" | "sources" | "admin";
type ViewDays = 1 | 7 | 21;
type Theme = "dark" | "light";
type DayLayout = "cards" | "table";

const sourceLabels: Record<string, string> = { ohl: "ОХЛ", zam: "ЗАМ", generic: "Прочее" };
const executionLabels: Record<string, string> = {
  not_started: "Не начато", in_progress: "В работе", completed: "Выполнено",
  partially_shipped: "Частично отгружено", not_shipped: "Не отгружено",
};
const today = () => new Date().toLocaleDateString("sv-SE");
const canPlan = (user: UserProfile | null) => user?.role === "admin" || user?.role === "planner";

export default function App() {
  const [sessionMode, setSessionMode] = useState<SessionMode | null>(null);
  const [user, setUser] = useState<UserProfile | null>(null);
  const [page, setPage] = useState<Page>("plan");
  const [matrix, setMatrix] = useState<MatrixData | null>(null);
  const [workshops, setWorkshops] = useState<WorkshopData[]>([]);
  const [lines, setLines] = useState<LineData[]>([]);
  const [catalog, setCatalog] = useState<CatalogData | null>(null);
  const [selectedWorkshop, setSelectedWorkshop] = useState<string | null>(null);
  const [selectedLine, setSelectedLine] = useState<number | null>(null);
  const [selectedItem, setSelectedItem] = useState<ScheduleItem | null>(null);
  const [viewDate, setViewDate] = useState(today());
  const [viewDays, setViewDays] = useState<ViewDays>(1);
  const [dayLayout, setDayLayout] = useState<DayLayout>(() => (localStorage.getItem("plan-day-layout") as DayLayout) || "cards");
  const [theme, setTheme] = useState<Theme>(() => (localStorage.getItem("plan-theme") as Theme) || "dark");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.sessionMode(), api.me()]).then(([mode, me]) => {
      setSessionMode(mode); setUser(me); void loadBase(me);
    }).catch(reason => {
      if (!(reason instanceof ApiError && reason.status === 401)) setError(message(reason));
      api.sessionMode().then(setSessionMode).catch(() => undefined);
      setLoading(false);
    });
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("plan-theme", theme);
  }, [theme]);

  const matrixParams = () => {
    const params = new URLSearchParams({ start: viewDate, days: String(viewDays) });
    if (selectedWorkshop) params.set("workshop_code", selectedWorkshop);
    if (selectedLine) params.set("line_id", String(selectedLine));
    return params.toString();
  };

  const loadBase = async (me = user) => {
    setLoading(true); setError(null);
    try {
      const [workshopRows, lineRows, planMatrix, catalogData] = await Promise.all([
        api.workshops(), api.lines(), api.matrix(matrixParams()), api.catalog(),
      ]);
      setWorkshops(workshopRows); setLines(lineRows); setMatrix(planMatrix); setCatalog(catalogData);
      if (me?.role === "master") {
        const own = lineRows.find(line => line.name === me.line_name);
        setSelectedWorkshop(me.workshop_code); setSelectedLine(own?.id || null);
      }
    } catch (reason) { setError(message(reason)); }
    finally { setLoading(false); }
  };

  const refreshPlan = async () => {
    const [nextMatrix, nextCatalog] = await Promise.all([api.matrix(matrixParams()), api.catalog()]);
    setMatrix(nextMatrix); setCatalog(nextCatalog);
  };

  useEffect(() => {
    if (!user || loading) return;
    api.matrix(matrixParams()).then(setMatrix).catch(reason => setError(message(reason)));
  }, [viewDate, viewDays, selectedWorkshop, selectedLine]);

  if (!user) return <Login mode={sessionMode} error={error} onLogin={async (username, password) => {
    setError(null); setLoading(true);
    try { const result = await api.login(username, password); setUser(result.user); await loadBase(result.user); }
    catch (reason) { setError(message(reason)); setLoading(false); }
  }} />;

  const logout = async () => { await api.logout(); setUser(null); setMatrix(null); setCatalog(null); setPage("plan"); };
  const navigateDate = (delta: number) => setViewDate(addDays(viewDate, delta * viewDays));
  const sendCsb = async () => {
    try {
      const result = await api.sendCsbNextDay();
      setNotice(`CSB: тестовое задание на ${formatDate(result.target_date)} подготовлено, ${result.item_count} поз.`);
    } catch (reason) { setError(message(reason)); }
  };

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span>PP</span><div><b>PLAN PORTAL</b><small>ПРОИЗВОДСТВЕННОЕ ПЛАНИРОВАНИЕ</small></div></div>
      <nav>
        <Nav active={page === "plan"} icon="▦" label="План производства" onClick={() => setPage("plan")} />
        <Nav active={page === "catalog"} icon="≡" label="Справочник" onClick={() => setPage("catalog")} />
        {canPlan(user) && <Nav active={page === "import"} icon="⇧" label="Загрузка Excel" onClick={() => setPage("import")} />}
        <Nav active={page === "sources"} icon="◫" label="Источники данных" onClick={() => setPage("sources")} />
        {user.role === "admin" && <Nav active={page === "admin"} icon="⚙" label="Администрирование" onClick={() => setPage("admin")} />}
      </nav>
      <div className="user-card"><span>{initials(user.display_name)}</span><div><b>{user.display_name}</b><small>{user.access_label}{user.line_name ? ` · ${user.line_name}` : ""}</small></div><button title="Выйти" onClick={() => void logout()}>↪</button></div>
    </aside>
    <main>
      <header className="topbar">
        <div><small>PLAN PORTAL · ФК</small><h1>{pageTitle(page)}</h1></div>
        <div className="top-actions">
          <span className="live-dot">● Система работает</span>
          <button className="theme-button" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? "☀ Светлая" : "◐ Тёмная"}</button>
          {page === "plan" && matrix && canPlan(user) && <button className="button secondary" onClick={() => void sendCsb()}>Передать завтра в CSB</button>}
          {page === "plan" && matrix && <a className="button primary" href={api.exportUrl(matrix.plan.id)}>Выгрузить Excel</a>}
        </div>
      </header>
      {error && <Toast tone="error" text={error} close={() => setError(null)} />}
      {notice && <Toast tone="success" text={notice} close={() => setNotice(null)} />}
      <div className="content">{loading ? <Loading /> : <>
        {page === "plan" && <PlanView matrix={matrix} workshops={workshops} lines={lines} user={user}
          selectedWorkshop={selectedWorkshop} selectedLine={selectedLine} viewDate={viewDate} viewDays={viewDays}
          dayLayout={dayLayout}
          onWorkshop={value => { setSelectedWorkshop(value); setSelectedLine(null); }}
          onLine={(workshop, id) => { setSelectedWorkshop(workshop); setSelectedLine(id); }}
          onReset={() => { setSelectedWorkshop(null); setSelectedLine(null); }}
          onDate={setViewDate} onDays={setViewDays} onPrevious={() => navigateDate(-1)} onNext={() => navigateDate(1)}
          onLayout={value => { setDayLayout(value); localStorage.setItem("plan-day-layout", value); }}
          onItem={setSelectedItem} onUpload={() => setPage("import")} />}
        {page === "catalog" && catalog && <CatalogView data={catalog} user={user} onSaved={async text => { setNotice(text); await refreshPlan(); }} />}
        {page === "import" && <ImportView user={user} onImported={async text => { setNotice(text); await refreshPlan(); setPage("plan"); }} />}
        {page === "sources" && catalog && <SourcesView data={catalog} />}
        {page === "admin" && user.role === "admin" && <AdminView onDeleted={async text => { setNotice(text); setMatrix(null); setPage("import"); await loadBase(user); }} onError={setError} />}
      </>}</div>
    </main>
    {selectedItem && matrix && <ItemDrawer item={selectedItem} planId={matrix.plan.id} user={user} lines={lines}
      onClose={() => setSelectedItem(null)} onError={setError}
      onChanged={async text => { setNotice(text); setSelectedItem(null); await refreshPlan(); }} />}
  </div>;
}

function Login({ mode, error, onLogin }: { mode: SessionMode | null; error: string | null; onLogin: (u: string, p: string) => Promise<void> }) {
  const [username, setUsername] = useState("demo.admin");
  const [password, setPassword] = useState("demo");
  const [busy, setBusy] = useState(false);
  const submit = async (event: React.FormEvent) => { event.preventDefault(); setBusy(true); await onLogin(username, password); setBusy(false); };
  return <div className="login-page"><div className="login-brand"><span>PP</span><b>PLAN PORTAL</b></div><form className="login-card" onSubmit={submit}>
    <small>КОРПОРАТИВНЫЙ ДОСТУП</small><h1>Вход в систему</h1><p>Производственный план ФК, справочник линий и статусы исполнения.</p>
    {error && <div className="inline-error">{error}</div>}
    <label>Логин<input autoFocus autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} /></label>
    <label>Пароль<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></label>
    <button className="button primary wide" disabled={busy}>{busy ? "Входим…" : "Войти"}</button>
    <footer>{mode?.auth_mode === "ldap" ? "Авторизация через корпоративный LDAP" : `Локальный режим · ${mode?.mock_hint || "demo.admin / demo"}`}</footer>
  </form></div>;
}

function Nav({ active, icon, label, onClick }: { active: boolean; icon: string; label: string; onClick: () => void }) {
  return <button className={active ? "active" : ""} onClick={onClick}><i>{icon}</i>{label}</button>;
}

function PlanView({ matrix, workshops, lines, user, selectedWorkshop, selectedLine, viewDate, viewDays, dayLayout, onWorkshop, onLine, onReset, onDate, onDays, onPrevious, onNext, onLayout, onItem, onUpload }: {
  matrix: MatrixData | null; workshops: WorkshopData[]; lines: LineData[]; user: UserProfile;
  selectedWorkshop: string | null; selectedLine: number | null; viewDate: string; viewDays: ViewDays;
  dayLayout: DayLayout;
  onWorkshop: (v: string) => void; onLine: (w: string, id: number) => void; onReset: () => void;
  onDate: (v: string) => void; onDays: (v: ViewDays) => void; onPrevious: () => void; onNext: () => void;
  onLayout: (v: DayLayout) => void;
  onItem: (v: ScheduleItem) => void; onUpload: () => void;
}) {
  if (!matrix) return <Empty title="Производственный план не загружен" text="Справочник сохранён. Загрузите недельный ОХЛ или квартальный ЗАМ, чтобы сформировать новый план." action={canPlan(user) ? <button className="button primary" onClick={onUpload}>Загрузить Excel</button> : undefined} />;
  const cells = matrix.workshops.flatMap(w => w.lines.flatMap(l => l.cells));
  const items = cells.flatMap(c => c.items);
  const totalKg = items.reduce((sum, item) => sum + Number(item.quantity_kg || 0), 0);
  const capacity = cells.reduce((sum, cell) => sum + Number(cell.capacity_hours), 0);
  const planned = cells.reduce((sum, cell) => sum + Number(cell.planned_hours), 0);
  const load = capacity ? planned / capacity * 100 : 0;
  const currentLine = lines.find(line => line.id === selectedLine);
  return <div className="stack">
    <section className="plan-head card"><div className="plan-title"><div className="crumbs"><button onClick={onReset}>Общий план</button>{selectedWorkshop && <><span>›</span><button onClick={() => onWorkshop(selectedWorkshop)}>{workshops.find(w => w.code === selectedWorkshop)?.name}</button></>}{currentLine && <><span>›</span><b>{currentLine.name}</b></>}</div><h2>{currentLine ? currentLine.name : selectedWorkshop ? `Цех ${workshops.find(w => w.code === selectedWorkshop)?.name}` : matrix.plan.name}</h2><p>Версия {matrix.plan.version} · SKU раскрыты непосредственно в плане</p></div><div className="status-chip">{planStatus(matrix.plan.status)}</div></section>
    <section className="date-toolbar card"><div className="range-buttons"><button onClick={onPrevious}>‹</button><input type="date" value={viewDate} onChange={e => onDate(e.target.value)} /><button onClick={() => onDate(today())}>Сегодня</button><button onClick={onNext}>›</button></div><div className="toolbar-switches"><div className="view-switch">{([1, 7, 21] as ViewDays[]).map(value => <button key={value} className={viewDays === value ? "active" : ""} onClick={() => onDays(value)}>{value === 1 ? "День" : value === 7 ? "Неделя" : "3 недели"}</button>)}</div>{viewDays === 1 && <div className="view-switch layout-switch"><button className={dayLayout === "cards" ? "active" : ""} onClick={() => onLayout("cards")}>▦ Карточки</button><button className={dayLayout === "table" ? "active" : ""} onClick={() => onLayout("table")}>☷ Таблица</button></div>}</div></section>
    <section className="metric-grid"><Metric label="Позиций в периоде" value={number(items.length)} note={`${number(totalKg)} кг`} tone="red" /><Metric label="ОХЛ" value={`${number(items.filter(i => i.source_kind === "ohl").reduce((s, i) => s + Number(i.quantity_kg || 0), 0))} кг`} note="по датам источника" tone="blue" /><Metric label="ЗАМ" value={`${number(items.filter(i => i.source_kind === "zam").reduce((s, i) => s + Number(i.quantity_kg || 0), 0))} кг`} note="в свободной мощности" tone="violet" /><Metric label="Загрузка" value={`${load.toFixed(1)}%`} note={`${number(Math.max(0, capacity - planned))} свободных ч`} tone={load >= 98 ? "green" : "amber"} /></section>
    {user.role !== "master" && <section className="workshop-strip"><button className={!selectedWorkshop ? "active" : ""} onClick={onReset}><b>Все цеха</b><small>{workshops.filter(w => w.code !== "UNASSIGNED").reduce((s, w) => s + w.lines.length, 0)} линий</small></button>{workshops.filter(w => w.code !== "UNASSIGNED").map(w => <button key={w.code} className={selectedWorkshop === w.code ? "active" : ""} onClick={() => onWorkshop(w.code)}><b>{w.name}</b><small>{w.lines.length} линий</small></button>)}</section>}
    <div className="capacity-note"><b>22 часа производства на линию</b><span>две смены по 11 часов · 2 часа обеда в сутки</span></div>
    {viewDays === 1 ? <DailyBoard matrix={matrix} layout={dayLayout} onLine={onLine} onItem={onItem} /> : <PlanMatrix matrix={matrix} onLine={onLine} onItem={onItem} />}
  </div>;
}

function DailyBoard({ matrix, layout, onLine, onItem }: { matrix: MatrixData; layout: DayLayout; onLine: (w: string, id: number) => void; onItem: (i: ScheduleItem) => void }) {
  const date = matrix.dates[0];
  if (layout === "table") return <DailyTable matrix={matrix} onItem={onItem} />;
  return <div className="daily-board stack">{matrix.workshops.map(workshop => <section className="card daily-workshop" key={workshop.code}><header><div><span>{workshop.code}</span><h3>{workshop.name}</h3></div><small>{formatDate(date)} · {workshop.lines.reduce((s, l) => s + l.cells[0].items.length, 0)} позиций</small></header>{workshop.lines.map(line => {
    const cell = line.cells[0]; return <article className="daily-line" key={line.id}><button className="daily-line-name" onClick={() => onLine(workshop.code, line.id)}><b>{line.name}</b><small>{cell.load_percent.toFixed(0)}% · {number(cell.planned_hours)} / {number(cell.capacity_hours)} ч</small><i><span style={{ width: `${Math.min(100, cell.load_percent)}%` }} /></i></button><div className="daily-items">{cell.items.length ? cell.items.map(item => <SkuCard key={item.id} item={item} onClick={() => onItem(item)} />) : <div className="free-slot"><b>Свободная мощность</b><small>{number(cell.gap_hours)} ч доступно</small></div>}</div></article>;
  })}</section>)}</div>;
}

function PlanMatrix({ matrix, onLine, onItem }: { matrix: MatrixData; onLine: (w: string, id: number) => void; onItem: (i: ScheduleItem) => void }) {
  return <section className="matrix-card card"><header className="matrix-toolbar"><div><h3>План по линиям и дням</h3><p>Артикулы и задания отображаются прямо в ячейках.</p></div><div className="legend"><span><i className="dot ohl" />ОХЛ</span><span><i className="dot zam" />ЗАМ</span></div></header><div className="matrix-scroll"><div className="matrix" style={{ gridTemplateColumns: `220px repeat(${matrix.dates.length}, 220px)` }}><div className="matrix-corner">Цех / линия</div>{matrix.dates.map(day => <div className="date-head" key={day}><small>{weekday(day)}</small><b>{shortDate(day)}</b><em>{weekNumber(day)} нед.</em></div>)}{matrix.workshops.map(workshop => <div className="workshop-fragment" key={workshop.code}><div className="workshop-row"><span>{workshop.code}</span><b>{workshop.name}</b></div>{workshop.lines.map(line => <div className="line-fragment" key={line.id}><button className="line-name" onClick={() => onLine(workshop.code, line.id)}><b>{line.name}</b><small>{line.code}</small></button>{line.cells.map(cell => <div key={cell.date} className={`plan-cell ${cellTone(cell)}`}><div className="cell-load"><b>{cell.load_percent.toFixed(0)}%</b><small>{number(cell.planned_hours)}/{number(cell.capacity_hours)} ч</small></div><div className="cell-bar"><i style={{ width: `${Math.min(100, cell.load_percent)}%` }} /></div><div className="cell-skus">{cell.items.slice(0, 5).map(item => <button key={item.id} onClick={() => onItem(item)}><span className={`source ${item.source_kind}`}>{sourceLabels[item.source_kind]}</span><b>{item.sku}</b><small title={item.product_name}>{item.product_name}</small><em>{number(item.quantity_kg || 0)} кг · {item.shift === "day" ? "д" : "н"}</em></button>)}{cell.items.length > 5 && <strong>+ ещё {cell.items.length - 5}</strong>}{!cell.items.length && <div className="empty-capacity">{number(cell.gap_hours)} ч свободно</div>}</div></div>)}</div>)}</div>)}</div></div></section>;
}

function SkuCard({ item, onClick }: { item: ScheduleItem; onClick: () => void }) {
  return <button className="sku-card" onClick={onClick}><div className="sku-card-head"><span className={`source ${item.source_kind}`}>{sourceLabels[item.source_kind]}</span><b>{item.sku}</b><em className={item.execution_status}>{executionLabels[item.execution_status]}</em></div><h4>{item.product_name}</h4><div className="sku-facts"><span><small>Смена</small><b>{item.shift === "day" ? "День" : "Ночь"}</b></span><span className="hours-fact"><small>Часы</small><b>{number(item.required_hours)} ч</b></span><span><small>Задание</small><b>{number(item.quantity_kg || 0)} кг</b></span><span><small>Короба</small><b>{item.box_count == null ? "—" : number(item.box_count)}</b></span><span><small>Замесы</small><b>{item.batch_count == null ? "—" : number(item.batch_count)}</b></span><span><small>ДП</small><b>{shortDate(item.production_date || "")}</b></span><span><small>ДМ</small><b>{item.marking_date ? shortDate(item.marking_date) : "—"}</b></span></div></button>;
}

function DailyTable({ matrix, onItem }: { matrix: MatrixData; onItem: (i: ScheduleItem) => void }) {
  const rows = matrix.workshops.flatMap(workshop => workshop.lines.flatMap(line => line.cells[0].items.map(item => ({ workshop, line, cell: line.cells[0], item }))));
  return <section className="card day-table"><header><div><h3>Табличный план ГП</h3><p>{formatDate(matrix.dates[0])} · {rows.length} позиций · максимум 22 производственных часа на линию</p></div></header><div className="table-scroll"><table><thead><tr><th>Цех</th><th>Линия</th><th>Источник</th><th>SKU / готовая продукция</th><th>Смена</th><th>Часы</th><th>Задание</th><th>Короба</th><th>Замесы</th><th>ДП</th><th>ДМ</th><th>Загрузка линии</th><th>Статус</th></tr></thead><tbody>{rows.map(({ workshop, line, cell, item }) => <tr className="clickable-row" key={item.id} onClick={() => onItem(item)}><td><span className="workshop-code">{workshop.name}</span></td><td><b>{line.name}</b><small>{line.code}</small></td><td><span className={`source ${item.source_kind}`}>{sourceLabels[item.source_kind]}</span></td><td><b>{item.sku}</b><small>{item.product_name}</small></td><td>{item.shift === "day" ? "День" : "Ночь"}</td><td><strong className="hours-value">{number(item.required_hours)} ч</strong></td><td><b>{number(item.quantity_kg || 0)} кг</b></td><td>{item.box_count == null ? "—" : number(item.box_count)}</td><td>{item.batch_count == null ? "—" : number(item.batch_count)}</td><td>{formatDate(item.production_date)}</td><td>{formatDate(item.marking_date)}</td><td><b>{cell.load_percent.toFixed(0)}%</b><small>{number(cell.planned_hours)} / {number(cell.capacity_hours)} ч</small></td><td><span className={`execution-badge ${item.execution_status}`}>{executionLabels[item.execution_status]}</span></td></tr>)}</tbody></table></div></section>;
}

function ItemDrawer({ item, planId, user, lines, onClose, onChanged, onError }: { item: ScheduleItem; planId: number; user: UserProfile; lines: LineData[]; onClose: () => void; onChanged: (t: string) => Promise<void>; onError: (t: string) => void }) {
  const [status, setStatus] = useState(item.execution_status); const [note, setNote] = useState(item.execution_note || "");
  const [date, setDate] = useState(item.production_date || ""); const [shift, setShift] = useState(item.shift);
  const [quantity, setQuantity] = useState(String(item.quantity_kg || item.quantity)); const [lineId, setLineId] = useState(String(item.line_id || ""));
  const [busy, setBusy] = useState(false);
  const saveStatus = async () => { setBusy(true); try { await api.updateExecution(planId, item.id, status, note); await onChanged("Статус исполнения сохранён и записан в аудит"); } catch (e) { onError(message(e)); } finally { setBusy(false); } };
  const savePlan = async () => { setBusy(true); try { await api.updateItem(planId, item.id, { production_date: date, shift, quantity: Number(quantity), line_id: Number(lineId), locked: true, comment: "Корректировка в PLAN Portal" }); await onChanged("Задание изменено, загрузка пересчитана"); } catch (e) { onError(message(e)); } finally { setBusy(false); } };
  return <div className="drawer-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}><aside className="drawer"><header><div><small>{item.workshop_name} · {item.line_name}</small><h2>{item.sku}</h2><p>{item.product_name}</p></div><button onClick={onClose}>×</button></header><div className="item-editor"><section><h3>Производственное задание</h3><dl><div><dt>Источник</dt><dd>{sourceLabels[item.source_kind]}</dd></div><div><dt>Задание</dt><dd>{number(item.quantity_kg || 0)} кг</dd></div><div><dt>Время</dt><dd>{number(item.required_hours)} ч</dd></div><div><dt>Коробов</dt><dd>{item.box_count == null ? "—" : number(item.box_count)}</dd></div><div><dt>Замесов</dt><dd>{item.batch_count == null ? "—" : number(item.batch_count)}</dd></div><div><dt>ДМ</dt><dd>{formatDate(item.marking_date)}</dd></div></dl>{item.warnings.length > 0 && <div className="warning-box">{item.warnings.map(value => <p key={value}>! {value}</p>)}</div>}</section>{user.role !== "viewer" && <section><h3>Исполнение / отгрузка</h3><label>Статус<select value={status} onChange={e => setStatus(e.target.value as ScheduleItem["execution_status"])}>{Object.entries(executionLabels).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label><label>Причина / комментарий<textarea value={note} onChange={e => setNote(e.target.value)} placeholder="Обязательно при неотгрузке" /></label><button className="button primary wide" disabled={busy} onClick={() => void saveStatus()}>Сохранить статус</button></section>}{canPlan(user) && <section><h3>Корректировка планера</h3><div className="form-grid"><label>Дата<input type="date" value={date} onChange={e => setDate(e.target.value)} /></label><label>Смена<select value={shift} onChange={e => setShift(e.target.value)}><option value="day">День</option><option value="night">Ночь</option></select></label><label>Задание, кг<input type="number" value={quantity} onChange={e => setQuantity(e.target.value)} /></label><label>Линия<select value={lineId} onChange={e => setLineId(e.target.value)}>{lines.map(l => <option value={l.id} key={l.id}>{l.workshop_name} · {l.name}</option>)}</select></label></div><button className="button dark wide" disabled={busy} onClick={() => void savePlan()}>Применить и пересчитать</button></section>}</div></aside></div>;
}

function CatalogView({ data, user, onSaved }: { data: CatalogData; user: UserProfile; onSaved: (t: string) => Promise<void> }) {
  const [query, setQuery] = useState(""); const [workshop, setWorkshop] = useState(""); const [editing, setEditing] = useState<CatalogRow | null>(null);
  const rows = useMemo(() => data.rows.filter(row => (!workshop || row.workshop_code === workshop) && (!query || `${row.sku} ${row.product_name} ${row.line_name}`.toLowerCase().includes(query.toLowerCase()))), [data, query, workshop]);
  return <div className="stack"><section className="metric-grid"><Metric label="Артикулов" value={number(data.summary.products)} note="единый справочник" tone="red" /><Metric label="Связей линия–SKU" value={number(data.summary.capabilities)} note="скорость и квант" tone="blue" /><Metric label="Линий" value={number(data.summary.lines)} note="ПЦ / КЦ" tone="green" /><Metric label="SKU с рецептурой" value={number(data.summary.with_recipes)} note="компоненты сырья" tone="amber" /></section><section className="card catalog-card"><div className="catalog-head"><div><h2>Мощности, замесы и технологические параметры</h2><p>Актуальные параметры из производственных файлов.</p></div><div className="filters"><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Артикул, название, линия…" /><select value={workshop} onChange={e => setWorkshop(e.target.value)}><option value="">Все цеха</option><option value="PC">ПЦ</option><option value="KC">КЦ</option></select></div></div><div className="table-scroll"><table><thead><tr><th>Артикул / продукция</th><th>Цех / линия</th><th>Скорость</th><th>Квант замеса</th><th>Мин. заказ</th><th>Короб</th><th>Старый план</th><th>Рецептура</th>{canPlan(user) && <th />}</tr></thead><tbody>{rows.map(row => <tr key={row.capability_id}><td><b>{row.sku}</b><small>{row.product_name}</small></td><td><span className="workshop-code">{row.workshop_name}</span><small>{row.line_name}</small></td><td><strong>{number(row.speed_kg_hour)} кг/ч</strong></td><td>{row.batch_quantum_kg ? `${number(row.batch_quantum_kg)} кг` : "—"}</td><td>{row.min_order_kg ? `${number(row.min_order_kg)} кг` : "—"}</td><td>{row.units_per_box ? `${number(row.units_per_box)} шт.` : "—"}</td><td>{row.legacy_quantum_units ? `${number(row.legacy_quantum_units)} ${row.legacy_capacity_unit}` : "—"}</td><td>{row.recipe_component_count ? `${row.recipe_component_count} комп.` : "—"}</td>{canPlan(user) && <td><button className="icon-button" onClick={() => setEditing(row)}>✎</button></td>}</tr>)}</tbody></table></div><footer>Показано {rows.length} из {data.rows.length}</footer></section>{editing && <CatalogEditor row={editing} onClose={() => setEditing(null)} onSaved={async () => { setEditing(null); await onSaved("Параметры линии обновлены"); }} />}</div>;
}

function CatalogEditor({ row, onClose, onSaved }: { row: CatalogRow; onClose: () => void; onSaved: () => Promise<void> }) {
  const [speed, setSpeed] = useState(String(row.speed_kg_hour)); const [quantum, setQuantum] = useState(String(row.batch_quantum_kg || "")); const [minimum, setMinimum] = useState(String(row.min_order_kg || "")); const [restrictions, setRestrictions] = useState(row.restrictions || ""); const [error, setError] = useState("");
  const save = async () => { try { await api.updateCapability(row.capability_id, { units_per_hour: Number(speed), batch_quantum_kg: quantum ? Number(quantum) : undefined, min_order_kg: minimum ? Number(minimum) : undefined, restrictions }); await onSaved(); } catch (e) { setError(message(e)); } };
  return <div className="modal-backdrop"><div className="modal"><header><div><small>{row.sku} · {row.line_name}</small><h2>Параметры линии</h2></div><button onClick={onClose}>×</button></header>{error && <div className="inline-error">{error}</div>}<div className="form-grid"><label>Скорость, кг/ч<input type="number" value={speed} onChange={e => setSpeed(e.target.value)} /></label><label>Квант замеса, кг<input type="number" value={quantum} onChange={e => setQuantum(e.target.value)} /></label><label>Минимальный заказ, кг<input type="number" value={minimum} onChange={e => setMinimum(e.target.value)} /></label><label className="full">Ограничения<textarea value={restrictions} onChange={e => setRestrictions(e.target.value)} /></label></div><footer><button className="button secondary" onClick={onClose}>Отмена</button><button className="button primary" onClick={() => void save()}>Сохранить</button></footer></div></div>;
}

function ImportView({ user, onImported }: { user: UserProfile; onImported: (t: string) => Promise<void> }) {
  const [preview, setPreview] = useState<ImportPreview | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  if (!canPlan(user)) return <Empty title="Загрузка недоступна" text="Для загрузки нужен доступ планера или администратора." />;
  const choose = async (file?: File) => { if (!file) return; setBusy(true); setError(""); try { setPreview(await api.previewImport(file)); } catch (e) { setError(message(e)); } finally { setBusy(false); } };
  const confirm = async () => { if (!preview) return; setBusy(true); try { const result = await api.confirmImport(preview); setPreview(null); await onImported(result.reference_updated != null ? `Справочник обновлён: ${result.reference_updated} строк` : "Файл загружен, общий план пересчитан"); } catch (e) { setError(message(e)); } finally { setBusy(false); } };
  return <div className="stack"><section className="card import-hero"><div><span>XL</span><div><small>АВТОМАТИЧЕСКАЯ ЗАГРУЗКА</small><h2>Загрузить производственные данные</h2><p>ОХЛ, ЗАМ, актуальный справочник или старый план распознаются автоматически.</p></div></div><label className="button primary">Выбрать Excel<input type="file" accept=".xlsx,.xlsm" onChange={e => void choose(e.target.files?.[0])} /></label></section>{error && <div className="inline-error">{error}</div>}{busy && <Loading />}{preview && <section className="card preview-card"><header><div><small>{templateLabel(preview.template_type)}</small><h2>{preview.file_name}</h2><p>Лист «{preview.detected_sheet}» · {preview.total_rows} строк</p></div><div className="preview-stats"><span className="valid"><b>{preview.valid_rows}</b> корректно</span><span className="invalid"><b>{preview.invalid_rows}</b> ошибки</span></div></header><div className="notes">{preview.notes.map(note => <p key={note}>✓ {note}</p>)}</div><div className="table-scroll"><table><thead><tr><th>Строка</th><th>SKU / наименование</th><th>Источник</th><th>План, кг</th><th>ДП / ДМ</th><th>Линия</th><th>Квант</th><th>Контроль</th></tr></thead><tbody>{preview.rows.slice(0, 150).map((row, i) => <tr key={`${row.row_number}-${i}`}><td>{row.row_number}</td><td><b>{row.sku}</b><small>{row.product_name}</small></td><td>{row.source_quantity != null ? `${number(row.source_quantity)} ${row.source_unit}` : templateLabel(preview.template_type)}</td><td>{row.quantity_kg == null ? "—" : number(row.quantity_kg)}</td><td><b>{formatDate(row.requested_date)}</b>{row.marking_date && <small>ДМ {formatDate(row.marking_date)}</small>}</td><td>{row.line_hint || "—"}</td><td>{row.batch_quantum_kg ? `${number(row.batch_quantum_kg)} кг` : row.legacy_quantum_units ? `${number(row.legacy_quantum_units)} шт.` : "—"}</td><td>{!row.valid ? <span className="bad">! {row.errors[0]}</span> : row.warnings.length ? <span className="warn">! {row.warnings[0]}</span> : <span className="good">✓</span>}</td></tr>)}</tbody></table></div><footer><button className="button secondary" onClick={() => setPreview(null)}>Другой файл</button><button className="button primary" disabled={!preview.valid_rows || busy} onClick={() => void confirm()}>{preview.template_type.includes("reference") ? "Обновить справочник" : "Загрузить и рассчитать"}</button></footer></section>}</div>;
}

function SourcesView({ data }: { data: CatalogData }) { return <div className="stack"><section className="source-intro card"><div><small>ЕДИНАЯ МОДЕЛЬ ДАННЫХ</small><h2>Журнал исходных файлов</h2><p>ОХЛ фиксирует даты, ЗАМ занимает доступную мощность, справочники дают квантовки, скорости и рецептуры.</p></div><div className="source-flow"><span>ОХЛ</span><i>+</i><span>ЗАМ</span><i>→</i><b>План</b></div></section><section className="card source-list"><header><h2>Загрузки</h2><p>Ошибочные строки не попадают в расчёт.</p></header>{data.sources.map(source => <article key={source.id}><span className="source-icon">XL</span><div><b>{source.file_name}</b><small>{templateLabel(source.template_type)} · {new Date(source.imported_at).toLocaleString("ru-RU")}</small></div><em>{source.valid_rows} / {source.total_rows}</em><strong className={source.invalid_rows ? "warn" : "good"}>{source.invalid_rows ? `${source.invalid_rows} ошибок` : "Проверено"}</strong></article>)}</section></div>; }

function AdminView({ onDeleted, onError }: { onDeleted: (t: string) => Promise<void>; onError: (t: string) => void }) {
  const [data, setData] = useState<AdminOverview | null>(null); const [confirmation, setConfirmation] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { api.adminOverview().then(setData).catch(e => onError(message(e))); }, []);
  if (!data) return <Loading />;
  const remove = async () => { setBusy(true); try { const result = await api.deletePlanData(confirmation); await onDeleted(`Удалено планов: ${result.plans_deleted}, заданий: ${result.schedule_items_deleted}. Справочник сохранён.`); } catch (e) { onError(message(e)); } finally { setBusy(false); } };
  return <div className="stack"><section className="metric-grid"><Metric label="Планы" value={number(data.counts.plans)} note={data.active_plan?.name || "нет активного"} tone="red" /><Metric label="Задания" value={number(data.counts.schedule_items)} note="производственные позиции" tone="blue" /><Metric label="LDAP" value={data.ldap.configured ? "Настроен" : "Локальный"} note={data.ldap.status} tone="green" /><Metric label="Почта" value={data.email.enabled ? "Включена" : "Выключена"} note={data.email.host} tone="amber" /></section><section className="admin-grid"><article className="card admin-panel"><header><small>ОПАСНАЯ ОПЕРАЦИЯ</small><h2>Очистить весь производственный план</h2><p>Будут удалены планы, задания и загруженные файлы спроса ОХЛ/ЗАМ. Справочник SKU, мощности, замесы и рецептуры сохранятся.</p></header><label>Введите «УДАЛИТЬ ПЛАН»<input value={confirmation} onChange={e => setConfirmation(e.target.value)} /></label><button className="button danger" disabled={busy || confirmation !== "УДАЛИТЬ ПЛАН"} onClick={() => void remove()}>Удалить весь план</button></article><article className="card admin-panel"><header><small>ИНТЕГРАЦИИ</small><h2>Готовность контуров</h2></header><div className="settings-list"><span><b>LDAP / AD</b><em>{data.ldap.status}</em></span><span><b>Почтовые уведомления</b><em>{data.email.enabled ? "включены" : "отключены"}</em></span><span><b>CSB</b><em>{data.csb.test_mode ? "тестовый режим" : "настроен"}</em></span></div></article></section><section className="card audit-card"><header><h2>Пользователи и права</h2><p>Роли приходят из LDAP и могут быть уточнены администратором; мастер закрепляется за одной линией.</p></header><div className="table-scroll"><table><thead><tr><th>Пользователь</th><th>Роль</th><th>Линия мастера</th><th>Состояние</th><th /></tr></thead><tbody>{data.users.map(row => <AdminUserRow key={row.id} row={row} lines={data.lines} onSaved={() => api.adminOverview().then(setData)} onError={onError} />)}</tbody></table></div></section><section className="card audit-card"><header><h2>Журнал аудита</h2><p>Последние действия пользователей.</p></header><div className="table-scroll"><table><thead><tr><th>Дата</th><th>Пользователь</th><th>Действие</th><th>Объект</th></tr></thead><tbody>{data.recent_audit.map(row => <tr key={row.id}><td>{new Date(row.created_at).toLocaleString("ru-RU")}</td><td><b>{row.username}</b></td><td>{auditLabel(row.action)}</td><td>{row.entity_type} {row.entity_id || ""}</td></tr>)}</tbody></table></div></section></div>;
}

function AdminUserRow({ row, lines, onSaved, onError }: { row: AdminOverview["users"][number]; lines: AdminOverview["lines"]; onSaved: () => Promise<void>; onError: (v: string) => void }) {
  const [role, setRole] = useState(row.role); const currentLine = lines.find(line => line.name === row.line_name); const [lineId, setLineId] = useState(String(currentLine?.id || "")); const [active, setActive] = useState(row.active); const [busy, setBusy] = useState(false);
  const save = async () => { setBusy(true); try { await api.updateUserAccess(row.id, role, lineId ? Number(lineId) : null, active); await onSaved(); } catch (e) { onError(message(e)); } finally { setBusy(false); } };
  return <tr><td><b>{row.display_name}</b><small>{row.username}{row.email ? ` · ${row.email}` : ""}</small></td><td><select value={role} onChange={e => setRole(e.target.value)}><option value="admin">Администратор</option><option value="planner">Планер</option><option value="master">Мастер</option><option value="viewer">Просмотр</option></select></td><td><select disabled={role !== "master"} value={lineId} onChange={e => setLineId(e.target.value)}><option value="">Выберите линию</option>{lines.map(line => <option value={line.id} key={line.id}>{line.workshop_name} · {line.name}</option>)}</select></td><td><label className="check-label"><input type="checkbox" checked={active} onChange={e => setActive(e.target.checked)} /> Активен</label></td><td><button className="button secondary" disabled={busy} onClick={() => void save()}>Сохранить</button></td></tr>;
}

function Metric({ label, value, note, tone }: { label: string; value: string; note: string; tone: string }) { return <article className={`metric ${tone}`}><small>{label}</small><b>{value}</b><span>{note}</span></article>; }
function Toast({ tone, text, close }: { tone: string; text: string; close: () => void }) { return <div className={`toast ${tone}`}>{tone === "success" ? "✓" : "!"}<span>{text}</span><button onClick={close}>×</button></div>; }
function Loading() { return <div className="loading"><i /><p>Загружаем производственные данные…</p></div>; }
function Empty({ title, text, action }: { title: string; text: string; action?: React.ReactNode }) { return <section className="empty"><span>◇</span><h2>{title}</h2><p>{text}</p>{action}</section>; }
function pageTitle(page: Page) { return ({ plan: "План производства", catalog: "Справочник", import: "Загрузка Excel", sources: "Источники данных", admin: "Администрирование" } as const)[page]; }
function templateLabel(v: string) { return ({ ohl_daily: "Недельный план ОХЛ", quarter_weekly: "Квартальный план ЗАМ", production_reference: "Актуальный справочник ПЦ/КЦ", legacy_reference: "Старый план и рецептуры", generic: "Универсальный шаблон" } as Record<string, string>)[v] || v; }
function planStatus(v: string) { return ({ needs_review: "Требует проверки", calculated: "Рассчитан", approved: "Утверждён", draft: "Черновик" } as Record<string, string>)[v] || v; }
function auditLabel(v: string) { return ({ plan_imported: "План загружен", reference_imported: "Справочник обновлён", schedule_item_updated: "Задание изменено", schedule_item_deleted: "Задание удалено", execution_status_updated: "Статус исполнения", plan_approved: "План утверждён", plan_data_deleted: "Все планы удалены", csb_next_day_prepared: "Задание CSB подготовлено", user_access_updated: "Права пользователя изменены" } as Record<string, string>)[v] || v; }
function message(reason: unknown) { return reason instanceof Error ? reason.message : "Неизвестная ошибка"; }
function number(value: number | string) { return Number(value || 0).toLocaleString("ru-RU", { maximumFractionDigits: 2 }); }
function formatDate(value?: string | null) { return value ? new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`)) : "—"; }
function shortDate(value: string) { return value ? new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short" }).format(new Date(`${value}T00:00:00`)) : "—"; }
function weekday(value: string) { return new Intl.DateTimeFormat("ru-RU", { weekday: "short" }).format(new Date(`${value}T00:00:00`)); }
function weekNumber(value: string) { const d = new Date(`${value}T00:00:00`); const u = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())); const day = u.getUTCDay() || 7; u.setUTCDate(u.getUTCDate() + 4 - day); const start = new Date(Date.UTC(u.getUTCFullYear(), 0, 1)); return Math.ceil((((u.getTime() - start.getTime()) / 86400000) + 1) / 7); }
function addDays(value: string, days: number) { const date = new Date(`${value}T12:00:00`); date.setDate(date.getDate() + days); return date.toLocaleDateString("sv-SE"); }
function cellTone(cell: MatrixCell) { if (cell.load_percent > 100) return "over"; if (cell.load_percent >= 98) return "full"; if (cell.load_percent >= 80) return "high"; if (cell.items.length) return "partial"; return "empty"; }
function initials(value: string) { return value.split(/\s|·/).filter(Boolean).slice(0, 2).map(v => v[0]).join("").toUpperCase(); }
