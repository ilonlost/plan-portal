import { useEffect, useState } from "react";
import { api } from "./api";
import { matchesCatalogText } from "./catalogSearch";

export interface PFMappingItem {
  sku: string; name: string; kind: "pf" | "filling"; line_id: number | null;
  speed_kg_hour: number | string | null; catalog_speed_kg_hour?: number | string | null;
  lead_days: number; note: string; enabled: boolean; date_defaulted?: boolean;
}
export interface PFMapping {
  gp_sku: string; gp_name?: string; gp_line_id: number | null;
  gp_speed_kg_hour: number | string | null; revision: number; items: PFMappingItem[];
  origin?: string; updated_by?: string;
}
export interface PFMappingCatalog {
  mappings: PFMapping[];
  products: { sku: string; name: string; product_id: number | null; active: boolean; capabilities: { line_id: number; speed_kg_hour: number | string }[] }[];
  lines: { id: number; name: string; workshop_name: string; status: string }[];
}
const clone = <T,>(value: T): T => structuredClone(value);
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const blank = (): PFMapping => ({ gp_sku: "", gp_line_id: null, gp_speed_kg_hour: null, revision: 0, items: [], origin: "new" });

export function PFMappings() {
  const [data, setData] = useState<PFMappingCatalog | null>(null);
  const [query, setQuery] = useState("");
  const [lineFilter, setLineFilter] = useState("");
  const [form, setForm] = useState<PFMapping | null>(null);
  const [baseline, setBaseline] = useState<PFMapping | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => { let current = true; api.pfMappings().then(value => { if (current) setData(value); }).catch(reason => { if (current) setError(errorText(reason)); }); return () => { current = false; }; }, []);
  const open = (mapping: PFMapping) => { setForm(clone(mapping)); setBaseline(clone(mapping)); setDirty(false); setError(""); setNotice(""); };
  const select = (mapping: PFMapping) => {
    if (dirty) { setError("Сохраните или отмените текущие изменения перед выбором другого ГП."); return; }
    open(mapping);
  };
  const change = (values: Partial<PFMapping>) => { setForm(current => current ? { ...current, ...values } : current); setDirty(true); setNotice(""); };
  const changeItem = (index: number, values: Partial<PFMappingItem>) => { if (form) change({ items: form.items.map((item, i) => i === index ? { ...item, ...values } : item) }); };
  const lineName = (id: number | null) => { const line = data?.lines.find(value => value.id === id); return line ? `${line.workshop_name} · ${line.name}` : id ? `Линия ${id} недоступна` : "По справочнику"; };
  const gp = data?.products.find(product => product.sku === form?.gp_sku);
  const scopeLines = data?.lines.filter(line => line.status === "active" && gp?.capabilities.some(cap => cap.line_id === line.id && Number(cap.speed_kg_hour) > 0)) || [];
  const visible = data?.mappings.filter(mapping => {
    const products = data.products.find(product => product.sku === mapping.gp_sku);
    return matchesCatalogText(`${mapping.gp_sku} ${mapping.gp_name} ${mapping.items.map(item => `${item.sku} ${item.name}`).join(" ")}`, query)
      && (!lineFilter || (mapping.gp_line_id ? mapping.gp_line_id === Number(lineFilter) : products?.capabilities.some(cap => cap.line_id === Number(lineFilter))));
  }) || [];
  const chooseGP = (value: string) => {
    const sku = value.split(" · ")[0].trim();
    const existing = data?.mappings.find(mapping => mapping.gp_sku === sku && mapping.gp_line_id === null);
    if (existing) open(existing);
    else { const product = data?.products.find(product => product.sku === sku); if (product) open({ ...blank(), gp_sku: sku, gp_name: product.name }); else setForm({ ...blank(), gp_sku: sku }); }
  };
  const save = async () => {
    if (!form) return;
    setBusy(true); setError(""); setNotice("");
    const payload: PFMapping = { gp_sku: form.gp_sku, gp_line_id: form.gp_line_id, gp_speed_kg_hour: form.gp_speed_kg_hour, revision: form.revision,
      items: form.items.map(({ sku, name, kind, line_id, speed_kg_hour, lead_days, note, enabled }) => ({ sku, name, kind, line_id, speed_kg_hour, lead_days, note, enabled })) };
    try {
      const saved = await api.savePFMapping(payload);
      // Update the local list immediately, so a failed refresh cannot cause a stale retry.
      setData(current => current ? { ...current, mappings: [...current.mappings.filter(row => row.gp_sku !== saved.gp_sku || row.gp_line_id !== saved.gp_line_id), { ...saved, gp_name: gp?.name || form.gp_name }] } : current);
      open({ ...saved, gp_name: gp?.name || form.gp_name });
      setNotice("Связи сохранены. Они будут использоваться при создании новых заданий ГП.");
    } catch (reason) { setError(errorText(reason)); }
    finally { setBusy(false); }
  };
  const valid = !!form?.gp_sku && !!gp && form.items.every(item => item.sku.trim() && item.name.trim() && item.sku !== form.gp_sku && Number.isInteger(item.lead_days) && item.lead_days >= 0 && item.lead_days <= 365 && (item.speed_kg_hour === null || Number(item.speed_kg_hour) > 0)) && new Set(form.items.map(item => item.sku.trim())).size === form.items.length;
  return <section className="pf-mappings stack">
    <header className="card admin-panel"><h2>Связи ГП → ПФ</h2><p>Сопоставления из исходной таблицы и изменения планеров. Объём ПФ рассчитывается через BOM по количеству штук ГП; здесь задаются состав заданий, линии и сроки. Изменения действуют для новых заданий; уже созданные задания сохраняют свои параметры.</p><div className="inline-actions"><button className="button primary" disabled={busy} onClick={() => select(blank())}>Добавить сопоставление</button><button className="button secondary" disabled={busy || dirty} onClick={async () => { setBusy(true); try { setData(await api.pfMappings()); setForm(null); setError(""); } catch (reason) { setError(errorText(reason)); } finally { setBusy(false); } }}>Обновить список</button></div></header>
    {error && <div className="inline-error" role="alert">{error}</div>}{notice && <div className="planner-message good" role="status">{notice}</div>}
    {!data ? <p>Загрузка связей…</p> : <>
      <section className="card admin-panel"><div className="filters"><input aria-label="Поиск ГП или ПФ" value={query} placeholder="Артикул или название ГП / ПФ" onChange={event => setQuery(event.target.value)} /><select aria-label="Фильтр линии ГП" value={lineFilter} onChange={event => setLineFilter(event.target.value)}><option value="">Все линии ГП</option>{data.lines.filter(line => line.status === "active").map(line => <option key={line.id} value={line.id}>{line.workshop_name} · {line.name}</option>)}</select></div>
      <div className="table-scroll pf-mapping-table"><table><thead><tr><th>ГП</th><th>Линия ГП</th><th>ПФ / начинка</th><th>Линия ПФ</th><th>За сколько дней</th><th>Скорость ПФ, кг/ч</th><th /></tr></thead><tbody>{visible.map(mapping => <tr key={`${mapping.gp_sku}-${mapping.gp_line_id}`} className={form?.gp_sku === mapping.gp_sku && form.gp_line_id === mapping.gp_line_id ? "pf-mapping-selected" : ""}>
        <td><b>{mapping.gp_sku}</b><small>{mapping.gp_name}</small><small>{mapping.origin === "saved" ? "Изменено планером" : "Из исходной таблицы"}</small></td><td>{mapping.gp_line_id ? lineName(mapping.gp_line_id) : "Все линии ГП"}</td>
        <td>{mapping.items.map(item => <div key={item.sku}><b>{item.sku}</b><small>{item.name}{!item.enabled ? " · Отключено" : ""}</small></div>)}{!mapping.items.length && "ПФ не назначены"}</td>
        <td>{mapping.items.map(item => <div key={item.sku}>{lineName(item.line_id)}</div>)}</td><td>{mapping.items.map(item => <div key={item.sku}>{item.lead_days === 0 ? "День в день" : item.lead_days}{item.date_defaulted ? " *" : ""}</div>)}</td><td>{mapping.items.map(item => <div key={item.sku}>{item.speed_kg_hour ?? item.catalog_speed_kg_hour ?? "Не задана"}</div>)}</td><td><button className="button secondary" disabled={busy} onClick={() => select(mapping)}>Изменить</button></td>
      </tr>)}</tbody></table></div><small>Показано {visible.length} из {data.mappings.length}. * В исходнике не было точного срока; установлено 2 дня.</small></section>
      {form && <section className="card admin-panel pf-mapping-editor" aria-label="Редактор связей ГП и ПФ">
        <header><h3>{form.gp_sku ? `${form.gp_sku} · ${form.gp_name || "Выберите ГП"}` : "Новое сопоставление"}</h3><p>{dirty ? "Есть несохранённые изменения" : "Выберите область действия и настройте ПФ"}</p></header>
        <fieldset disabled={busy}><div className="form-grid">
          <label className="full">ГП из справочника<input list="pf-mapping-products" disabled={!!baseline?.gp_sku} value={form.gp_sku} onChange={event => chooseGP(event.target.value)} placeholder="Артикул или наименование" /></label>
          <label>Линия ГП<select aria-label="Линия ГП" value={form.gp_line_id || ""} disabled={dirty} onChange={event => {
            const lineId = Number(event.target.value) || null;
            const existing = data.mappings.find(row => row.gp_sku === form.gp_sku && row.gp_line_id === lineId);
            const original = data.mappings.find(row => row.gp_sku === form.gp_sku && row.gp_line_id === null) || form;
            open(existing || { ...clone(original), gp_line_id: lineId, revision: 0, origin: "new" });
          }}><option value="">Все линии ГП</option>{scopeLines.map(line => <option key={line.id} value={line.id}>{line.workshop_name} · {line.name}</option>)}</select><small>Правило для конкретной линии имеет приоритет над общим.</small></label>
          <label>Скорость этапа ГП, кг/ч<input type="number" min="0.01" max="1000000" step="0.01" value={form.gp_speed_kg_hour ?? ""} placeholder="Из справочника" onChange={event => change({ gp_speed_kg_hour: event.target.value || null })} /><small>Пустое поле — использовать скорость ГП из справочника.</small></label>
        </div>
        {form.items.map((item, index) => <fieldset className="pf-mapping-item" key={index}><legend>{item.kind === "filling" ? "Начинка" : "ПФ"} {index + 1}</legend><div className="form-grid">
          <label>Артикул ПФ<input aria-label={`Артикул ПФ ${index + 1}`} list="pf-mapping-products" value={item.sku} onChange={event => { const sku = event.target.value.split(" · ")[0].trim(); const product = data.products.find(row => row.sku === sku); changeItem(index, { sku, name: product?.name || "", line_id: null, speed_kg_hour: null, catalog_speed_kg_hour: null }); }} /></label>
          <label>Тип<select value={item.kind} onChange={event => changeItem(index, { kind: event.target.value as PFMappingItem["kind"] })}><option value="pf">ПФ</option><option value="filling">Начинка</option></select></label>
          <label className="full">Наименование ПФ<input readOnly={!!data.products.find(product => product.sku === item.sku)?.product_id} value={item.name} onChange={event => changeItem(index, { name: event.target.value })} /><small>Для существующего артикула название берётся из справочника.</small></label>
          <label>Линия ПФ<select aria-label={`Линия ПФ ${index + 1}`} value={item.line_id || ""} onChange={event => { const id = Number(event.target.value) || null; const product = data.products.find(row => row.sku === item.sku); changeItem(index, { line_id: id, speed_kg_hour: null, catalog_speed_kg_hour: product?.capabilities.find(cap => cap.line_id === id)?.speed_kg_hour ?? null }); }}><option value="">По справочнику / выбрать при создании</option>{data.lines.map(line => <option key={line.id} value={line.id} disabled={line.status !== "active"}>{line.workshop_name} · {line.name}{line.status !== "active" ? " · Неактивна" : ""}</option>)}</select></label>
          <label>Скорость ПФ, кг/ч<input aria-label={`Скорость ПФ ${index + 1}`} type="number" min="0.01" max="1000000" step="0.01" value={item.speed_kg_hour ?? ""} placeholder={item.catalog_speed_kg_hour ? `Справочник: ${item.catalog_speed_kg_hour}` : "Из справочника / при создании"} onChange={event => changeItem(index, { speed_kg_hour: event.target.value || null })} /></label>
          <label>За сколько дней до ГП<input aria-label={`Срок ПФ ${index + 1}`} type="number" min="0" max="365" step="1" value={item.lead_days} onChange={event => changeItem(index, { lead_days: event.target.value === "" ? NaN : Number(event.target.value), date_defaulted: false })} /><small>0 — день в день. Начинка тоже считается от даты ГП.</small></label>
          <label className="check-label"><input type="checkbox" checked={item.enabled} onChange={event => changeItem(index, { enabled: event.target.checked })} />Включать в раскрутку</label>
          <label className="full">Примечание<textarea value={item.note} onChange={event => changeItem(index, { note: event.target.value })} /></label>
        </div><button type="button" className="button danger" onClick={() => change({ items: form.items.filter((_, i) => i !== index) })}>Убрать ПФ из сопоставления</button></fieldset>)}
        <button type="button" className="button secondary" onClick={() => change({ items: [...form.items, { sku: "", name: "", kind: "pf", line_id: null, speed_kg_hour: null, lead_days: 2, enabled: true, note: "" }] })}>Добавить ПФ</button>
        {!form.items.length && <p>Связанных ПФ нет. После сохранения этот ГП не будет раскручиваться в выбранной области.</p>}
        <footer><button className="button secondary" onClick={() => { if (baseline) open(baseline); }}>Отменить изменения</button><button className="button primary" disabled={!valid} onClick={() => void save()}>Сохранить связи</button></footer></fieldset>
      </section>}
      <datalist id="pf-mapping-products">{data.products.map(product => <option key={product.sku} value={`${product.sku} · ${product.name}`} />)}</datalist>
    </>}
  </section>;
}
