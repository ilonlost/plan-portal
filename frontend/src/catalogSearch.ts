export function normalizeCatalogSearch(value: unknown): string {
  return String(value ?? "").normalize("NFKC")
    .replace(/[\u200B-\u200D\u2060\uFEFF]/g, "")
    .trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

export function matchesCatalogText(value: unknown, filter: unknown): boolean {
  const needle = normalizeCatalogSearch(filter);
  return !needle || normalizeCatalogSearch(value).includes(needle);
}
