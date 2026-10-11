// UI check with captured live read responses; never receives or prints an upstream token.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.argv[2] || 'playwright');
const root = path.resolve(__dirname, '..');
const dist = path.join(root, 'frontend', 'dist');
const readback = JSON.parse(fs.readFileSync(path.join(root, '.local', 'document-browser-readback.json'), 'utf8'));

async function main() {
  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    let file = path.resolve(dist, '.' + pathname);
    if (file !== dist && !file.startsWith(dist + path.sep)) { response.writeHead(403); response.end(); return; }
    if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(dist, 'index.html');
    const mime = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css' };
    response.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream');
    response.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true, ...(process.argv[3] ? { executablePath: process.argv[3] } : {}) });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    const queries = [];
    let forceFailure = false;
    let writeCount = 0;
    let customer = { Code: 1, Name: 'مشتری نمونه', Mobile: '09120000001', GroupName: 'test', Address: 'نشانی نمونه', CodeMeli: '0012345678' };
    let connectBody;
    let customerEditBody;
    let releaseWrite;
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      let body = {};
      let status = 200;
      if (url.pathname === '/api/session') body = { connected: false };
      if (url.pathname === '/api/connect' && route.request().method() === 'POST') {
        connectBody = route.request().postDataJSON();
        assert.deepEqual(connectBody, { password: 'kariz' });
        body = { connected: true };
      }
      if (url.pathname === '/api/customers') body = { items: [customer], from_code: 1, to_code: 250 };
      if (url.pathname === '/api/customers/1') body = { item: customer };
      if (url.pathname === '/api/capabilities') body = { items: [] };
      if (url.pathname === '/api/catalogue') body = { mapping: { products: [], source_gold_modes: [] }, source_catalogues: { groups: [{ Name: 'test' }], bank_accounts: [] } };
      if (url.pathname === '/api/operations' && route.request().method() === 'POST') {
        const operation = route.request().postDataJSON();
        if (operation.kind === 'customer_edit') {
          customerEditBody = operation;
          customer = { ...customer, Name: operation.payload.full_name, Mobile: operation.payload.mobile,
            Address: operation.payload.address, CodeMeli: operation.payload.national_id };
          body = { ...operation, status: 'verified', source_response: { customer } };
        } else {
          writeCount++;
          await new Promise(resolve => { releaseWrite = resolve; });
          body = { ...operation, status: 'uncertain', message: 'نتیجه نیازمند بررسی است' };
        }
      }
      if (url.pathname === '/api/documents') {
        queries.push(Object.fromEntries(url.searchParams));
        if (forceFailure) { status = 502; body = { detail: 'دریافت اسناد موقتاً ناموفق بود.' }; }
        else if (url.searchParams.get('search')) body = { ...readback.page1, items: [], total: 0, page: 1 };
        else if (url.searchParams.get('kind') === 'payment') body = readback.payments;
        else if (['delivery', 'trade', 'other'].includes(url.searchParams.get('kind'))) {
          const kind = url.searchParams.get('kind');
          body = { ...readback.page1, page: Number(url.searchParams.get('page') || 1),
            items: readback.page1.items.map(row => ({ ...row, category: kind })) };
        }
        else body = url.searchParams.get('page') === '2' ? readback.page2 : readback.page1;
      }
      await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.getByLabel('رمز عبور').fill('kariz');
    await page.getByRole('button', { name: 'ورود', exact: true }).click();
    await page.locator('.documents-page tbody tr').first().waitFor();
    assert.deepEqual(connectBody, { password: 'kariz' });
    await page.locator('.documents-page tbody tr').first().waitFor();
    assert.equal(await page.locator('.documents-page h1').innerText(), 'اسناد');
    assert.equal(await page.locator('.documents-page tbody tr').count(), 25);
    assert.equal(await page.getByLabel('کد مشتری (اختیاری)').isVisible(), false);
    assert.equal(queries[0].customer, undefined);
    assert.equal(queries[0].from_code, undefined);
    assert.equal(queries[0].to_code, undefined);
    await page.getByRole('button', { name: /جزئیات سند/ }).first().click();
    await page.locator('.modal').waitFor();
    await page.locator('.modal').getByRole('button', { name: '×', exact: true }).click();
    await page.getByRole('button', { name: 'بعدی', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.documents-page .pages').textContent.includes('صفحه ۲'));
    assert.ok(queries.some(query => query.page === '2'));
    await page.locator('.document-types').getByRole('button', { name: 'واریز و برداشت', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('.documents-page tbody .badge.payment').length === 25);
    await page.getByRole('searchbox').fill('نشانه آزمایش');
    await page.getByText('سندی مطابق این فیلترها پیدا نشد. فیلترها را پاک کنید.', { exact: true }).waitFor();
    await page.getByRole('searchbox').fill('');
    await page.locator('.documents-page tbody tr').first().waitFor();
    forceFailure = true;
    await page.getByRole('button', { name: '↻ تازه‌سازی', exact: true }).click();
    await page.getByRole('alert').waitFor();
    assert.equal(await page.locator('.documents-page tbody tr').count(), 25);
    forceFailure = false;
    await page.getByRole('button', { name: 'تلاش دوباره', exact: true }).click();
    await page.getByRole('alert').waitFor({ state: 'hidden' });
    await page.locator('.document-types').getByRole('button', { name: 'همه اسناد', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.document-types button.chosen').textContent === 'همه اسناد' && !document.querySelector('.notice.loading'));
    await page.screenshot({ path: path.join(root, '.local', 'documents-desktop.png'), fullPage: true });
    await page.locator('.sidebar nav').getByRole('button', { name: /مشتریان/ }).click();
    await page.getByRole('button', { name: 'دریافت بازه', exact: true }).click();
    await page.getByRole('button', { name: /مشاهده/ }).click();
    await page.getByRole('heading', { name: 'مشتری نمونه', exact: true }).waitFor();
    await page.getByRole('button', { name: 'ویرایش اطلاعات', exact: true }).click();
    const edit = page.getByRole('dialog', { name: 'ویرایش اطلاعات مشتری' });
    await edit.waitFor();
    assert.equal(await edit.getByLabel('نام کامل').inputValue(), 'مشتری نمونه');
    assert.equal(await edit.getByLabel('شماره تماس').inputValue(), '09120000001');
    assert.equal(await edit.getByLabel('نشانی').inputValue(), 'نشانی نمونه');
    assert.equal(await edit.getByLabel('کد ملی').inputValue(), '0012345678');
    assert.equal(await edit.getByLabel('کد ثابت').isDisabled(), true);
    await edit.getByLabel('نام کامل').fill('مشتری ویرایش‌شده');
    await edit.getByLabel('شماره تماس').fill('09129999999');
    await edit.getByLabel('نشانی').fill('نشانی جدید');
    await edit.getByLabel('کد ملی').fill('0098765432');
    page.once('dialog', confirmation => confirmation.accept());
    await edit.getByRole('button', { name: /ذخیره|ویرایش/ }).click();
    await page.getByRole('heading', { name: 'مشتری ویرایش‌شده', exact: true }).waitFor();
    assert.equal(customerEditBody.kind, 'customer_edit');
    assert.deepEqual(customerEditBody.payload, { customer_code: 1, full_name: 'مشتری ویرایش‌شده', group_name: 'test',
      mobile: '09129999999', address: 'نشانی جدید', national_id: '0098765432' });
    assert.equal(await page.getByRole('heading', { name: 'مشتری ویرایش‌شده', exact: true }).count(), 1);
    assert.equal(await page.locator('.page table tbody tr').filter({ hasText: 'مشتری ویرایش‌شده' }).count(), 1);
    await page.locator('.page .modal').getByRole('button', { name: '×', exact: true }).click();
    for (const [nav, kind, add] of [
      ['واریز و برداشت', 'payment', 'واریز یا برداشت جدید'],
      ['دریافت و تحویل کالا', 'delivery', 'دریافت یا تحویل جدید'],
      ['معاملات', 'trade', 'معامله جدید'],
      ['سایر اسناد', 'other', null],
    ]) {
      await page.locator('.sidebar nav').getByRole('button', { name: new RegExp(nav) }).click();
      await page.locator(`.documents-page tbody .badge.${kind}`).first().waitFor();
      assert.equal(await page.locator('.documents-page h1').innerText(), nav);
      assert.equal(await page.locator('.document-types').count(), 0);
      assert.equal(queries.at(-1).kind, kind);
      assert.equal(queries.at(-1).customer, undefined);
      assert.equal(await page.locator('.createcard').count(), 0);
      await page.locator('.document-filters summary').click();
      await page.getByRole('button', { name: 'پاک کردن فیلترها', exact: true }).click();
      await page.waitForTimeout(400);
      assert.equal(queries.at(-1).kind, kind);
      if (add) {
        await page.getByRole('button', { name: add, exact: true }).click();
        await page.getByRole('dialog').waitFor();
        assert.equal(await page.locator('.creation-modal .createcard').count(), 1);
        await page.keyboard.press('Escape');
        await page.getByRole('dialog').waitFor({ state: 'hidden' });
        assert.equal(await page.getByRole('button', { name: add, exact: true }).evaluate(el => el === document.activeElement), true);
      }
    }
    await page.locator('.sidebar nav').getByRole('button', { name: '▤ اسناد' }).click();
    await page.locator('.documents-page tbody tr').first().waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('.mobilehead').waitFor();
    assert.equal(await page.getByLabel('کد مشتری (اختیاری)').isVisible(), false);
    assert.equal(await page.locator('.documents-page tbody tr').first().locator('.document-amount').isVisible(), true);
    assert.ok(await page.locator('.documents-page table').evaluate(table => table.getBoundingClientRect().width <= 390));
    await page.locator('.documents-page tbody tr').first().click();
    await page.locator('.modal').waitFor();
    await page.locator('.modal').getByRole('button', { name: '×', exact: true }).click();
    await page.getByRole('button', { name: '☰', exact: true }).click();
    await page.locator('.sidebar.drawer').getByRole('button', { name: '▤ اسناد' }).click();
    await page.locator('.sidebar.drawer').waitFor({ state: 'hidden' });
    await page.screenshot({ path: path.join(root, '.local', 'documents-mobile.png'), fullPage: true });
    await page.getByRole('button', { name: '☰', exact: true }).click();
    await page.locator('.sidebar.drawer').getByRole('button', { name: /واریز و برداشت/ }).click();
    await page.locator('.documents-page tbody .badge.payment').first().waitFor();
    await page.getByRole('button', { name: 'واریز یا برداشت جدید', exact: true }).click();
    await page.getByRole('dialog').waitFor();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: path.join(root, '.local', 'creation-mobile.png'), fullPage: true });
    const dialog = page.getByRole('dialog');
    await dialog.locator('select').first().selectOption('1');
    await dialog.locator('input[inputmode="decimal"]').fill('1000');
    page.once('dialog', confirmation => confirmation.accept());
    await dialog.getByRole('button', { name: 'بررسی خلاصه و ثبت', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.creation-modal button[aria-label="بستن پنجره"]').disabled);
    await page.keyboard.press('Escape');
    assert.equal(await dialog.count(), 1);
    releaseWrite();
    await page.getByText('نتیجه نیازمند بررسی است', { exact: true }).first().waitFor();
    assert.equal(writeCount, 1);
    assert.equal(await dialog.getByRole('button', { name: 'بررسی خلاصه و ثبت', exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: 'بستن پنجره', exact: true }).click();
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ password_login: 'passed', customer_edit: 'passed', auto_load: true, visible_documents: 25, advanced_filters_collapsed: true,
      details: 'passed', pagination: 'passed', type_filter: 'passed', search_empty_state: 'passed',
      failure_keeps_previous_rows: true, category_pages: 'passed', creation_modals: 'passed',
      mobile_drawer: 'passed', pending_close_guard: 'passed', uncertain_not_resent: true, browser_errors: errors.length }));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
