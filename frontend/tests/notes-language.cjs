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
    let failSave = false, stalePolls = 0, holdState = false;
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/notes') {
        while (holdState) await new Promise(resolve => setTimeout(resolve, 50));
        // A poll that started before the save still carries the previous language.
        if (stalePolls > 0) { stalePolls--; return route.fulfill({ json: {...state, options: {...state.options, uiLanguage: 'en'}} }); }
        return route.fulfill({ json: state });
      }
      if (url.pathname === '/api/notes/devices') return route.fulfill({ json:{ok:true, devices:[]} });
      if (url.pathname === '/api/notes/ui-language') {
        if (failSave) return route.fulfill({ json:{ok:false, error:'The language could not be saved. Check the storage location and try again.'} });
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

    // A failed save is shown and the language stays as it was.
    failSave = true;
    await page.getByRole('button', {name:'App language: English', exact:true}).click();
    await page.getByRole('menuitemradio', {name:'Deutsch'}).click();
    await page.getByRole('alert').filter({hasText:'could not be saved'}).waitFor();
    await page.getByRole('heading', {name:'Settings', exact:true}).waitFor();
    assert.deepEqual(saved, ['en']);
    failSave = false;

    // A stale poll answering with the old language does not switch the UI back.
    // Record every heading text from the click on; the UI must never flip back after switching.
    await page.evaluate(() => {
      window.headings = [document.querySelector('h1')?.textContent];
      const record = () => { const h = document.querySelector('h1')?.textContent; if (h && h !== window.headings.at(-1)) window.headings.push(h) };
      new MutationObserver(record).observe(document.body, {subtree:true, childList:true, characterData:true});
    });
    stalePolls = 3;
    await page.getByRole('button', {name:'App language: English', exact:true}).click();
    await page.getByRole('menuitemradio', {name:'Deutsch'}).click();
    await page.getByRole('heading', {name:'Einstellungen', exact:true}).waitFor();
    assert.ok(stalePolls > 0, 'the choice must show while stale polls still answer with the old language');
    // Polls run every second; wait until both stale answers have been rendered.
    while (stalePolls > 0) await new Promise(resolve => setTimeout(resolve, 100));
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.deepEqual(await page.evaluate(() => window.headings), ['Settings', 'Einstellungen']);
    assert.equal(await page.getByRole('alert').count(), 0);
    assert.deepEqual(saved, ['en', 'de']);

    // The window URL carries the saved language, so the screen before the first poll matches it.
    holdState = true;
    await page.goto(`http://127.0.0.1:${server.address().port}/?view=notes&lang=en`);
    await page.getByText('Connecting to client …', {exact:true}).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.lang), 'en');
    holdState = false;

    // An English meeting keeps English section headings in its notes, also in the German UI.
    state.options.uiLanguage = 'de';
    state.reviews = [{ id:'m1', title:'Kickoff', language:'en', started:'2026-10-09T10:00:00', ended:'2026-10-09T11:00:00',
      status:'', error:'', warning:'', busy:false, canSummarize:false, sourceCharacters:0, savedPath:'m1.md', savedAt:'',
      storeError:'', revision:1, phase:'complete', editable:true, autoRetry:false, people:[], peopleNote:'',
      draft:{ summary:'Scope agreed.', decisions:'- Start the pilot.', openQuestions:'- Budget owner?', people:[], tasks:[] } }];
    await page.goto(`http://127.0.0.1:${server.address().port}/?view=notes&lang=de`);
    const editor = page.getByRole('textbox', {name:'Zusammenfassung direkt bearbeiten'});
    await editor.waitFor();
    assert.equal(await editor.inputValue(), 'Scope agreed.\n\nDecisions\n- Start the pilot.\n\nOpen questions\n- Budget owner?');
    assert.deepEqual(errors, []);
    console.log('Language UI passed: flag dropdown, Escape, switch to English with saved choice, settings gear, separate meeting language, failed save, stale poll, initial language, meeting-language notes headings.');
  } finally {
    holdState = false; // release a held state request, so a failed assertion cannot hang the close
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
// Exit explicitly: after a failure, a request still pending in the route handler must not keep node alive.
})().catch(error => { console.error(error); process.exit(1); });
