/*
 * Browser smoke test: terminal-only UI.
 * Usage: node tests/browser_smoke.cjs <control-url> <playwright-core-path> <view-url>
 */
const { chromium } = require(process.argv[3]);
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.argv[2]);
    await page.waitForFunction(() => document.getElementById('role').textContent.includes('控制权限'));
    if (await page.locator('#disconnect').isEnabled()) {
      await page.locator('#disconnect').click();
      await page.waitForFunction(() => document.getElementById('state').textContent.includes('未连接'));
    }

    // connect a loopback device
    await page.locator('#open-connect').click();
    await page.locator('#port').fill('loop://');
    await page.locator('#connect').click();
    await page.waitForFunction(() => !document.getElementById('send').disabled);
    assert.equal(await page.locator('#send-form').isVisible(), true, 'composer must stay visible');
    assert.equal(await page.locator('.tab').count(), 0, 'no log/hex/records tabs anymore');
    assert.equal(await page.locator('.inspector').count(), 0, 'no evidence panel anymore');

    // typing straight into the terminal
    await page.locator('#terminal').click();
    await page.keyboard.type('hello');
    await page.waitForFunction(() => document.getElementById('terminal').textContent.includes('hello'));
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.querySelectorAll('.term-line').length >= 2);

    // the send box still works and shows up in the terminal
    await page.locator('#payload').fill('from send box');
    await page.locator('#send').click();
    await page.waitForFunction(() => document.getElementById('terminal').textContent.includes('from send box'));

    // connecting remembers the parameters as a preset
    await page.waitForFunction(() => document.querySelectorAll('.preset-item').length > 0);

    // resizable sidebar
    const before = await page.locator('.sidebar').evaluate(node => node.getBoundingClientRect().width);
    const box = await page.locator('#drag-sidebar').boundingBox();
    await page.mouse.move(box.x + 3, box.y + 150);
    await page.mouse.down();
    await page.mouse.move(box.x + 103, box.y + 150, { steps: 5 });
    await page.mouse.up();
    const after = await page.locator('.sidebar').evaluate(node => node.getBoundingClientRect().width);
    assert.ok(after > before + 60, `sidebar should resize: ${before} -> ${after}`);
    assert.equal(await page.locator('#export').isDisabled(), false, 'export needs a session');

    // layout is remembered
    await page.reload();
    await page.waitForFunction(() => document.getElementById('role').textContent.includes('控制权限'));
    const reloaded = await page.locator('.sidebar').evaluate(node => node.getBoundingClientRect().width);
    assert.ok(Math.abs(reloaded - after) < 3, `layout not persisted: ${reloaded} vs ${after}`);

    // reconnect from the preset row
    await page.locator('#disconnect').click();
    await page.waitForFunction(() => document.getElementById('state').textContent.includes('未连接'));
    await page.locator('.preset-go').first().click();
    await page.waitForFunction(() => document.getElementById('state').textContent.includes('已连接'));

    // read-only viewer
    const viewer = await browser.newPage();
    await viewer.goto(process.argv[4]);
    await viewer.waitForFunction(() => document.getElementById('role').textContent.includes('只读'));
    assert.equal(await viewer.locator('#send').isDisabled(), true, 'read-only cannot send');
    assert.equal(await viewer.locator('#open-connect').isDisabled(), true, 'read-only cannot open a connection');
    await viewer.close();

    // narrow layout
    await page.setViewportSize({ width: 620, height: 820 });
    await page.waitForTimeout(300);
    assert.equal(await page.locator('#devices-toggle').isVisible(), true);
    await page.locator('#devices-toggle').click();
    assert.equal(await page.locator('.shell').evaluate(node => node.classList.contains('devices-open')), true);

    assert.deepEqual(errors, []);
    console.log('PASS: terminal typing, send box, preset, drag resize, export, persistence, read-only, mobile');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
