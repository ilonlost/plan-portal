import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const fixture = path.join(root, '.attendance-permissions-review.html');
let server, browser;
const users = [
  { id: 1, username: 'admin', display_name: 'Администратор', role: 'admin', can_record_attendance: false },
  { id: 2, username: 'master', display_name: 'Мастер', role: 'master', can_record_attendance: true, line_name: 'Булка', workshop_code: '01' },
  { id: 3, username: 'service', display_name: 'Сервис', role: 'viewer', auth_provider: 'service', can_record_attendance: false },
].map(user => ({ active: true, section_permissions: {}, ...user }));
const writes = [];
try {
  await fs.writeFile(fixture, `<!doctype html><html lang="ru"><body><div id="root"></div><script type="module">
import React from 'react';import {createRoot} from 'react-dom/client';import {AdminView} from '/src/App.tsx';
window.errors=[];createRoot(document.getElementById('root')).render(React.createElement(AdminView,{onDeleted:async()=>{},onError:e=>window.errors.push(e)}));
</script></body></html>`);
  server = await createServer({ root, configFile: path.join(root, 'vite.config.ts'), server: { host: '127.0.0.1', port: 0 } });
  await server.listen();
  browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.route('**/api/**', async route => {
    const request = route.request(); const url = new URL(request.url());
    if (url.pathname.endsWith('/admin/overview')) return route.fulfill({ json: {
      counts: {}, ldap: {}, email: {}, csb: {}, users,
      lines: [{id: 1, name: 'Булка', workshop_code: '01', active: true}],
    } });
    assert.equal(request.method(), 'PATCH');
    assert.match(url.pathname, /\/admin\/users\/\d+$/);
    const id = Number(url.pathname.split('/').pop()); const body = request.postDataJSON();
    writes.push(body); Object.assign(users.find(user => user.id === id), body);
    return route.fulfill({ json: { ok: true } });
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/.attendance-permissions-review.html`);
  await page.getByRole('button', { name: 'Пользователи и права', exact: true }).click();
  const checkbox = page.getByRole('checkbox', { name: 'Ввод количества сотрудников', exact: true });
  assert.equal(await checkbox.isEnabled(), true);
  assert.equal(await checkbox.isChecked(), false);
  await checkbox.check();
  await page.getByRole('button', { name: 'Сохранить права', exact: true }).click();
  await page.waitForResponse(r => r.url().endsWith('/admin/overview'));
  assert.equal(writes.at(-1).can_record_attendance, true);
  assert.equal(await checkbox.isChecked(), true);
  await page.locator('.access-users button').filter({hasText: 'Мастер'}).click();
  assert.equal(await checkbox.isChecked(), true);
  await checkbox.uncheck();
  const saved = page.waitForResponse(r => r.url().endsWith('/admin/overview'));
  await page.getByRole('button', { name: 'Сохранить права', exact: true }).click(); await saved;
  assert.equal(writes.at(-1).can_record_attendance, false);
  await page.locator('.access-users button').filter({hasText: 'Администратор'}).click();
  assert.equal(await checkbox.isChecked(), true);
  await page.locator('.access-users button').filter({hasText: 'Сервис'}).click();
  assert.equal(await checkbox.isEnabled(), false);
  assert.equal(await checkbox.isChecked(), false);
  assert.deepEqual(errors, []); assert.deepEqual(await page.evaluate(() => window.errors), []);
  console.log('Attendance permission UI: admin grant persists, master revoke persists, service disabled; passed');
} finally {
  await browser?.close(); await server?.close(); await fs.rm(fixture, {force:true});
}
