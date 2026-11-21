import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const fixture = path.join(root, ".inspection-review.html");
const output = process.env.INSPECTION_REVIEW_OUTPUT;
let server, browser;
const auditRow = id => ({ id, username: "codex:authorized-catalog-repair", action: "catalog_analogue_applied", entity_type: "product", entity_id: "588", created_at: "2026-11-11T10:00:00Z", details: { sku: "1010035914", analogue_sku: "1010025512", before: { capabilities: [] }, after: { line_name: "Хлеба", units_per_hour: "597.60" } } });
const columns = ["id", "sku", "name", "active"].map(name => ({ name, type: name === "id" ? "INTEGER" : "VARCHAR" }));
try {
  await fs.writeFile(fixture, `<!doctype html><html lang="ru"><head><meta name="viewport" content="width=device-width,initial-scale=1.0"></head><body style="padding:24px"><div id="root"></div><script type="module">
import React from 'react';import {createRoot} from 'react-dom/client';import {AdminView} from '/src/App.tsx';import {CatalogIssues} from '/src/ProductionImport.tsx';
createRoot(document.getElementById('root')).render(React.createElement(AdminView,{onDeleted:async()=>{},onError:e=>{window.errors.push(e)}}));window.errors=[];
window.showIssues=()=>createRoot(document.body.appendChild(document.createElement('div'))).render(React.createElement(CatalogIssues,{issues:[{sku:'1010035914',product_name:'Хлеб тыквенный',line_hint:'Хлеба',row_numbers:[2,3],reason:'не задана положительная скорость (кг/ч) на линиях: Хлеба'}]}));
</script></body></html>`);
  server = await createServer({ root, configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0 } }); await server.listen();
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = []; page.on("pageerror", e => errors.push(e.message));
  let auditQuery, databaseQuery, failDatabase = false;
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    assert.equal(route.request().method(), "GET");
    if (url.pathname.endsWith("/admin/overview")) return route.fulfill({ json: { counts: { plans: 1, schedule_items: 1007, products: 630, lines: 16 }, active_plan: { name: "Комплексный план ФК" }, ldap: { configured: true, status: "Подключено" }, email: { enabled: true, host: "Корпоративная почта" }, csb: { test_mode: true } } });
    if (url.pathname.endsWith("/audit-log/filters")) return route.fulfill({ json: { actions: ["catalog_analogue_applied"], users: ["codex:authorized-catalog-repair"], entities: ["product"] } });
    if (url.pathname.endsWith("/audit-log")) {
      auditQuery = url.searchParams;
      const offset = Number(url.searchParams.get("offset")); const missing = url.searchParams.get("q") === "missing";
      return route.fulfill({ json: { total: missing ? 0 : 51, offset, limit: 50, rows: missing ? [] : Array.from({ length: offset ? 1 : 50 }, (_, i) => auditRow(100 - offset - i)) } });
    }
    if (url.pathname.endsWith("/database")) return route.fulfill({ json: { read_only: true, tables: [{ name: "products", label: "Артикулы", columns }, { name: "line_capabilities", label: "Артикулы и мощности линий", columns: [{ name: "id", type: "INTEGER" }, { name: "product_id", type: "INTEGER" }, { name: "units_per_hour", type: "NUMERIC" }] }] } });
    if (url.pathname.includes("/database/")) {
      databaseQuery = url.searchParams;
      if (failDatabase) return route.fulfill({ status: 403, json: { detail: "Требуются права администратора" } });
      return route.fulfill({ json: { read_only: true, total: 1, offset: 0, limit: 50, rows: url.pathname.endsWith("products") ? [{ id: 588, sku: "1010035914", name: "Хлеб тыквенный зам 300г*10", active: true }] : [{ id: 784, product_id: 588, units_per_hour: "597.60" }] } });
    }
    throw new Error(`Unexpected URL: ${url}`);
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/.inspection-review.html`);
  await page.getByRole("button", { name: "Журнал изменений", exact: true }).click();
  await page.getByText("1–50 из 51", { exact: true }).waitFor();
  await page.getByText("Показать изменения", { exact: true }).first().click();
  assert.match(await page.locator("details[open] pre").innerText(), /597.60/);
  await page.getByLabel("Поиск", { exact: true }).fill("1010035914");
  await Promise.all([page.waitForResponse(r => r.url().includes("audit-log?") && r.url().includes("q=1010035914")), page.getByRole("button", { name: "Найти", exact: true }).click()]);
  assert.equal(auditQuery.get("q"), "1010035914");
  await page.getByRole("button", { name: "Далее", exact: true }).click(); await page.getByText("51–51 из 51").waitFor();
  await page.getByLabel("Поиск", { exact: true }).fill("missing"); await page.getByRole("button", { name: "Найти", exact: true }).click();
  await page.getByText("По выбранным условиям изменений не найдено.").waitFor(); assert.equal(auditQuery.get("offset"), "0");
  await page.getByRole("button", { name: "Сбросить", exact: true }).click(); await page.getByText("1–50 из 51").waitFor();
  await page.getByText("Показать изменения", { exact: true }).first().click();
  if (output) { await fs.mkdir(output, { recursive: true }); await page.screenshot({ path: path.join(output, "audit-desktop.png") }); }
  await page.getByRole("button", { name: "База данных", exact: true }).click();
  await page.getByRole("button", { name: "Открыть #588" }).click(); await page.getByRole("heading", { name: "Запись № 588" }).waitFor();
  await page.getByLabel("Таблица", { exact: true }).selectOption("line_capabilities");
  await page.getByRole("button", { name: "Открыть #784" }).waitFor();
  await page.getByLabel("Точное условие: столбец").selectOption("product_id"); await page.getByLabel("Значение", { exact: true }).fill("588");
  await Promise.all([page.waitForResponse(r => r.url().includes("column=product_id")), page.getByRole("button", { name: "Найти", exact: true }).click()]); assert.equal(databaseQuery.get("value"), "588");
  await Promise.all([page.waitForResponse(r => r.url().includes("sort=units_per_hour")), page.getByRole("button", { name: "units_per_hour", exact: true }).click()]);
  await page.getByRole("button", { name: "Открыть #784" }).click();
  if (output) await page.screenshot({ path: path.join(output, "database-desktop.png") });
  await page.evaluate(() => document.documentElement.dataset.theme = "dark");
  if (output) await page.screenshot({ path: path.join(output, "database-dark.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  // Isolate component responsiveness from the existing portal-wide 820px body minimum.
  await page.addStyleTag({content: "body {min-width:0}"});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  if (output) await page.screenshot({ path: path.join(output, "database-mobile.png") });
  failDatabase = true; await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Требуются права администратора" }).waitFor();
  await page.evaluate(() => window.showIssues());
  await page.getByRole("heading", { name: "Нужно заполнить справочник: 1 позиций" }).waitFor();
  assert.match(await page.locator(".import-catalog-issues").innerText(), /1010035914/);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  assert.deepEqual(errors, []); assert.deepEqual(await page.evaluate(() => window.errors), []);
  console.log("Admin navigation, audit filtering/details/pagination, database browsing/sort/filter/errors, catalog issues and mobile layout passed");
} finally {
  await browser?.close(); await server?.close(); await fs.rm(fixture, { force: true });
}
