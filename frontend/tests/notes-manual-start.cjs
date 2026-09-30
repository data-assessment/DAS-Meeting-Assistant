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
    const state = { health:'auth', autoStartSuppressed:false, storagePath:'C:/Test/Meeting-Notizen', enabled:true, active:false,
      currentId:null, autoStart:false, error:'', uiView:'meeting', uiRequest:1,
      options:{managed:false, hasSpeechKey:false, hasChatKey:false, language:'en-US'}, reviews:[] };
    let starts = 0, failStart = false;
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/notes') return route.fulfill({ json: state });
      if (url.pathname === '/api/start') {
        starts++;
        if (failStart) return route.fulfill({json:{ok:false, error:'Bitte den Audio-Abschluss des vorherigen Meetings abwarten.'}});
        state.active = true; state.currentId = 'm1';
        state.reviews = [{ id:'m1', title:'Recording', started:'2026-01-05T10:00:00', ended:'', people:[], peopleNote:'', draft:null,
          busy:false, canSummarize:false, sourceCharacters:0, savedPath:'', savedAt:'', storeError:'', revision:0, phase:'meeting', editable:false, autoRetry:false }];
      }
      if (url.pathname === '/api/stop') {
        state.active = false;
        Object.assign(state.reviews[0], { ended:'2026-01-05T10:05:00', phase:'complete' });
      }
      return route.fulfill({json:{ok:true}});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/?view=notes`);

    // Without Azure keys there is nothing to start.
    await page.getByText('Azure-Zugang fehlt', {exact:true}).waitFor();
    assert.equal(await page.getByRole('button', {name:'Jetzt starten', exact:true}).count(), 0);

    // Manual start needs neither Teams sign-in nor automatic start.
    state.options.hasSpeechKey = true; state.options.hasChatKey = true;
    await page.getByText('Automatischer Start ist aus', {exact:true}).waitFor();
    assert.equal(await page.getByRole('button', {name:'Automatischen Start einschalten', exact:true}).count(), 1);
    await page.getByRole('button', {name:'Jetzt starten', exact:true}).click();
    await page.getByText('Meeting läuft', {exact:true}).waitFor();
    assert.equal(starts, 1);
    assert.equal(await page.getByRole('button', {name:'Neues Meeting starten', exact:true}).count(), 0);

    // After Stop the finished meeting stays visible and offers the next start.
    await page.getByRole('button', {name:'Stoppen', exact:true}).click();
    const next = page.getByRole('button', {name:'Neues Meeting starten', exact:true});
    await next.waitFor();
    failStart = true;
    await next.click();
    await page.getByRole('alert').filter({hasText:'Audio-Abschluss'}).waitFor();
    assert.equal(starts, 2);

    // A managed profile must finish onboarding before capture can start.
    state.reviews = []; state.currentId = null; state.health = 'ok'; state.autoStart = true;
    state.options = {managed:true, setupComplete:true, onboardingComplete:true, language:'de-DE'};
    await page.getByText('Bereit für Teams-Anrufe', {exact:true}).waitFor();
    assert.equal(await page.getByRole('button', {name:'Jetzt starten', exact:true}).count(), 1);
    assert.deepEqual(errors, []);
  } finally {
    await browser?.close();
    server.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
