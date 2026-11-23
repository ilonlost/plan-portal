import { useEffect, useState } from "react";
import { api } from "./api";
import { newPFMapping, PFMappingEditor, type PFMapping, type PFMappingItem, type PFMappingCatalog } from "./PFMappings";
import { matchesCatalogText } from "./catalogSearch";
import type { CatalogProduct, LineData } from "./types";
import "./PFCatalog.css";

export const isPFSku = (sku: string) => /^(102|4)/.test(sku.trim());
type Capability = CatalogProduct["capabilities"][number];
type Editor = { product: CatalogProduct | null; capability: Capability | null };
const number = (value: number) => Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 3 });

type PFCatalogProps = {
  products: CatalogProduct[]; lines: LineData[]; canEdit: boolean; onSaved: (text: string) => Promise<void>;
};

type Link = { mapping: PFMapping; item: PFMappingItem | null };
type PFEntry = { key: string; sku: string; name: string; product?: CatalogProduct; links: Link[] };

function catalogEntries(products: CatalogProduct[], mappings: PFMapping[]) {
  const entries = new Map<string, PFEntry>();
  for (const product of products) {
    if (isPFSku(product.sku) && product.catalog_status !== "deleted") {
      entries.set(product.sku, { key: product.sku, sku: product.sku, name: product.product_name, product, links: [] });
    }
  }
  for (const mapping of mappings) {
    if (!mapping.items.length) {
      const key = JSON.stringify([mapping.gp_sku, mapping.gp_line_id]);
      entries.set(key, { key, sku: "", name: "ПФ не назначены", links: [{ mapping, item: null }] });
    }
    for (const item of mapping.items) {
      const entry = entries.get(item.sku) || { key: item.sku, sku: item.sku, name: item.name, links: [] as Link[] };
      entry.links.push({ mapping, item });
      entries.set(item.sku, entry);
    }
  }
  return [...entries.values()].sort((a, b) => Number(!!b.product?.capabilities.length) - Number(!!a.product?.capabilities.length));
}

export function PFCatalog({ products, lines, canEdit, onSaved }: PFCatalogProps) {
  const [query, setQuery] = useState("");
  const [lineFilter, setLineFilter] = useState("");
  const [gpLine, setGPLine] = useState("");
  const [editor, setEditor] = useState<Editor | null>(null);
  const [catalog, setCatalog] = useState<PFMappingCatalog | null>(null);
  const [mappingEditor, setMappingEditor] = useState<PFMapping | null>(null);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let current = true;
    if (canEdit) {
      setLoading(true); setLoadError("");
      api.pfMappings().then(value => { if (current) setCatalog(value); })
        .catch(() => { if (current) setLoadError("Не удалось загрузить связи ГП. Обновите список, чтобы повторить."); })
        .finally(() => { if (current) setLoading(false); });
    }
    return () => { current = false; };
  }, [canEdit, revision]);
  const all = catalogEntries(products, canEdit ? catalog?.mappings || [] : []);
  const visible = all.flatMap(entry => {
    const caps = entry.product?.capabilities || [];
    if (lineFilter === "missing" && caps.length) return [];
    if (lineFilter && lineFilter !== "missing" && !caps.some(cap => cap.line_id === Number(lineFilter))) return [];
    const pfMatch = matchesCatalogText([entry.sku, entry.name, ...caps.map(cap => cap.line_name)].join(" "), query);
    const links = entry.links.filter(({ mapping }) => {
      if (gpLine && !(mapping.gp_line_id ? mapping.gp_line_id === Number(gpLine) :
        catalog?.products.find(p => p.sku === mapping.gp_sku)?.capabilities.some(cap => cap.line_id === Number(gpLine)))) return false;
      return pfMatch || matchesCatalogText(`${mapping.gp_sku} ${mapping.gp_name || ""}`, query);
    });
    return (gpLine || !pfMatch) && !links.length ? [] : [{ ...entry, links }];
  });
  const lineName = (id: number) => catalog?.lines.find(line => line.id === id)?.name || String(id);
  const saved = async () => {
    setEditor(null); setError(""); setRevision(n => n + 1);
    try { await onSaved("Параметры ПФ сохранены в справочнике"); }
    catch { setError("ПФ сохранён, но список не удалось обновить. Обновите страницу."); }
  };
  const editMapping = (mapping: PFMapping) => { setNotice(""); setMappingEditor(mapping); };
  const mappingSaved = (saved: PFMapping) => {
    setCatalog(current => current && ({ ...current, mappings: [...current.mappings.filter(m => m.gp_sku !== saved.gp_sku || m.gp_line_id !== saved.gp_line_id), saved] }));
    setMappingEditor(null); setNotice("Связи сохранены.");
    setRevision(n => n + 1);
  };
  return <section className="card catalog-card pf-catalog">
    <div className="catalog-head"><div><h2>ПФ</h2></div>
      <div className="filters"><input aria-label="Поиск ПФ" placeholder="Артикул, название ГП / ПФ или линия…" value={query} onChange={e => setQuery(e.target.value)} />
        <select aria-label="Фильтр линии ПФ" value={lineFilter} onChange={e => setLineFilter(e.target.value)}><option value="">Все линии ПФ</option><option value="missing">Без линии</option>{lines.filter(line => line.status === "active").map(line => <option key={line.id} value={line.id}>{line.workshop_name} · {line.name}</option>)}</select>
        {canEdit && <>
          <select aria-label="Фильтр линии ГП" value={gpLine} onChange={e => setGPLine(e.target.value)}><option value="">Все линии ГП</option>{catalog?.lines.filter(line => line.status === "active").map(line => <option key={line.id} value={line.id}>{line.workshop_name} · {line.name}</option>)}</select>
          <button className="button secondary" disabled={loading} onClick={() => setRevision(n => n + 1)}>Обновить связи</button>
          <button className="button secondary" disabled={!catalog || loading} onClick={() => editMapping(newPFMapping())}>Добавить сопоставление</button>
          <button className="button primary" onClick={() => setEditor({ product: null, capability: null })}>Создать ПФ</button>
        </>}
      </div>
    </div>
    {(error || (canEdit && loadError)) && <p className="inline-error" role="alert">{error || loadError}</p>}
    {notice && <p className="planner-message good" role="status">{notice}</p>}
    <div className="table-scroll integrated-table-scroll"><table><thead><tr><th>Артикул / ПФ</th><th>Цех / линия ПФ</th><th>Скорость, кг/ч</th><th>Статус</th>{canEdit && <><th>Связанные ГП</th><th>Настройка ПФ</th></>}</tr></thead>
      <tbody>{visible.map(({ key, sku, name, product, links }) => {
        const caps: (Capability | null)[] = product?.capabilities.length ? product.capabilities.filter(cap => !lineFilter || cap.line_id === Number(lineFilter)) : [null];
        return caps.map((cap, index) => <tr key={JSON.stringify([key, cap?.capability_id])}>
          <td><b>{sku}</b><small>{name}</small></td>
          <td>{cap ? <><b>{cap.workshop_name}</b><small>{cap.line_name}</small></> : <span className="bad">Линия не назначена</span>}</td>
          <td>{cap ? `${number(cap.speed_kg_hour)} кг/ч` : "—"}</td>
          <td>{product ? <span className={`status-chip ${product.active && product.catalog_status === "active" ? "good" : "warning"}`}>{product.active && product.catalog_status === "active" ? "Активный" : "Заблокирован"}</span> : "Только в связях"}</td>
          {canEdit && index === 0 && <td rowSpan={caps.length}>
            {links.map(({ mapping, item }) => <div className="pf-gp-link" key={JSON.stringify([mapping.gp_sku, mapping.gp_line_id])}>
              <button className="button secondary" disabled={loading} onClick={() => editMapping(mapping)}>{mapping.gp_sku} · {mapping.gp_name || "ГП"}</button>
              <small>{mapping.gp_line_id ? `Линия ГП: ${lineName(mapping.gp_line_id)}` : "Все линии ГП"}{item && ` · ${item.lead_days === 0 ? "День в день" : `За ${item.lead_days} дн.`}${item.line_id ? ` · ПФ: ${lineName(item.line_id)}` : ""}${item.speed_kg_hour ? ` · ${number(Number(item.speed_kg_hour))} кг/ч` : ""}${item.enabled ? "" : " · Отключено"}`}</small>
            </div>)}
            {sku && <button className="button secondary" disabled={!catalog || loading} onClick={() => editMapping({ ...newPFMapping(), items: [{ sku, name, kind: "pf", line_id: null, speed_kg_hour: null, lead_days: 2, enabled: true, note: "" }] })}>Связать с ГП</button>}
          </td>}
          {canEdit && <td>{product && <div className="inline-actions"><button className="button secondary" onClick={() => setEditor({ product, capability: cap })}>{cap ? "Изменить" : "Задать линию и скорость"}</button>{index === 0 && !!cap && <button className="button secondary" onClick={() => setEditor({ product, capability: null })}>Добавить линию</button>}</div>}</td>}
        </tr>);
      })}</tbody></table></div>
    {!visible.length && <p className="pf-catalog-empty">По выбранным условиям записей нет.</p>}
    <footer>Показано {visible.length} из {all.length} · Без линии: {all.filter(entry => entry.sku && !entry.product?.capabilities.length).length}</footer>
    {canEdit && editor && <PFEditor {...editor} products={products} lines={lines} onClose={() => setEditor(null)} onSaved={saved} />}
    {canEdit && catalog && mappingEditor && <PFMappingEditor data={catalog} initial={mappingEditor} onSaved={mappingSaved} onClose={() => setMappingEditor(null)} />}
  </section>;
}

function PFEditor({ product, capability, products, lines, onClose, onSaved }: Editor & {
  products: CatalogProduct[]; lines: LineData[]; onClose: () => void; onSaved: () => Promise<void>;
}) {
  const [sku, setSku] = useState(product?.sku || "");
  const [name, setName] = useState(product?.product_name || "");
  const [lineId, setLineId] = useState(String(capability?.line_id || ""));
  const [speed, setSpeed] = useState(String(capability?.speed_kg_hour || ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const duplicate = !product && products.some(row => row.sku.toLowerCase() === sku.trim().toLowerCase());
  const available = lines.filter(line => line.status === "active" && !product?.capabilities.some(cap => cap.line_id === line.id && cap.capability_id !== capability?.capability_id));
  const valid = isPFSku(sku) && sku.trim().length <= 80 && !!name.trim() && name.trim().length <= 200 && !duplicate
    && available.some(line => line.id === Number(lineId)) && Number.isFinite(Number(speed)) && Number(speed) > 0;
  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!valid || busy) return;
    setBusy(true); setError("");
    try {
      const values = { line_id: Number(lineId), units_per_hour: Number(speed) };
      if (product) await api.updateProduct(product.product_id, { ...values, ...(capability ? { capability_id: capability.capability_id } : {}) });
      else await api.createProduct({ ...values, sku: sku.trim(), product_name: name.trim(), state: "ПФ" });
      await onSaved();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); setBusy(false); }
  };
  return <div className="modal-backdrop"><section className="modal pf-catalog-editor" role="dialog" aria-modal="true" aria-labelledby="pf-editor-title">
    <header><div><h2 id="pf-editor-title">{product ? "Линия и скорость ПФ" : "Создать ПФ"}</h2><p>{product ? `${product.sku} · ${product.product_name}` : "Артикул должен начинаться на 102 или 4."}</p></div><button aria-label="Закрыть редактор ПФ" disabled={busy} onClick={onClose}>×</button></header>
    <form onSubmit={event => void save(event)}><fieldset disabled={busy}>
      {!product && <><label>Артикул ПФ<input autoFocus value={sku} maxLength={80} onChange={event => setSku(event.target.value)} required /></label><label>Название ПФ<input value={name} maxLength={200} onChange={event => setName(event.target.value)} required /></label>{sku && !isPFSku(sku) && <p className="inline-error">Артикул ПФ должен начинаться на 102 или 4.</p>}{duplicate && <p className="inline-error">Артикул уже есть в справочнике. Откройте его для редактирования.</p>}</>}
      <label>Линия ПФ<select aria-label="Линия ПФ" value={lineId} onChange={event => setLineId(event.target.value)} required><option value="">Выберите линию</option>{available.map(line => <option key={line.id} value={line.id}>{line.workshop_name} · {line.name}</option>)}</select></label>
      <label>Скорость ПФ, кг/ч<input type="number" min="0" step="any" value={speed} onChange={event => setSpeed(event.target.value)} required /></label>
      <p>Настройки сохраняются в общем справочнике. Скорость, отдельно заданная в связи ГП → ПФ или в задании, имеет приоритет.</p>
      {error && <p className="inline-error" role="alert">{error}</p>}
      <footer><button className="button secondary" type="button" onClick={onClose}>Отмена</button><button className="button primary" disabled={!valid} type="submit">{busy ? "Сохранение…" : "Сохранить"}</button></footer>
    </fieldset></form>
  </section></div>;
}
