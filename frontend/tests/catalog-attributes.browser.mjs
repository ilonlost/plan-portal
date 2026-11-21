import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const fixture = path.join(root, ".catalog-attributes-review.html");
const products = ["1010011409", "0002"].map((sku, i) => ({ product_id: i + 1, sku, product_name: `Продукция ${sku}`, active: true, catalog_status: "active", capabilities: [], line_names: [], capability_count: 0 }));
const data = { products, rows: products.map((p, i) => ({ ...p, capability_id: i + 1, speed_kg_hour: 100, workshop_name: "ПЦ", line_name: "Хлеба" })), unmapped_products: [], summary: { products: 2, capabilities: 2, lines: 1, with_recipes: 0 } };
let server, browser;
try {
  await fs.writeFile(fixture, `<!doctype html><html lang="ru"><head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head><body><header class="topbar"><div class="top-actions"><span id="catalog-status"></span></div></header><div id="root" style="padding:24px"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client'; import {CatalogView} from '/src/App.tsx';
const data = ${JSON.stringify(data)};
const root=createRoot(document.getElementById('root')); window.unmountCatalog=()=>root.unmount(); root.render(React.createElement(CatalogView,{data,lines:[],user:{role:'viewer'},focusProductId:null,onFocusHandled:()=>{},onCreateTask:async()=>{},onSaved:async()=>{}}));</script></body></html>`);
  server = await createServer({ root, configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, watch: { ignored: ["**/*"] } } });
  await server.listen();
  browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" });
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  await page.clock.install({ time: new Date("2026-11-18T09:00:00Z") });
  await page.clock.pauseAt(new Date("2026-11-18T09:00:00Z"));
  const errors = []; page.on("pageerror", e => errors.push(e.message));
  let count = 0;
  await page.route("**/api/**", async route => {
    if (!route.request().url().endsWith("/catalog/source-attributes")) return route.fulfill({ json: null });
    assert.equal(route.request().headers()["content-type"], "application/json");
    assert.deepEqual(route.request().postDataJSON().skus, ["0002", "1010011409"]);
    count++;
    await route.fulfill({ json: { values: {
      "1010011409": { mercury: count === 1, chz: true, shelf_life: "14", unit: "ШТ" },
      "0002": { mercury: false, chz: false, shelf_life: null, unit: null },
    }, sources: { mercury: "ok", chz: count > 1 ? "unavailable" : "ok", details: "ok" }, checked_at: new Date().toISOString() } });
  });
  await page.goto(`${server.resolvedUrls.local[0]}.catalog-attributes-review.html`);
  await page.locator(".topbar #catalog-status").getByText(/^Обновлено /).waitFor();
  assert.equal(await page.getByRole("button", { name: "Обновить данные CSB", exact: true }).count(), 0);
  const table = page.locator(".catalog-card table");
  for (const title of ["Меркурий", "ЧЗ", "Срок годности", "ЕИ"]) assert.equal(await table.getByRole("columnheader", { name: title, exact: true }).count(), 1);
  const filterIds = await table.locator("datalist").evaluateAll(nodes => nodes.map(node => node.id));
  assert.equal(new Set(filterIds).size, filterIds.length);
  let cells = await table.locator("tbody tr").first().locator("td").allTextContents();
  assert.deepEqual(cells.slice(-4), ["Да", "Да", "14", "ШТ"]);
  assert.deepEqual((await table.locator("tbody tr").nth(1).locator("td").allTextContents()).slice(-4), ["Нет", "Нет", "Нет данных", "Нет данных"]);
  await table.getByLabel("Фильтр: Меркурий", { exact: true }).fill("Да");
  assert.equal(await table.locator("tbody tr").count(), 1);
  await table.getByLabel("Фильтр: Меркурий", { exact: true }).fill("");
  await page.getByRole("navigation", { name: "Представление справочника" }).getByRole("button", { name: /Параметры SKU/ }).click();
  cells = await table.locator("tbody tr").first().locator("td").allTextContents();
  assert.deepEqual(cells.slice(-5, -1), ["Да", "Да", "14", "ШТ"]);
  await table.getByLabel("Фильтр: ЕИ", { exact: true }).fill("ШТ");
  assert.equal(await table.locator("tbody tr").count(), 1);
  await table.getByLabel("Фильтр: ЕИ", { exact: true }).fill("");
  assert.equal(count, 1);
  await page.clock.fastForward(599999);
  assert.equal(count, 1);
  await page.clock.fastForward(1);
  await table.getByRole("cell", { name: "Недоступно", exact: true }).first().waitFor();
  assert.deepEqual((await table.locator("tbody tr").first().locator("td").allTextContents()).slice(-5, -1), ["Нет", "Недоступно", "14", "ШТ"]);
  assert.equal(count, 2);
  const refreshed = page.waitForResponse(response => response.url().endsWith("/catalog/source-attributes"));
  await page.clock.fastForward(600000);
  await refreshed;
  assert.equal(count, 3);
  await page.evaluate(() => window.unmountCatalog());
  await page.clock.fastForward(1200000);
  assert.equal(count, 3);
  assert.deepEqual(errors, []);
  console.log("Catalog attributes: both views, flags, missing values, 10-minute polling, cleanup and partial failure passed");
} finally {
  await browser?.close(); await server?.close(); await fs.rm(fixture, { force: true });
}
