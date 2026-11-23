import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const fixture = path.join(root, ".pf-mapping-review.html");
const item = { sku: "4510001", name: "Круассан шоколадно-ореховый — полуфабрикат", kind: "pf", line_id: 1, speed_kg_hour: 109.04, lead_days: 2, note: "За 1–2 дня", enabled: true };
const data = {
  mappings: [{ gp_sku: "GP1", gp_name: "Круассаны с шоколадно-ореховой начинкой", gp_line_id: null, gp_speed_kg_hour: 27.26, revision: 0, origin: "source", items: [item] }],
  products: [{ sku: "GP1", name: "Круассаны с шоколадно-ореховой начинкой", product_id: 1, active: true, capabilities: [{ line_id: 1, speed_kg_hour: 100 }] }, { sku: "GP2", name: "Новый ГП", product_id: 2, active: true, capabilities: [{ line_id: 2, speed_kg_hour: 80 }] }, { sku: "4510001", name: item.name, product_id: 3, active: true, capabilities: [{ line_id: 1, speed_kg_hour: 109.04 }] }],
  lines: [{ id: 1, name: "Слойка", workshop_name: "ПЦ", status: "active" }, { id: 2, name: "Жареные блюда", workshop_name: "КЦ", status: "active" }],
};
let server, browser;
try {
  await fs.writeFile(fixture, `<!doctype html><html lang="ru"><head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head><body><div id="root" style="padding:24px;max-width:1200px;margin:auto"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client'; import {CatalogView} from '/src/App.tsx';
createRoot(document.getElementById('root')).render(React.createElement(CatalogView,{data:{products:[{product_id:3,sku:"4510001",product_name:${JSON.stringify(item.name)},line_names:["Слойка"],active:true,catalog_status:"active",capabilities:[{capability_id:7,line_id:1,line_name:"Слойка",workshop_name:"ПЦ",speed_kg_hour:100}]}],rows:[],unmapped_products:[],summary:{products:0,lines:0,capabilities:0,with_recipes:0}},lines:[],user:{role:'planner'},onFocusHandled:()=>{},onSaved:async()=>{}}));</script></body></html>`);
  server = await createServer({ root, configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0 } });
  await server.listen();
  browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/catalog/source-attributes", route => route.fulfill({ json: { values: {}, sources: { mercury: "ok", chz: "ok", details: "ok" }, checked_at: new Date().toISOString() } }));
  let submitted;
  const openMappings = async () => {
    await page.getByRole("navigation", { name: "Представление справочника" }).getByRole("button", { name: /^ПФ/ }).click();
    assert.equal(await page.locator(".pf-catalog table").count(), 1);
  };
  await page.route("**/api/planner-admin/pf-mappings", async route => {
    if (route.request().method() === "GET") return route.fulfill({ json: data });
    submitted = route.request().postDataJSON();
    const saved = { ...submitted, revision: submitted.revision + 1, origin: "saved", gp_name: data.products.find(row => row.sku === submitted.gp_sku).name };
    data.mappings = [...data.mappings.filter(row => row.gp_sku !== saved.gp_sku || row.gp_line_id !== saved.gp_line_id), saved];
    return route.fulfill({ json: saved });
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/.pf-mapping-review.html`);
  await openMappings();
  await page.locator("tbody tr").filter({hasText:"4510001"}).getByRole("button", { name: /^GP1 ·/ }).click();
  await page.getByRole("spinbutton", { name: "Срок ПФ 1", exact: true }).fill("4");
  await page.getByRole("spinbutton", { name: "Скорость ПФ 1", exact: true }).fill("150");
  await page.getByRole("button", { name: "Сохранить связи", exact: true }).click();
  await page.getByRole("status").waitFor();
  assert.equal(submitted.items[0].lead_days, 4);
  assert.equal(Number(submitted.items[0].speed_kg_hour), 150);
  assert.equal(submitted.revision, 0);
  assert.equal("catalog_speed_kg_hour" in submitted.items[0], false);
  await page.reload();
  await openMappings();
  await page.locator("tbody tr").filter({hasText:"4510001"}).getByRole("button", { name: /^GP1 ·/ }).click();
  assert.equal(await page.getByRole("spinbutton", { name: "Срок ПФ 1", exact: true }).inputValue(), "4");
  await page.getByRole("combobox", { name: "Линия ГП", exact: true }).selectOption("1");
  await page.getByRole("button", { name: "Сохранить связи", exact: true }).click();
  await page.getByRole("status").waitFor();
  assert.equal(submitted.gp_line_id, 1);
  assert.equal(submitted.revision, 0);
  await page.getByRole("button", { name: "Связать с ГП", exact: true }).click();
  await page.getByRole("combobox", { name: "ГП из справочника", exact: true }).pressSequentially("GP2");
  assert.equal(await page.getByRole("combobox", { name: "ГП из справочника", exact: true }).inputValue(), "GP2");
  assert.equal(await page.getByRole("combobox", { name: "Артикул ПФ 1", exact: true }).inputValue(), "4510001");
  await page.getByRole("combobox", { name: "Линия ПФ 1", exact: true }).selectOption("1");
  await page.getByRole("button", { name: "Сохранить связи", exact: true }).click();
  await page.getByRole("status").waitFor();
  assert.equal(submitted.gp_sku, "GP2");
  assert.equal(submitted.items[0].name, item.name);
  await page.getByRole("textbox", { name: "Поиск ПФ", exact: true }).fill("4510001");
  assert.equal(await page.locator("tbody tr").count(), 1);
  assert.equal(await page.locator("tbody .pf-gp-link").count(), 3);
  await page.getByLabel("Поиск ПФ").fill("GP2");
  assert.equal(await page.locator("tbody .pf-gp-link").count(), 1);
  await page.getByRole("button", { name: "Добавить сопоставление", exact: true }).click();
  assert.equal(await page.getByLabel("ГП из справочника", { exact: true }).inputValue(), "");
  await page.getByLabel("Закрыть редактор связей").click();
  if (process.env.PF_REVIEW_OUTPUT) {
    await fs.mkdir(process.env.PF_REVIEW_OUTPUT, { recursive: true });
    await page.screenshot({ path: path.join(process.env.PF_REVIEW_OUTPUT, "pf-mappings.png"), fullPage: true });
    await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; });
    await page.screenshot({ path: path.join(process.env.PF_REVIEW_OUTPUT, "pf-mappings-dark.png"), fullPage: true });
  }
  assert.deepEqual(errors, []);
  console.log("PF mappings: existing rows, edit/save/reload, line scope, new GP/PF and search passed");
} finally {
  await browser?.close(); await server?.close(); await fs.rm(fixture, { force: true });
}
