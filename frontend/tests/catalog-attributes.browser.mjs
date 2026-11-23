import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const fixture = path.join(root, ".catalog-attributes-review.html");
const products = ["1010011409", "0002"].map((sku, i) => ({ product_id: i + 1, sku, product_name: `Продукция ${sku}`, active: true, catalog_status: "active", state: "ОХЛ", advance_status: "По плану", units_per_box: 4, box_weight_kg: 1, capabilities: [], line_names: ["Хлеба"], capability_count: 1 }));
const blocked = { ...products[0], sku: "0001", product_id: 3, catalog_status: "blocked" };
const missingLine = { ...products[0], sku: "0003", product_id: 4, line_names: [], capability_count: 0 };
const data = { products: [blocked, products[1], missingLine, products[0]], rows: products.map((p, i) => ({ ...p, capability_id: i + 1, speed_kg_hour: 100, workshop_name: "ПЦ", line_name: "Хлеба" })), unmapped_products: [], summary: { products: 4, capabilities: 2, lines: 1, with_recipes: 0 } };
let server, browser;
try {
  await fs.writeFile(fixture, `<!doctype html><html lang="ru"><head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head><body><header class="topbar"><div class="top-actions"><span id="catalog-status"></span></div></header><div id="root" style="padding:24px"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client'; import {CatalogView} from '/src/App.tsx';
const data = ${JSON.stringify(data)};
const root=createRoot(document.getElementById('root')); window.unmountCatalog=()=>root.unmount(); window.renderRole=role=>root.render(React.createElement(CatalogView,{data,lines:[{id:4,name:'Хлеба',status:'active',workshop_name:'ПЦ'}],user:{role},focusProductId:null,onFocusHandled:()=>{},onCreateTask:async()=>{},onSaved:async()=>{}})); window.renderRole('viewer');</script></body></html>`);
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
    assert.deepEqual(route.request().postDataJSON().skus, ["0001", "0002", "0003", "1010011409"]);
    count++;
    await route.fulfill({ json: { values: {
      "1010011409": { mercury: count === 1, chz: true, shelf_life: "14", unit: "ШТ" },
      "0002": { mercury: false, chz: false, shelf_life: null, unit: null },
      "0001": { mercury: false, chz: false, shelf_life: "14", unit: "ШТ" },
      "0003": { mercury: false, chz: false, shelf_life: "14", unit: "ШТ" },
    }, sources: { mercury: "ok", chz: count > 1 ? "unavailable" : "ok", details: "ok" }, checked_at: new Date().toISOString() } });
  });
  await page.goto(`${server.resolvedUrls.local[0]}.catalog-attributes-review.html`);
  await page.locator(".topbar #catalog-status").getByText(/^Обновлено /).waitFor();
  assert.equal(await page.getByRole("button", { name: "Обновить данные CSB", exact: true }).count(), 0);
  const table = page.locator(".catalog-card table");
  const headers = await table.locator("th").allTextContents();
  assert.equal(headers[1], "Статус артикула");
  assert.equal(headers[headers.indexOf("Упаковка") + 1], "ЕИ");
  const statusFilter = table.getByLabel("Фильтр: Статус артикула", { exact: true });
  await statusFilter.fill("Заблокирован");
  assert.deepEqual(await table.locator("tbody tr td:nth-child(2)").allTextContents(), ["Заблокирован"]);
  await statusFilter.fill("");
  assert.deepEqual(await table.locator("tbody tr td:first-child b").allTextContents(), ["1010011409", "0002", "0003", "0001"]);
  const lineFilter = table.getByLabel("Фильтр: Линия", { exact: true });
  await lineFilter.fill("Хлеба");
  assert.deepEqual(await table.locator("tbody tr td:first-child b").allTextContents(), ["1010011409", "0002", "0001"]);
  await lineFilter.fill("");
  for (const title of ["Меркурий", "ЧЗ", "Срок годности", "ЕИ"]) assert.equal(await table.getByRole("columnheader", { name: title, exact: true }).count(), 1);
  const filterIds = await table.locator("datalist").evaluateAll(nodes => nodes.map(node => node.id));
  assert.equal(new Set(filterIds).size, filterIds.length);
  let cells = await table.locator("tbody tr").first().locator("td").allTextContents();
  assert.deepEqual(cells.slice(-4), ["ШТ", "Да", "Да", "14"]);
  assert.deepEqual((await table.locator("tbody tr").nth(1).locator("td").allTextContents()).slice(-4), ["Нет данных", "Нет", "Нет", "Нет данных"]);
  await table.getByLabel("Фильтр: Меркурий", { exact: true }).fill("Да");
  assert.equal(await table.locator("tbody tr").count(), 1);
  const bulkActions = page.getByRole("form", { name: "Массовые действия", exact: true });
  const tableTop = () => table.evaluate(node => node.getBoundingClientRect().top + window.scrollY);
  const topBeforeSelection = await tableTop();
  await page.getByRole("button", { name: "Выбрать", exact: true }).click();
  assert.equal(await bulkActions.count(), 0);
  await page.getByRole("button", { name: "Выбрать все", exact: true }).click();
  assert.equal(await page.getByText("Выбрано 1 из 1", { exact: true }).count(), 1);
  assert.equal(await page.getByRole("button", { name: "Скачать выбранные в XLSX" }).count(), 0);
  await page.evaluate(() => window.renderRole("planner"));
  await bulkActions.waitFor();
  assert.equal(await tableTop(), topBeforeSelection);
  assert.equal(await page.locator(".article-tools textarea").count(), 0);
  assert.equal(await page.locator(".article-tools").getByRole("button", { name: "Скачать выбранные в XLSX" }).count(), 1);
  const updates = [];
  await page.route("**/catalog/selection/update", async route => {
    updates.push(route.request().postDataJSON());
    await route.fulfill({ json: { changed: 1, plan_recalculated: false } });
  });
  await page.route("**/catalog/selection/export.xlsx", async route => {
    assert.deepEqual(route.request().postDataJSON(), { product_ids: [1] });
    await route.fulfill({ contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", body: "fixture" });
  });
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Скачать выбранные в XLSX" }).click();
  assert.equal((await download).suggestedFilename(), "Выбранные артикулы.xlsx");
  const action = page.getByLabel("Действие", { exact: true });
  const apply = page.getByRole("button", { name: "Применить (1)", exact: true });
  await action.selectOption("plan");
  page.once("dialog", dialog => dialog.dismiss());
  await apply.click();
  assert.equal(updates.length, 0);
  for (const [option, change] of [
    ["plan", { action: "set_date_status", advance_status: "По графику" }],
    ["az", { action: "set_date_status", advance_status: "АЗ" }],
    ["blocked", { action: "set_status", status: "blocked" }],
    ["line", { action: "add_line", line_id: 4, units_per_hour: 125 }],
  ]) {
    await action.selectOption(option);
    if (option === "line") {
      assert.equal(await tableTop(), topBeforeSelection);
      await page.getByLabel("Линия", { exact: true }).selectOption("4");
      await page.getByLabel("Скорость новых привязок, кг/ч").fill("125");
    }
    page.once("dialog", dialog => { assert.match(dialog.message(), /1 из 1/); return dialog.accept(); });
    const response = page.waitForResponse(res => res.url().endsWith("/catalog/selection/update"));
    await apply.click();
    await response;
    assert.deepEqual(updates.at(-1), { product_ids: [1], change });
  }
  await table.getByLabel("Фильтр: Меркурий", { exact: true }).fill("");
  assert.equal(await table.getByRole("checkbox").nth(1).isChecked(), false);
  await table.locator("tbody tr").nth(1).click();
  assert.equal(await table.getByRole("checkbox").nth(1).isChecked(), true);
  await table.getByRole("checkbox").first().uncheck();
  assert.equal(await table.getByLabel(`Выбрать артикул ${products[1].sku}`).isChecked(), true);
  const search = page.getByPlaceholder("Артикул, продукция или линия…");
  await search.fill(products[0].sku);
  assert.equal(await bulkActions.count(), 0);
  await page.getByRole("button", { name: "Выбрать все", exact: true }).click();
  await search.fill("");
  assert.equal(await table.getByRole("checkbox").nth(1).isChecked(), false);
  await page.getByRole("button", { name: "Отменить выбор" }).click();
  assert.equal(await table.getByRole("checkbox").count(), 0);
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
