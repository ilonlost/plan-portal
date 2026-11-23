import { useState } from "react";
import { api } from "./api";
import { matchesCatalogText } from "./catalogSearch";
import type { CatalogProduct, LineData } from "./types";
import "./PFCatalog.css";

export const isPFSku = (sku: string) => /^(102|4)/.test(sku.trim());
type Capability = CatalogProduct["capabilities"][number];
type Editor = { product: CatalogProduct | null; capability: Capability | null };
const number = (value: number) => Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 3 });

export function PFCatalog({ products, lines, canEdit, onSaved }: {
  products: CatalogProduct[]; lines: LineData[]; canEdit: boolean; onSaved: (text: string) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [lineFilter, setLineFilter] = useState("");
  const [editor, setEditor] = useState<Editor | null>(null);
  const [error, setError] = useState("");
  const all = products.filter(product => isPFSku(product.sku) && product.catalog_status !== "deleted");
  const visible = all.filter(product => matchesCatalogText(`${product.sku} ${product.product_name} ${product.line_names.join(" ")}`, query)
    && (!lineFilter || (lineFilter === "missing" ? !product.capabilities.length : product.capabilities.some(cap => cap.line_id === Number(lineFilter)))))
    .sort((a, b) => Number(a.capabilities.length === 0) - Number(b.capabilities.length === 0));
  const saved = async () => {
    setEditor(null);
    setError("");
    try { await onSaved("Параметры ПФ сохранены в справочнике"); }
    catch { setError("ПФ сохранён, но список не удалось обновить. Обновите страницу."); }
  };
  return <section className="card catalog-card pf-catalog">
    <div className="catalog-head"><div><h2>ПФ</h2><p>Артикула ПФ для раскрутки</p></div>
      <div className="filters"><input aria-label="Поиск ПФ" placeholder="Артикул, название или линия…" value={query} onChange={event => setQuery(event.target.value)} />
        <select aria-label="Фильтр линии ПФ" value={lineFilter} onChange={event => setLineFilter(event.target.value)}><option value="">Все линии</option><option value="missing">Без линии</option>{lines.filter(line => line.status === "active").map(line => <option key={line.id} value={line.id}>{line.workshop_name} · {line.name}</option>)}</select>
        {canEdit && <button className="button primary" onClick={() => setEditor({ product: null, capability: null })}>Создать ПФ</button>}
      </div>
    </div>
    {error && <p className="inline-error" role="alert">{error}</p>}
    <div className="table-scroll integrated-table-scroll"><table><thead><tr><th>Артикул / ПФ</th><th>Цех / линия</th><th>Скорость, кг/ч</th><th>Статус</th>{canEdit && <th>Настройка</th>}</tr></thead>
      <tbody>{visible.map(product => {
        const caps: (Capability | null)[] = product.capabilities.length ? product.capabilities.filter(cap => !lineFilter || lineFilter === "missing" || cap.line_id === Number(lineFilter)) : [null];
        return caps.map((cap, index) => <tr key={`${product.product_id}-${cap?.capability_id || "missing"}`}>
          <td><b>{product.sku}</b><small>{product.product_name}</small></td>
          <td>{cap ? <><b>{cap.workshop_name}</b><small>{cap.line_name}</small></> : <span className="bad">Линия не назначена</span>}</td>
          <td>{cap ? `${number(cap.speed_kg_hour)} кг/ч` : "Не задана"}</td>
          <td><span className={`status-chip ${product.active && product.catalog_status === "active" ? "good" : "warning"}`}>{product.active && product.catalog_status === "active" ? "Активный" : "Заблокирован"}</span></td>
          {canEdit && <td><div className="inline-actions"><button className="button secondary" onClick={() => setEditor({ product, capability: cap })}>{cap ? "Изменить" : "Задать линию и скорость"}</button>{index === 0 && !!cap && <button className="button secondary" onClick={() => setEditor({ product, capability: null })}>Добавить линию</button>}</div></td>}
        </tr>);
      })}</tbody></table></div>
    {!visible.length && <p className="pf-catalog-empty">{all.length ? "По выбранным фильтрам ПФ не найдены." : "В справочнике пока нет артикулов ПФ с префиксом 102 или 4."}</p>}
    <footer>Показано {visible.length} из {all.length} ПФ · Без линии: {all.filter(product => !product.capabilities.length).length}</footer>
    {canEdit && editor && <PFEditor {...editor} products={products} lines={lines} onClose={() => setEditor(null)} onSaved={saved} />}
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
