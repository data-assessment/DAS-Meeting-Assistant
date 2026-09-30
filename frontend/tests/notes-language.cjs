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
      currentId:null, autoStart:true, error:'', uiView:'meeting', uiRequest:1,
      options:{managed:false, hasSpeechKey:true, hasChatKey:true, language:'de-DE', uiLanguage:'de'}, reviews:[] };
    const saved = [];
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/notes') return route.fulfill({ json: state });
      if (url.pathname === '/api/notes/devices') return route.fulfill({ json:{ok:true, devices:[]} });
      if (url.pathname === '/api/notes/ui-language') {
        const { language } = route.request().postDataJSON();
        saved.push(language); state.options.uiLanguage = language;
      }
      return route.fulfill({ json:{ok:true} });
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/?view=notes`);
    await page.getByText('Bereit für Teams-Anrufe', {exact:true}).waitFor();

    // Dropdown: the current flag opens a menu with both languages named in their own language.
    const toggle = page.getByRole('button', {name:'App-Sprache: Deutsch', exact:true});
    assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
    await toggle.click();
    assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(await page.getByRole('menuitemradio', {name:'Deutsch'}).getAttribute('aria-checked'), 'true');
    await page.keyboard.press('Escape');
    assert.equal(await page.getByRole('menu').count(), 0);

    await toggle.click();
    await page.getByRole('menuitemradio', {name:'English'}).click();
    await page.getByText('Ready for Teams calls', {exact:true}).waitFor();
    assert.deepEqual(saved, ['en']);
    assert.equal(await page.evaluate(() => document.documentElement.lang), 'en');
    await page.getByRole('button', {name:'App language: English', exact:true}).waitFor();

    // The gear opens settings and is hidden there; the meeting language keeps its own setting.
    await page.getByRole('button', {name:'Settings', exact:true}).click();
    await page.getByRole('heading', {name:'Settings', exact:true}).waitFor();
    assert.equal(await page.getByRole('button', {name:'Settings', exact:true}).count(), 0);
    assert.equal(await page.getByLabel('Meeting language', {exact:true}).inputValue(), 'de-DE');
    assert.deepEqual(errors, []);
    console.log('Language UI passed: flag dropdown, Escape, switch to English with saved choice, settings gear, separate meeting language.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode=1; });
