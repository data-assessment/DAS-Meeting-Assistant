const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const serveDist = require('./serve-dist.cjs');

(async () => {
  const server = await serveDist();
  let browser;
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const page = await browser.newPage({ viewport: { width: 680, height: 900 } });
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const state = { health:'ok', storagePath:'C:/Test/Meeting-Notizen', enabled:true, active:true,
      currentId:null, autoStart:true, error:'', uiView:'settings', uiRequest:1,
      options:{managed:false, hasSpeechKey:true, hasChatKey:true, language:'de-DE', uiLanguage:'de'}, reviews:[] };
    const saved = [];
    let failSave = false;
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/notes') return route.fulfill({ json: state });
      if (url.pathname === '/api/notes/devices') return route.fulfill({ json:{ok:true, devices:[]} });
      if (url.pathname === '/api/settings' && route.request().method() === 'GET')
        return route.fulfill({ json:{ schema:[], values:{ STT_DICTIONARY:['decídalo'], COMPANY_CONTEXT:'Wir beraten.' } } });
      if (url.pathname === '/api/settings') {
        if (failSave) return route.fulfill({ json:{ok:false, error:'The company context supports at most 4000 characters.'} });
        saved.push(route.request().postDataJSON()); return route.fulfill({ json:{ok:true, changed:[]} });
      }
      return route.fulfill({ json:{ok:true} });
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/?view=notes&lang=de`);
    await page.getByRole('heading', {name:'Unternehmenskontext', exact:true}).waitFor();

    // Loaded values, editable even while a meeting runs (devices and access are locked then).
    const terms = page.getByLabel('Wichtige Begriffe', {exact:true});
    const context = page.getByLabel('Über das Unternehmen', {exact:true});
    await page.waitForFunction(() => !document.querySelector('.company-context textarea')?.disabled);
    assert.equal(await terms.inputValue(), 'decídalo');
    assert.equal(await context.inputValue(), 'Wir beraten.');
    assert.equal(await page.locator('fieldset').first().evaluate(element => element.disabled), true);

    await terms.fill('decídalo\n  Review-Agent \n\nData Assessment Solutions');
    const about = 'Data Assessment Solutions GmbH entwickelt decídalo.\n\nTeams: Plattform, Beratung.';
    await context.fill(about);
    await page.getByText(`${about.length} von 4000 Zeichen`, {exact:false}).waitFor();
    await page.getByRole('button', {name:'Kontext speichern', exact:true}).click();
    await page.getByRole('status').filter({hasText:'Kontext gespeichert'}).waitFor();
    assert.deepEqual(saved, [{ values: {
      STT_DICTIONARY: ['decídalo', 'Review-Agent', 'Data Assessment Solutions'],
      COMPANY_CONTEXT: about } }]);

    // A rejected save is shown, and nothing is lost from the fields.
    failSave = true;
    await page.getByRole('button', {name:'Kontext speichern', exact:true}).click();
    await page.getByRole('alert').filter({hasText:'at most 4000'}).waitFor();
    assert.equal(await terms.inputValue(), 'decídalo\n  Review-Agent \n\nData Assessment Solutions');
    assert.deepEqual(errors, []);
    console.log('Company context UI passed: loads saved values, editable during a meeting, saves trimmed terms and text, shows a rejected save.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exit(1); });
