// Run with PLAYWRIGHT_MODULE pointing to a Playwright installation.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const file = ".pf-import-table-" + process.pid + ".html";
const server = await createServer({ root, server: { host: "127.0.0.1", port: 0 } });
let browser;
try {
  await fs.writeFile(path.join(root, file), `<!doctype html><html lang="ru"><meta charset="UTF-8"><div id="root"></div><script type="module">
    import React from "react"; import {createRoot} from "react-dom/client";
    import {ImportPFReview} from "/src/ImportPFReview.tsx"; import "/src/styles.css";
    const candidates=Array.from({length:101},(_,i)=>({task_id:i+1,sku:"GP-"+(i+1),name:"Изделие",line_name:"ГП",production_date:"2026-11-27",shift:"day",quantity_kg:10,components:{"PF-1":"ПФ"}}));
    createRoot(document.getElementById("root")).render(React.createElement(ImportPFReview,{
      initial:{order_id:1,plan_id:2,version:3,candidates},fileName:"Проверка.xlsx",lines:[{id:1,name:"Слойка",workshop_name:"ПЦ"}],onDone:async(n)=>{document.title="created-"+n}
    }));</script></html>`);
  await server.listen();
  browser = await chromium.launch({ headless:true, ...(process.env.PLAYWRIGHT_CHANNEL ? {channel:process.env.PLAYWRIGHT_CHANNEL} : {}) });
  const page = await browser.newPage();
  let saved;
  let active = 0, peak = 0;
  const calls = new Map();
  const url = "/api/plans/2/imports/1/pf";
  await page.route("**" + url + "/**", async route => {
    const id = Number(route.request().url().split("/").pop());
    calls.set(id,(calls.get(id)||0)+1);
    active++; peak=Math.max(peak,active);
    await new Promise(resolve=>setTimeout(resolve,15));
    active--;
    if(id===1 || (id===2 && calls.get(id)===1)) return route.fulfill({status:422,json:{detail:id===1?"ПФ PF-1: в BOM нет положительной потребности":"ПФ PF-1 из связей ГП → ПФ отсутствует в расчёте BOM"}});
    return route.fulfill({json:{production_date:"2026-11-27",mapping_revision:"map",calculation_revision:"calc",items:[{sku:"PF-1",name:"ПФ",quantity_kg:"9.78",quantity_units:null,line_id:1,production_date:"2026-11-25",speed_kg_hour:50,errors:[]}]}});
  });
  await page.route("**"+url,async route=>{saved=route.request().postDataJSON(); return route.fulfill({json:{created:saved.length}});});
  await page.goto("http://127.0.0.1:"+server.httpServer.address().port+"/"+file);
  await page.getByLabel("Выбрать все задания ГП (101)").check();
  await page.getByText(/Проверьте норму расхода ПФ/).waitFor();
  await page.getByText(/Сверьте артикул ПФ/).waitFor();
  await page.getByRole("button",{name:"Создать задания ПФ (99)",exact:true}).waitFor();
  assert.ok(peak<=4);
  assert.ok(await page.getByRole("button",{name:"Создать задания ПФ (99)",exact:true}).isDisabled());
  await page.getByRole("row").filter({hasText:/Сверьте артикул ПФ/}).getByText("Обновить расчёт").click();
  await page.getByRole("button",{name:"Создать задания ПФ (100)"}).waitFor();
  await page.getByRole("button",{name:/Снять выбор с ГП с ошибкой расчёта/}).click();
  await page.getByRole("button",{name:"Далее"}).click();
  await page.getByLabel("Скорость ПФ, кг/ч").fill("75");
  await page.getByRole("button",{name:"Назад"}).click();
  await page.getByRole("button",{name:"Далее"}).click();
  assert.equal(await page.getByLabel("Скорость ПФ, кг/ч").inputValue(),"75");
  await page.getByRole("button",{name:"Создать задания ПФ (100)"}).click();
  await page.waitForFunction(()=>document.title==="created-100");
  assert.equal(saved.length,100); assert.ok(saved.every(s=>s.task_id!==1));
  assert.equal(saved.find(s=>s.task_id===101).pf_tasks[0].speed_kg_hour,75);
  console.log("Passed: 101 GPs, bounded previews, zero/missing BOM, retry, explicit deselection, paging, edits and submission.");
} finally {
  await browser?.close(); await server.close(); await fs.rm(path.join(root,file),{force:true});
}
