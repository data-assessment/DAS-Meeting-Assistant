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
    const stored = { STT_DICTIONARY:['decídalo'], COMPANY_CONTEXT:'Wir beraten.' };
    let failSave = false;
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/notes') return route.fulfill({ json: state });
      if (url.pathname === '/api/notes/devices') return route.fulfill({ json:{ok:true, devices:[]} });
      if (url.pathname === '/api/settings' && route.request().method() === 'GET')
        return route.fulfill({ json:{ schema:[], values: stored } });
      if (url.pathname === '/api/settings') {
        if (failSave) return route.fulfill({ json:{ok:false, error:'Der Unternehmenskontext kann höchstens 4000 Zeichen lang sein.'} });
        const { values } = route.request().postDataJSON();
        saved.push(values);
        // Like the backend: case-insensitive duplicates are dropped.
        stored.STT_DICTIONARY = values.STT_DICTIONARY.filter((term, i, all) => all.findIndex(other => other.toLowerCase() === term.toLowerCase()) === i);
        stored.COMPANY_CONTEXT = values.COMPANY_CONTEXT;
        return route.fulfill({ json:{ok:true, changed:[]} });
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

    await terms.fill('decídalo\n  Review-Agent \n\nDecídalo\nData Assessment Solutions');
    const about = 'Data Assessment Solutions GmbH entwickelt decídalo.\n\nTeams: Plattform, Beratung.';
    await context.fill(about);
    await page.getByText(`${about.length} von 4000 Zeichen`, {exact:false}).waitFor();
    await page.getByText('auch mit den Zwischenständen während des Meetings', {exact:false}).waitFor();
    await page.getByText('ohne Komma, =, < oder >', {exact:false}).waitFor();
    await page.getByRole('button', {name:'Kontext speichern', exact:true}).click();
    await page.getByRole('status').filter({hasText:'Kontext gespeichert'}).waitFor();
    assert.deepEqual(saved, [{
      STT_DICTIONARY: ['decídalo', 'Review-Agent', 'Decídalo', 'Data Assessment Solutions'], COMPANY_CONTEXT: about }]);
    // The fields now show what was stored: the duplicate is gone.
    assert.equal(await terms.inputValue(), 'decídalo\nReview-Agent\nData Assessment Solutions');

    // A rejected save shows the (translated) reason and keeps what the user typed.
    failSave = true;
    await terms.fill('decídalo\nNeuer Begriff');
    await page.getByRole('button', {name:'Kontext speichern', exact:true}).click();
    await page.getByRole('alert').filter({hasText:'höchstens 4000 Zeichen'}).waitFor();
    assert.equal(await terms.inputValue(), 'decídalo\nNeuer Begriff');
    assert.deepEqual(errors, []);
    console.log('Company context UI passed: loads saved values, editable during a meeting, hints name limits and interim updates, saves and shows the stored values, keeps input on a rejected save.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exit(1); });
