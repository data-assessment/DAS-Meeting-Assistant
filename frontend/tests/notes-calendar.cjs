const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const root = path.resolve(__dirname, '../dist');
  const server = http.createServer((req, res) => {
    const file = path.join(root, new URL(req.url, 'http://localhost').pathname === '/' ? 'index.html' : new URL(req.url, 'http://localhost').pathname);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true });
    const page = await browser.newPage({ viewport: { width: 680, height: 850 } });
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const people = [{ id: 'anna', name: 'Anna Muster', email: 'anna@example.org', source: 'calendar' }, { id: 'robin', name: 'Robin Beispiel', email: 'robin@example.org', source: 'calendar' }];
    const candidates = [{ id: 'a', title: 'Projektplanung September', start: '2026-09-17T11:00:00Z', end: '2026-09-17T11:30:00Z', response: 'accepted' }, { id: 'b', title: 'Andere Besprechung', start: '2026-09-17T11:00:00Z', end: '2026-09-17T11:45:00Z', response: 'tentativelyAccepted' }];
    const review = { id: 'meeting', title: 'Teams-Gespräch', started: '2026-09-17T13:01:00', ended: '2026-09-17T13:30:00', status: 'Gespräch beendet', phase: 'complete', editable: true, tasksEditable: true, revision: 1, busy: false, error: '', warning: '', storeError: '', people: [], peopleNote: '', calendarCandidates: candidates, calendarSelected: false, draft: { summary: 'Die nächsten Projektschritte wurden abgestimmt.', decisions: '', openQuestions: '', people: [], tasks: [{ id: 'task', title: 'Angebot vorbereiten', owner: '', ownerId: '', recipient: '', due: '', uncertainty: '', questions: [], included: true }] } };
    const state = { health: 'ok', storagePath: 'C:/Test/Meeting-Notizen', enabled: true, active: false, currentId: null, autoStart: true, error: '', uiView: 'meeting', uiRequest: 1, options: { hasSpeechKey: true, hasChatKey: true }, reviews: [review] };
    let reloads = 0;
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/notes') return route.fulfill({ json: state });
      if (url.pathname.endsWith('/calendar-select')) {
        assert.equal(route.request().postDataJSON().id, 'a');
        review.calendarSelected = true; review.title = candidates[0].title; review.people = people;
        review.calendarContext = { ...candidates[0], organizer: 'Anna Muster', people };
        review.displayTitle = '17.09.2026 · 13:00 · Projektplanung September';
        review.draft.tasks[0].owner = 'Robin Beispiel'; review.draft.tasks[0].ownerId = 'robin'; review.revision++;
      }
      if (url.pathname.endsWith('/calendar-refresh')) reloads++;
      return route.fulfill({ json: { ok: true } });
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/?view=notes`);
    const panel = page.getByRole('region', { name: 'Outlook-Termin', exact: true });
    await panel.getByText('2 Outlook-Termine passen zu dieser Aufzeichnung. Bitte auswählen.').waitFor();
    assert.match(await panel.locator('select').textContent(), /Mit Vorbehalt/);
    await page.screenshot({ path: path.resolve(__dirname, '../../reports/outlook-choice.png') });
    await panel.locator('select').selectOption('a');
    await page.getByRole('heading', { name: '17.09.2026 · 13:00 · Projektplanung September', exact: true }).waitFor();
    assert.match(await panel.textContent(), /Teilnehmer laut Einladung \(2\)/);
    assert.match(await panel.textContent(), /Robin Beispiel/);
    assert.doesNotMatch(await panel.textContent(), /Andere Besprechung/);
    assert.equal(await page.getByRole('combobox', { name: 'Verantwortlich für Aufgabe 1' }).locator('optgroup[label="Aus der Kalendereinladung"] option').count(), 2);
    assert.equal(await page.getByRole('combobox', { name: 'Verantwortlich für Aufgabe 1' }).inputValue(), 'robin');
    assert.equal(await page.locator('option[value="__retained"]').count(), 0);
    await page.screenshot({ path: path.resolve(__dirname, '../../reports/outlook-selected.png') });
    await page.getByRole('button', { name: 'Vergrößern', exact: true }).click();
    assert.equal(await panel.isVisible(), true);
    await page.setViewportSize({ width: 360, height: 780 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.screenshot({ path: path.resolve(__dirname, '../../reports/outlook-mobile.png') });
    await page.getByRole('button', { name: 'Verkleinern', exact: true }).click();
    review.calendarContext = null; review.calendarSelected = false; review.calendarCandidates = []; review.draft.tasks = [];
    await panel.getByRole('button', { name: 'Outlook-Termine neu laden' }).click();
    assert.equal(reloads, 1);
    assert.deepEqual(errors, []);
    console.log('Outlook UI: ambiguity, selection, title, roster, owner dropdown, full view, mobile and reload without tasks passed.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
