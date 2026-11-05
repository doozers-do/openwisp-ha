/* Real Chromium navigation, API requests, cookie paths and WebSocket routing. */
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '/tmp/openwisp-browser/node_modules/playwright');

(async () => {
  const origin = process.argv[2];
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [], escaped = [], sockets = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.origin === origin && !url.pathname.startsWith('/api/hassio_ingress/')) escaped.push(url.pathname);
    });
    page.on('websocket', socket => sockets.push(socket.url()));
    for (const token of ['one', 'two']) {
      const prefix = '/api/hassio_ingress/' + token;
      const response = await page.goto(origin + prefix + '/admin/');
      assert.equal(response.status(), 200);
      assert.equal(response.headers()['x-frame-options'], 'SAMEORIGIN');
      assert.equal(response.headers()['strict-transport-security'], undefined);
      await page.waitForLoadState('networkidle');
      assert.match(await page.textContent('body'), /test-admin/);
      const cookies = await context.cookies(origin + prefix + '/admin/');
      assert(cookies.some(cookie => cookie.name === 'openwisp_ingress_session'));
      assert(cookies.some(cookie => cookie.name === 'openwisp_ingress_csrf'));
      assert(cookies.every(cookie => cookie.path === prefix + '/' && !cookie.secure));
      const api = await page.request.get(origin + prefix + '/api/v1/controller/device/');
      assert.equal(api.status(), 200);
      // Existing forms must keep CSRF protection and reverse their actions under
      // the prefix. A validation-error POST does not change application state.
      await page.goto(origin + prefix + '/admin/password_change/');
      await page.locator('input[type=submit]').click();
      assert(page.url().startsWith(origin + prefix + '/'));
      const rejected = await page.request.post(origin + prefix + '/admin/password_change/', {data: {}});
      assert.equal(rejected.status(), 403);
      await page.goto(origin + prefix + '/admin/config/device/');
      assert.equal((await page.request.get(origin + prefix + '/static/admin/css/base.css')).status(), 200);
      // The notifications connection must perform a real successful upgrade.
      const upgraded = await page.evaluate(prefix => new Promise(resolve => {
        const socket = new WebSocket('ws://' + location.host + prefix + '/ws/notification/');
        socket.onopen = () => { socket.close(); resolve(true); };
        socket.onerror = () => resolve(false);
        setTimeout(() => resolve(false), 10000);
      }), prefix);
      assert(upgraded, 'Notification WebSocket failed');
    }
    const prefix = '/api/hassio_ingress/two';
    await page.goto(origin + prefix + '/admin/geo/floorplan/add/');
    await page.locator('#id_location').fill('11111111-1111-4111-8111-111111111111');
    await page.locator('#id_floor').fill('0');
    await page.locator('#id_image').setInputFiles({name: 'floor.png', mimeType: 'image/png',
      buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8//8/AwMDEwMDAwMDAwAkBgMB/DXemwAAAABJRU5ErkJggg==', 'base64')});
    await page.locator('input[name=_continue]').first().click();
    await page.waitForLoadState('networkidle');
    assert.match(await page.textContent('body'), /successfully/);
    const media = await page.locator('a[href*="/media/floorplans/"]').first().getAttribute('href');
    assert(media.startsWith(prefix + '/media/'), media);
    assert.equal((await page.request.get(origin + media)).status(), 200);
    assert.equal((await page.request.get(origin + '/api/hassio_ingress/limited/admin/geo/floorplan/')).status(), 403);
    // Logging out clears this prefix's own session and CSRF cookies.
    const csrf = (await context.cookies(origin + prefix + '/admin/')).find(c => c.name === 'openwisp_ingress_csrf').value;
    const logout = await page.request.post(origin + prefix + '/admin/logout/', {
      form: {csrfmiddlewaretoken: csrf}, headers: {Referer: origin + prefix + '/admin/'}, maxRedirects: 0});
    assert(logout.status() < 400);
    assert(!(await context.cookies(origin + prefix + '/admin/')).some(c => c.name.startsWith('openwisp_ingress_')));
    for (const token of ['missing', 'denied']) {
      assert.equal((await page.request.get(origin + '/api/hassio_ingress/' + token + '/admin/')).status(), 403);
    }
    assert.deepEqual(escaped, []);
    assert.deepEqual(errors, []);
    assert(sockets.every(url => new URL(url).pathname.startsWith('/api/hassio_ingress/')));
    console.log('PASS: Chromium variable-prefix navigation, forms/CSRF, API, assets, upload/media, logout, scoped HTTP cookies, account permissions, identity denial and WebSockets');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
