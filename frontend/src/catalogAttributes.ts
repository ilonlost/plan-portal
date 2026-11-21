import { useEffect, useState } from "react";
import { api } from "./api";

export type SourceAttributes = {
  mercury: boolean | null; chz: boolean | null; shelf_life: string | null; unit: string | null;
  shelf_life_conflict?: boolean; unit_conflict?: boolean;
};
export type SourceAttributesData = {
  values: Record<string, SourceAttributes>;
  sources: Record<"mercury" | "chz" | "details", "ok" | "not_configured" | "unavailable">;
  checked_at: string;
};
export const sourceColumns = [
  ["mercury", "Меркурий"], ["chz", "ЧЗ"], ["shelf_life", "Срок годности"], ["unit", "ЕИ"],
] as const;

export function useCatalogAttributes(skus: string[], refreshKey: unknown) {
  const skuKey = JSON.stringify([...new Set(skus)].sort());
  const [data, setData] = useState<Record<string, { value: SourceAttributes; sources: SourceAttributesData["sources"] }>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    setData({}); setCheckedAt(null);
    const load = async () => {
      if (inFlight || controller.signal.aborted) return;
      inFlight = true;
      setLoading(true); setError("");
      try {
        const keys: string[] = JSON.parse(skuKey);
        const next: typeof data = {};
        let latestCheck: string | null = null;
        for (let offset = 0; offset < keys.length; offset += 1000) {
          const result = await api.catalogAttributes(keys.slice(offset, offset + 1000), controller.signal);
          if (controller.signal.aborted) return;
          latestCheck = result.checked_at;
          for (const [sku, value] of Object.entries(result.values)) next[sku] = { value, sources: result.sources };
        }
        if (!controller.signal.aborted) { setData(next); setCheckedAt(latestCheck); }
      } catch {
        if (!controller.signal.aborted) {
          setData({});
          setError("Не удалось загрузить данные CSB. Следующая попытка — автоматически через 10 минут.");
        }
      } finally {
        inFlight = false;
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void load();
    const timer = window.setInterval(() => { void load(); }, 10 * 60 * 1000);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [skuKey, refreshKey]);
  const label = (sku: string, field: typeof sourceColumns[number][0]) => {
    const item = data[sku];
    if (!item) return loading ? "Загрузка…" : "Недоступно";
    const status = item.sources[field === "mercury" || field === "chz" ? field : "details"];
    if (status !== "ok") return status === "not_configured" ? "Не настроено" : "Недоступно";
    if ((field === "unit" || field === "shelf_life") && item.value[`${field}_conflict`]) return "Расхождение в источнике";
    const value = item.value[field];
    if (typeof value === "boolean") return value ? "Да" : "Нет";
    return value ?? "Нет данных";
  };
  const unavailable = !!error || Object.values(data).some(item => Object.values(item.sources).some(status => status !== "ok"));
  return { label, loading, error, data, checkedAt, unavailable };
}
