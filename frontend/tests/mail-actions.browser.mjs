import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const fixture = new URL("../.mail-actions-review.html", import.meta.url);
const server = await createServer({ root: fileURLToPath(new URL("../", import.meta.url)), server: { host: "127.0.0.1", port: 0 } });
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const action = {
    key: "manual_task_created", group: "planning", title: "Создание ручного задания", content: "Линия, дата и объём.",
    inherited: ["planner@example.com"], rule: { mode: "inherit", recipients: "" },
  };
  await page.route("**/api/admin/overview", route => route.fulfill({ json: {
    counts: {}, ldap: {}, email: { enabled: true }, csb: {}, lines: [], mail_actions: [action], recent_notifications: [],
  } }));
  await page.route("**/api/admin/mail-actions/manual_task_created", route => {
    const { previous, rule } = route.request().postDataJSON();
    assert.deepEqual(previous, action.rule);
    action.rule = { ...rule, recipients: rule.mode === "custom" ? rule.recipients : "" };
    return route.fulfill({ json: { rule: action.rule } });
  });
  await fs.writeFile(fixture, `
    <meta charset="UTF-8"><div id="root"></div><script type="module">
    import React from 'react';
    import { createRoot } from 'react-dom/client';
    import { AdminView } from '/src/App.tsx';
    createRoot(document.querySelector('#root')).render(React.createElement(AdminView, {onError: console.error}));
    </script>`);
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/.mail-actions-review.html`);
  await page.getByRole("button", { name: "Письма", exact: true }).click();
  await page.getByText("planner@example.com").waitFor();
  const mode = page.getByLabel("Правило — Создание ручного задания");
  await mode.selectOption("custom");
  await page.getByLabel("Получатели — Создание ручного задания").fill("owner@example.com");
  await page.getByRole("button", { name: "Сохранить правило" }).click();
  await page.getByText("Сохранено", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Журнал отправки" }).click();
  await page.getByRole("button", { name: "Рассылки по действиям" }).click();
  assert.equal(await mode.inputValue(), "custom");
  assert.equal(await page.getByLabel("Получатели — Создание ручного задания").inputValue(), "owner@example.com");
  await mode.selectOption("off");
  await page.getByRole("button", { name: "Сохранить правило" }).click();
  await page.getByText("Сохранено", { exact: true }).waitFor();
  assert.equal(action.rule.mode, "off");
  await page.getByLabel("Поиск").fill("нет совпадения");
  await page.getByText("Действий по этому запросу нет.").waitFor();
  assert.deepEqual(errors, []);
  console.log("Mail action rules: edit, save, reopen, disable, search — passed");
} finally {
  await browser?.close();
  await server.close();
  await fs.rm(fixture, { force: true });
}
