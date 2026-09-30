const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const root = path.resolve(__dirname, '../dist');
  const server = http.createServer((req, res) => {
    const name = new URL(req.url, 'http://localhost').pathname;
    const file = path.join(root, name === '/' ? 'index.html' : name);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const page = await browser.newPage({ viewport: { width: 680, height: 800 } });
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const state = { health:'ok', storagePath:'C:/Test/Meeting-Notizen', enabled:true, active:false,
      currentId:null, autoStart:true, error:'', uiView:'history', uiRequest:1,
      options:{managed:true, setupComplete:false, onboardingComplete:false, hasSpeechKey:false, hasChatKey:false, language:'de-DE'}, reviews:[] };
    let attempts = 0, settings, releaseLogin, closes = 0, failSave = false;
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/notes') return route.fulfill({ json: state });
      if (url.pathname === '/api/notes/devices') return route.fulfill({ json:{ok:true, devices:[{name:'Headset',loopback:false}]} });
      if (url.pathname === '/api/notes/connect') {
        attempts++;
        if (attempts === 1) {
          await new Promise(resolve => { releaseLogin = resolve });
          return route.fulfill({json:{ok:false,error:'Der DAS-Sprachdienst ist noch nicht bereit.'}});
        }
        state.options.setupComplete = true;
        state.uiView = 'settings'; state.uiRequest++;
      }
      if (url.pathname === '/api/notes/finish-setup') {
        if (failSave) return route.fulfill({json:{ok:false,error:'Einstellungen konnten nicht gespeichert werden.'}});
        settings = route.request().postDataJSON();
        state.options.onboardingComplete = true;
        state.options.language = settings.language;
        state.autoStart = settings.autoStart;
        closes++;
      }
      if (url.pathname === '/api/notes/close') closes++;
      return route.fulfill({json:{ok:true}});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/?view=notes`);
    await page.getByRole('heading', {name:'Willkommen bei DAS Meeting Assistant', exact:true}).waitFor();
    assert.equal(await page.locator('input[type=password]').count(), 0);
    assert.equal(await page.getByLabel('Textmodell-Endpunkt', {exact:true}).count(), 0);
    assert.equal(await page.getByRole('button', {name:'Fertig', exact:true}).count(), 0);
    assert.equal(await page.getByRole('button', {name:'Alle Meetings', exact:true}).count(), 0);
    assert.equal(await page.locator('summary').count(), 0);
    fs.mkdirSync(path.resolve(__dirname, '../../reports'), {recursive:true});
    await page.screenshot({path:path.resolve(__dirname, '../../reports/managed-signin.png')});
    await page.getByRole('button', {name:'Mit Microsoft anmelden', exact:true}).click();
    await page.getByText('Anmeldung und DAS-Zugang werden geprüft …', {exact:true}).waitFor();
    assert.equal(await page.getByRole('button', {name:'Bitte warten …', exact:true}).isDisabled(), true);
    releaseLogin();
    await page.getByRole('alert').filter({hasText:'noch nicht bereit'}).waitFor();
    assert.equal(state.options.setupComplete, false);
    assert.equal(await page.getByRole('button', {name:'Fertig', exact:true}).count(), 0);
    await page.getByRole('button', {name:'Mit Microsoft anmelden', exact:true}).click();
    await page.getByRole('heading', {name:'Anmeldung erfolgreich', exact:true}).waitFor();
    assert.equal(await page.locator('details').getAttribute('open'), null);
    await page.screenshot({path:path.resolve(__dirname, '../../reports/managed-setup.png')});
    await page.setViewportSize({width:360,height:400});
    const finish = page.getByRole('button', {name:'Fertig', exact:true});
    const bounds = await finish.boundingBox();
    assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 400, 'Finish stays in the visible footer');
    await page.screenshot({path:path.resolve(__dirname, '../../reports/managed-setup-small.png')});
    await finish.click();
    await page.getByRole('heading', {name:'Einstellungen', exact:true}).waitFor();
    assert.equal(closes, 1);
    assert.deepEqual(settings, {enabled:true, language:'de-DE', mic:'', loopback:'', autoStart:true});
    await page.setViewportSize({width:680,height:800});
    await page.locator('summary').click();
    await page.getByLabel('Meeting-Sprache', {exact:true}).selectOption('en-US');
    await page.getByLabel('Mikrofon', {exact:true}).selectOption('Headset');
    await page.getByLabel('Bei Teams-Anrufen automatisch starten').uncheck();
    failSave = true;
    await finish.click();
    await page.getByRole('alert').filter({hasText:'nicht gespeichert'}).waitFor();
    assert.equal(closes, 1);
    failSave = false;
    await finish.click();
    await page.waitForFunction(() => !document.querySelector('[role=alert]'));
    assert.equal(closes, 2);
    assert.deepEqual(settings, {enabled:true, language:'en-US', mic:'Headset', loopback:'', autoStart:false});
    state.active = true; state.uiRequest++;
    await page.getByRole('button', {name:'Schließen', exact:true}).waitFor();
    assert.equal(await page.getByLabel('Meeting-Sprache', {exact:true}).isDisabled(), true);
    await page.getByRole('button', {name:'Schließen', exact:true}).click();
    await page.waitForFunction(() => !document.querySelector('button.primary:disabled'));
    assert.equal(closes, 3);
    state.active = false;
    state.options.managed = false; state.uiRequest++; state.uiView='settings';
    await page.getByRole('heading', {name:'Azure-Verbindungen', exact:true}).waitFor();
    assert.equal(await page.locator('input[type=password]').count(), 2);
    assert.deepEqual(errors, []);
    console.log('DAS setup UI: mandatory login, busy/failure/retry, optional settings, visible Finish at 360x400, save failure, close, active meeting and Community isolation passed.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode=1; });
