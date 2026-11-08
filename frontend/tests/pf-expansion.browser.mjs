// Run with a Playwright installation available via PLAYWRIGHT_MODULE.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const fixture = path.join(root, ".pf-review.html");
const output = process.env.PF_REVIEW_OUTPUT;
const lines = [
  { id: 1, name: "Слойка", workshop_code: "PC", workshop_name: "ПЦ", status: "active" },
  { id: 2, name: "НАЧИНКА", workshop_code: "PC", workshop_name: "ПЦ", status: "active" },
];
const markup = `<!doctype html><html lang="ru"><head><meta name="viewport" content="width=device-width, initial-scale=1.0"></head><body><div id="root"></div><script type="module">
import React from 'react'; import {createRoot} from 'react-dom/client';
import {ManualTaskModal} from '/src/App.tsx';
createRoot(document.getElementById('root')).render(React.createElement(ManualTaskModal, {
  planId:1, version:7, lines:${JSON.stringify(lines)}, initialDate:'2026-11-10', initialProductId:1, initialLineId:1,
  onClose:()=>{}, onSaved:async()=>{window.saved=true}, onError:()=>{}
}));</script></body></html>`;
let server;
let browser;
try {
  await fs.writeFile(fixture, markup);
  server = await createServer({ root, configFile: path.join(root, "vite.config.ts"), server: { host: "127.0.0.1", port: 0 } });
  await server.listen();
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  let submitted;
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/manual-products")) return route.fulfill({ json: [{ product_id: 1, sku: "1010027200", name: "Круассаны с шоколадно-ореховой начинкой", speed_kg_hour: 136.3 }] });
    if (url.pathname.endsWith("/pf-preview")) {
      const request = route.request().postDataJSON();
      return route.fulfill({ json: { production_date: request.production_date, quantity_kg: request.quantity, gp_speed_kg_hour: 27.26, gp_required_hours: 3.67, gp_speed_override: true,
        items: [
          { sku: "1021002013", name: "Круассан эконом шоколад-орех,80г — п/ф", line_id: 1, production_date: "2026-11-08", speed_kg_hour: 109.04, quantity_kg: request.quantity, date_defaulted: true, note: null, errors: [] },
          { sku: "1021001493", name: "Начинка шоколадно-ореховая п/ф", line_id: 2, production_date: "2026-11-05", speed_kg_hour: null, quantity_kg: request.quantity, date_defaulted: false, note: "Начинка за три дня до ДП ПФ круассан", errors: ["Укажите скорость ПФ в кг/ч"] },
        ] } });
    }
    if (url.pathname.endsWith("/items")) { submitted = route.request().postDataJSON(); return route.fulfill({ json: { id: 1 } }); }
    throw new Error(`Unexpected request: ${url.pathname}`);
  });
  const address = server.httpServer.address();
  await page.goto(`http://127.0.0.1:${address.port}/.pf-review.html`);
  await page.getByLabel("Задание, кг", { exact: true }).fill("100");
  await page.getByText("Начинка шоколадно-ореховая п/ф", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Создать задание", exact: true }).isDisabled(), true);
  await page.getByLabel("Скорость ПФ, кг/ч", { exact: true }).nth(1).fill("200");
  await page.waitForFunction(() => ![...document.querySelectorAll("button")].find(b => b.textContent === "Создать задание").disabled);
  await page.locator(".manual-plan-modal").evaluate(element => { element.scrollTop = 0; });
  if (output) { await fs.mkdir(output, { recursive: true }); await page.screenshot({ path: path.join(output, "pf-desktop.png") }); }
  await page.setViewportSize({ width: 390, height: 844 });
  if (output) await page.screenshot({ path: path.join(output, "pf-mobile.png") });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await page.getByRole("button", { name: "Создать задание", exact: true }).click();
  await page.waitForFunction(() => window.saved);
  assert.equal(submitted.expand_pf, true);
  assert.equal(submitted.pf_tasks.length, 2);
  assert.equal(submitted.pf_tasks[1].speed_kg_hour, 200);
  await page.evaluate(() => { window.saved = false; });
  await page.getByLabel("Раскручивать ПФ", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Создать задание", exact: true }).click();
  await page.waitForFunction(() => window.saved);
  assert.equal(submitted.expand_pf, false);
  assert.deepEqual(submitted.pf_tasks, []);
  assert.deepEqual(errors, []);
  console.log("PF form: preselection, missing speed, preview, creation, checkbox and mobile width passed");
} finally {
  await browser?.close();
  await server?.close();
  await fs.rm(fixture, { force: true });
}
