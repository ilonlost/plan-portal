import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const fixture = path.join(root, ".pf-catalog-review.html");
const lines = [{ id: 1, name: "Слойка", workshop_code: "PC", workshop_name: "ПЦ", status: "active" }, { id: 2, name: "Начинка", workshop_code: "PC", workshop_name: "ПЦ", status: "active" }];
const cap = (id, line, speed) => ({ capability_id: id, line_id: line.id, line_name: line.name, workshop_code: line.workshop_code, workshop_name: line.workshop_name, speed_kg_hour: speed });
const product = (id, sku, caps = []) => ({ product_id: id, sku, product_name: `Продукция ${sku}`, state: null, active: true, catalog_status: "active", capabilities: caps, capability_count: caps.length, line_names: caps.map(cap => cap.line_name) });
let products = [product(1, "1010001"), product(2, "1020001", [cap(21, lines[0], 120), cap(22, lines[1], 50)]), product(3, "4510001"), product(4, "1020002"), product(5, "2102000")];
let server, browser;
try {
  await fs.writeFile(fixture, `<!doctype html><html lang="ru"><head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head><body><div id="root" style="padding:24px;max-width:1400px;margin:auto"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client'; import {CatalogView} from '/src/App.tsx';
function Test() { const [products,setProducts]=React.useState(${JSON.stringify(products)}); return React.createElement(CatalogView,{data:{products,rows:[],unmapped_products:[],summary:{products:products.length,capabilities:2,lines:2,with_recipes:0}},lines:${JSON.stringify(lines)},user:{role:new URLSearchParams(location.search).get('role')||'planner'},focusProductId:null,onFocusHandled:()=>{},onCreateTask:async()=>{},onSaved:async()=>setProducts(await fetch('/api/test-products').then(r=>r.json()))}); }
createRoot(document.getElementById('root')).render(React.createElement(Test));</script></body></html>`);
  server = await createServer({ root, configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0, watch: { ignored: ["**/*"] } } });
  await server.listen();
  browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" });
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  let submitted;
  await page.route("**/api/**", async route => {
    const req = route.request(); const pathname = new URL(req.url()).pathname;
    if (pathname.endsWith("/test-products")) return route.fulfill({ json: products });
    if (pathname.endsWith("/catalog/source-attributes")) return route.fulfill({ json: { values: {}, sources: { mercury: "not_configured", chz: "not_configured", details: "not_configured" }, checked_at: new Date().toISOString() } });
    if (req.method() === "PATCH") {
      submitted = req.postDataJSON(); const p = products.find(p => p.product_id === Number(pathname.split("/").pop()));
      const value = cap(submitted.capability_id || 31, lines.find(line => line.id === submitted.line_id), submitted.units_per_hour);
      p.capabilities = [...p.capabilities.filter(cap => cap.capability_id !== value.capability_id), value]; p.line_names = p.capabilities.map(cap => cap.line_name);
      return route.fulfill({ json: { ok: true } });
    }
    if (req.method() === "POST") { submitted = req.postDataJSON(); products.push({ ...product(6, submitted.sku, [cap(61, lines[0], submitted.units_per_hour)]), product_name: submitted.product_name }); return route.fulfill({ json: { ok: true } }); }
    return route.fulfill({ json: null });
  });
  const url = `${server.resolvedUrls.local[0]}.pf-catalog-review.html`;
  await page.goto(url);
  await page.getByRole("navigation", { name: "Представление справочника" }).getByRole("button", { name: /^ПФ 3$/ }).click();
  const table = page.locator(".pf-catalog table");
  assert.equal(await table.locator("tbody tr").count(), 4);
  assert.equal(await table.getByText("1010001", { exact: true }).count(), 0);
  assert.equal(await table.getByText("2102000", { exact: true }).count(), 0);
  await table.locator("tr").filter({ hasText: "4510001" }).getByRole("button").click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Линия ПФ", { exact: true }).selectOption("1");
  await dialog.getByLabel("Скорость ПФ, кг/ч").fill("0");
  assert.equal(await dialog.getByRole("button", { name: "Сохранить", exact: true }).isEnabled(), false);
  await dialog.getByLabel("Скорость ПФ, кг/ч").fill("75.5");
  await dialog.getByRole("button", { name: "Сохранить", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  assert.deepEqual(submitted, { line_id: 1, units_per_hour: 75.5 });
  await table.locator("tr").filter({ hasText: "1020001" }).filter({ hasText: "120 кг/ч" }).getByRole("button", { name: "Изменить", exact: true }).click();
  assert.equal(await dialog.getByLabel("Линия ПФ", { exact: true }).locator('option[value="2"]').count(), 0);
  await dialog.getByLabel("Скорость ПФ, кг/ч").fill("130");
  await dialog.getByRole("button", { name: "Сохранить", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  assert.deepEqual(submitted, { line_id: 1, units_per_hour: 130, capability_id: 21 });
  assert.equal(products[1].capabilities.find(cap => cap.capability_id === 22).speed_kg_hour, 50);
  await page.getByRole("button", { name: "Создать ПФ", exact: true }).click();
  await dialog.getByLabel("Артикул ПФ", { exact: true }).fill("1011234");
  await dialog.getByLabel("Название ПФ").fill("Новый полуфабрикат");
  await dialog.getByLabel("Линия ПФ", { exact: true }).selectOption("1");
  await dialog.getByLabel("Скорость ПФ, кг/ч").fill("90");
  assert.equal(await dialog.getByRole("button", { name: "Сохранить", exact: true }).isEnabled(), false);
  await dialog.getByLabel("Артикул ПФ", { exact: true }).fill("4021234");
  await dialog.getByRole("button", { name: "Сохранить", exact: true }).click();
  await dialog.waitFor({ state: "hidden" }); assert.equal(submitted.state, "ПФ");
  await page.getByLabel("Фильтр линии ПФ").selectOption("missing");
  assert.equal(await table.locator("tbody tr").count(), 1);
  await page.getByLabel("Фильтр линии ПФ").selectOption("");
  if (process.env.PF_REVIEW_OUTPUT) {
    await fs.mkdir(process.env.PF_REVIEW_OUTPUT, { recursive: true });
    await page.screenshot({ path: path.join(process.env.PF_REVIEW_OUTPUT, "pf-catalog.png"), fullPage: true });
    await page.evaluate(() => document.documentElement.dataset.theme = "dark");
    await page.screenshot({ path: path.join(process.env.PF_REVIEW_OUTPUT, "pf-catalog-dark.png"), fullPage: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Создать ПФ", exact: true }).click();
  assert.ok(await dialog.evaluate(el => el.getBoundingClientRect().width <= window.innerWidth));
  await page.goto(`${url}?role=viewer`);
  await page.getByRole("navigation", { name: "Представление справочника" }).getByRole("button", { name: /^ПФ 3$/ }).click();
  assert.equal(await page.locator(".pf-catalog button").count(), 0);
  assert.deepEqual(errors, []);
  console.log("PF catalog: prefixes, unmapped PF, line/speed save, multiple lines, creation, filters, viewer and mobile passed");
} finally { await browser?.close(); await server?.close(); await fs.unlink(fixture).catch(() => {}); }
