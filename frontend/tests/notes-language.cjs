const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const serveDist = require('./serve-dist.cjs');

(async () => {
  const server = await serveDist();
  let browser;
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const page = await browser.newPage({ viewport: { width: 680, height: 800 } });
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const state = { health:'ok', storagePath:'C:/Test/Meeting-Notizen', enabled:true, active:false,
      currentId:null, autoStart:true, error:'', uiView:'meeting', uiRequest:1,
      options:{managed:false, hasSpeechKey:true, hasChatKey:true, language:'de-DE', uiLanguage:'de'}, reviews:[] };
    const saved = [];
    let failSave = false, stalePolls = 0, holdState = false, saveDelay = 0;
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
        if (saveDelay) await new Promise(resolve => setTimeout(resolve, saveDelay));
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
    const alert = page.getByRole('alert').filter({hasText:'could not be saved'});
    await alert.waitFor();
    await page.getByRole('heading', {name:'Settings', exact:true}).waitFor();
    // Shown inside the top bar, not over the content below it.
    const bar = await page.locator('nav.notes-navigation').boundingBox(), box = await alert.boundingBox();
    assert.ok(box.y >= bar.y && box.y + box.height <= bar.y + bar.height + 1, 'error stays inside the bar');
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

    // A choice switches the whole window at once, while the save is still running.
    saveDelay = 1500;
    await page.getByRole('button', {name:'App-Sprache: Deutsch', exact:true}).click();
    await page.getByRole('menuitemradio', {name:'English'}).click();
    await page.getByRole('heading', {name:'Summary', exact:true}).waitFor({timeout: 700});
    assert.deepEqual(saved, ['en', 'de'], 'the save is still pending');
    await page.waitForFunction(() => document.querySelector('.language-toggle')?.getAttribute('aria-disabled') === 'false');
    assert.deepEqual(saved, ['en', 'de', 'en']);
    saveDelay = 0;

    // Keyboard: Enter opens with the checked item focused, arrows move, Escape returns to the toggle,
    // Tab leaves and closes, choosing keeps focus on the toggle.
    const englishToggle = page.getByRole('button', {name:'App language: English', exact:true});
    const focused = () => page.evaluate(() => document.activeElement?.textContent || document.activeElement?.getAttribute('aria-label'));
    await englishToggle.focus();
    await page.keyboard.press('Enter');
    assert.equal(await focused(), 'English');
    await page.keyboard.press('ArrowDown');
    assert.equal(await focused(), 'Deutsch');
    await page.keyboard.press('Escape');
    assert.equal(await page.getByRole('menu').count(), 0);
    assert.equal(await focused(), 'App language: English');
    await page.keyboard.press('ArrowDown');
    assert.equal(await page.getByRole('menu').count(), 1);
    await page.keyboard.press('Tab');
    assert.equal(await page.getByRole('menu').count(), 0, 'tabbing out closes the menu');
    await englishToggle.focus();
    await page.keyboard.press('Enter');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.getByRole('menu').count(), 0, 'Shift+Tab back onto the toggle closes the menu');
    await englishToggle.focus();
    await page.keyboard.press('Enter');
    await page.keyboard.press('Home');
    assert.equal(await focused(), 'Deutsch');
    await page.keyboard.press('Enter');
    await page.getByRole('button', {name:'App-Sprache: Deutsch', exact:true}).waitFor();
    assert.equal(await focused(), 'App-Sprache: Deutsch');

    // Settings opened with the gear return to where they came from, including a meeting picked in the history.
    state.reviews = [{ ...state.reviews[0], id:'m0', title:'Wochenrunde', language:'de' }, state.reviews[0]];
    await page.getByRole('button', {name:'Alle Meetings', exact:true}).click();
    await page.getByRole('heading', {name:'Alle Meetings', exact:true}).waitFor();
    await page.getByRole('button', {name:'Einstellungen', exact:true}).click();
    await page.getByRole('button', {name:'Zurück zu allen Meetings', exact:true}).click();
    await page.getByRole('heading', {name:'Alle Meetings', exact:true}).waitFor();
    await page.locator('.history-row').filter({hasText:'Kickoff'}).getByRole('button').click();
    await page.getByRole('heading', {name:'Kickoff', exact:true}).waitFor();
    await page.getByRole('button', {name:'Einstellungen', exact:true}).click();
    // The common languages are always listed; Community adds "Other …" for e.g. Swiss German.
    const meetingLanguage = page.getByLabel('Meeting-Sprache', {exact:true});
    assert.deepEqual(await meetingLanguage.locator('option').allTextContents(), ['Deutsch', 'Englisch', 'Französisch', 'Andere …']);
    await meetingLanguage.selectOption('en-US');
    assert.equal(await page.getByLabel('Sprachcode, z. B. de-CH').count(), 0);
    await meetingLanguage.selectOption('other');
    await page.getByLabel('Sprachcode, z. B. de-CH').fill('de-CH');
    assert.equal(await meetingLanguage.inputValue(), 'other');
    assert.equal(await page.getByLabel('Sprachcode, z. B. de-CH').inputValue(), 'de-CH');
    await page.getByRole('button', {name:'Zurück zum Meeting', exact:true}).click();
    await page.getByRole('heading', {name:'Kickoff', exact:true}).waitFor();
    assert.deepEqual(errors, []);
    console.log('Language UI passed: flag dropdown, Escape, switch to English with saved choice, settings gear, separate meeting language, failed save inside the bar, stale poll, initial language, meeting-language notes headings, immediate switch, keyboard menu, settings return, free Community meeting language.');
  } finally {
    holdState = false; // release a held state request, so a failed assertion cannot hang the close
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
// Exit explicitly: after a failure, a request still pending in the route handler must not keep node alive.
})().catch(error => { console.error(error); process.exit(1); });
