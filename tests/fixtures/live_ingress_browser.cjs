/* Read-only target check. Input is a private JSON file with origin/prefix/session. */
const fs = require('node:fs');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '/tmp/openwisp-browser/node_modules/playwright');

(async () => {
  const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext();
    await context.addCookies([{name: 'ingress_session', value: config.session,
      url: config.origin, httpOnly: true, sameSite: 'Lax'}]);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const base = config.origin + config.prefix;
    for (const path of ['/admin/', '/admin/config/device/', '/admin/config/device/add/', '/admin/geo/location/']) {
      const response = await page.goto(base + path);
      assert.equal(response.status(), 200);
      await page.waitForLoadState('networkidle');
      assert.equal(response.headers()['strict-transport-security'], undefined);
      assert.equal(response.headers()['x-frame-options'], 'SAMEORIGIN');
    }
    assert.equal((await page.request.get(base + '/api/v1/controller/device/')).status(), 200);
    const upgraded = await page.evaluate(prefix => new Promise(resolve => {
      const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const socket = new WebSocket(scheme + '//' + location.host + prefix + '/ws/notification/');
      socket.onopen = () => { socket.close(); resolve(true); };
      socket.onerror = () => resolve(false);
      setTimeout(() => resolve(false), 10000);
    }), config.prefix);
    assert(upgraded);
    assert.deepEqual(errors, []);
    const cookies = (await context.cookies(base + '/admin/')).filter(cookie => cookie.name.startsWith('openwisp_ingress_'));
    assert.equal(cookies.length, 2);
    assert(cookies.every(cookie => cookie.path === config.prefix + '/' && cookie.secure === config.origin.startsWith('https:')));
    console.log('PASS: real HA ingress, mapped identity, navigation/device form, API, cookies, no HSTS, same-origin framing and WebSocket');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error.message.replace(/\/api\/hassio_ingress\/[^/\s]+/g, '/api/hassio_ingress/[redacted]'));
  process.exit(1);
});
