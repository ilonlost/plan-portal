import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const root = fileURLToPath(new URL("../", import.meta.url));
const server = await createServer({ root, server: { host: "127.0.0.1", port: 0 } });
let browser;
try {
  await server.listen();
  browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  let role = "planner";
  let access = { import: true, az: true, feedback: true };
  let historyReads = 0;
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    let json = [];
    if (path.endsWith("/session/me")) json = { username: "tester", display_name: "Тест", role, section_visibility: access };
    if (path.endsWith("/session/mode")) json = { auth_mode: "local" };
    if (path.endsWith("/matrix")) return route.fulfill({ status: 404, json: {} });
    if (path.endsWith("/lines")) json = [{ id: 1, status: "active", workshop_name: "ПЦ", name: "Слойка" }];
    if (path.endsWith("/advance-confirmations")) historyReads++;
    if (path.endsWith("/feedback")) json = { entries: [], events: [] };
    return route.fulfill({ json });
  });
  const sidebar = page.locator(".sidebar");
  const operations = page.getByRole("navigation", { name: "Операции с Excel" });
  const feedback = sidebar.getByRole("button", { name: "Обратная связь", exact: true });
  const excel = sidebar.getByRole("button", { name: /Загрузка Excel|Подтверждение АЗ/ });
  await page.goto(server.resolvedUrls.local[0]);
  await excel.click();
  assert.equal(await excel.count(), 1);
  await page.getByLabel("Область загрузки").selectOption("1");
  assert.equal(historyReads, 0);
  const history = page.waitForResponse(response => response.url().endsWith("/advance-confirmations"));
  await operations.getByRole("button", { name: "Подтверждение АЗ" }).click();
  await history;
  await page.getByRole("heading", { name: "Применённые подтверждения" }).waitFor();
  await operations.getByRole("button", { name: "Загрузка Excel" }).click();
  assert.equal(await page.getByLabel("Область загрузки").inputValue(), "1");
  assert.equal(await sidebar.locator("nav").getByText("Обратная связь").count(), 0);
  await feedback.click();
  await page.getByRole("heading", { name: "Отзывы и предложения" }).waitFor();
  assert.equal(await feedback.getAttribute("aria-current"), "page");
  for (const permitted of ["import", "az"]) {
    access = { import: permitted === "import", az: permitted === "az", feedback: false };
    await page.reload();
    await excel.click();
    await page.getByRole("heading", { name: permitted === "az" ? "Корректировка объёмов АЗ" : "Загрузить производственные данные" }).waitFor();
    assert.equal(await operations.count(), 0);
    assert.equal(await feedback.count(), 0);
  }
  role = "viewer";
  access = { import: true, az: true, feedback: true };
  await page.reload();
  await feedback.waitFor();
  assert.equal(await excel.count(), 0);
  assert.deepEqual(errors, []);
  console.log("Excel and AZ navigation, retained selection, section permissions and footer feedback passed");
} finally {
  await browser?.close();
  await server.close();
}
