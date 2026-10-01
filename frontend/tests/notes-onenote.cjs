const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const serveDist = require('./serve-dist.cjs');

(async () => {
  const reports = path.resolve(__dirname, '../../reports');
  fs.mkdirSync(reports, {recursive:true});
  const server = await serveDist();
  let browser, page;
  try {
    browser = await chromium.launch({channel:'msedge', headless:true});
    page = await browser.newPage({viewport:{width:680,height:800}});
    page.setDefaultTimeout(10000);
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const books = ['Projects', 'Vertrieb', 'V3'].map((label, i) => ({id:`b${i}`,label,sectionsUrl:`book-${i}`}));
    const review = {id:'meeting', title:'Projektabstimmung', started:'2026-09-18T10:00:00', ended:'',
      status:'Aufnahme', phase:'live', editable:true, tasksEditable:true, revision:1, busy:false,
      error:'',warning:'',storeError:'',savedPath:'C:/Test/Meeting-Notizen/meeting.md',people:[],peopleNote:'',calendarCandidates:[],calendarSelected:false,
      onenote:{mode:'local',status:'ready'},
      draft:{summary:'Die nächsten Projektschritte wurden abgestimmt.', decisions:'',openQuestions:'',people:[],tasks:[]}};
    const state = {health:'ok',storagePath:'C:/Test/Meeting-Notizen',enabled:true,active:true,currentId:'meeting',autoStart:true,
      error:'',uiView:'meeting',uiRequest:1,options:{hasSpeechKey:true,hasChatKey:true},reviews:[review]};
    const selections = []; let publishes = 0, deliveries = 0, failSave = false, failSections = false, failTargets = false;
    let slowBook = '', releaseSections, remembered = false;
    await page.route('**/api/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/notes') return route.fulfill({json:state});
      if (url.pathname.endsWith('/targets')) {
        if (failTargets) return route.fulfill({json:{ok:false,error:'OneNote ist vorübergehend nicht erreichbar.'}});
        return route.fulfill({json:{ok:true,account:'account',notebooks:books,
          suggestedBook:review.onenote.target?.book || 'book-0',reason:'Passend zur E-Mail-Domäne kunde.example',
          domain:'kunde.example',rememberDomain:remembered,warning:''}});
      }
      if (url.pathname.endsWith('/sections')) {
        const book = route.request().postDataJSON().book;
        if (book === slowBook) await new Promise(resolve => { releaseSections = resolve });
        if (failSections) return route.fulfill({json:{ok:false,error:'Abschnitte nicht erreichbar.'}});
        return route.fulfill({json:{ok:true,sections:[{id:`${book}-meetings`,name:'Besprechungen'},{id:`${book}-archive`,name:'Archiv'}],suggestedSection:`${book}-meetings`}});
      }
      if (url.pathname.endsWith('/select')) {
        const data = route.request().postDataJSON(); selections.push(data);
        if (failSave) return route.fulfill({json:{ok:false,error:'Speicherort konnte nicht gesichert werden.'}});
        remembered = !!data.rememberDomain;
        review.onenote = data.mode === 'local' ? {mode:'local',status:'ready'} : {mode:'onenote',status:'ready',
          target:{account:'account',book:data.book,bookName:books.find(b => b.sectionsUrl === data.book).label,
            sectionId:data.section,sectionName:data.section.endsWith('archive') ? 'Archiv' : 'Besprechungen'}};
        if (data.mode === 'onenote' && review.phase === 'complete') {
          deliveries++; review.onenote.status = 'uncertain'; review.onenote.error = 'Übertragung noch nicht bestätigt.';
          review.editable = review.tasksEditable = false;
        }
      }
      if (url.pathname.endsWith('/publish')) {
        publishes++; review.onenote.status = 'saved'; review.onenote.error = ''; review.editable = false; review.tasksEditable = true;
      }
      if (url.pathname.endsWith('/edit')) {
        const data = route.request().postDataJSON();
        review.draft.tasks = review.draft.tasks.map(t => ({...t,...data.tasks?.[t.id]}));
        review.revision++; review.savedAt = new Date().toISOString();
        review.onenote.taskSync = {status:'pending'};
        return route.fulfill({json:{ok:true,draft:review.draft,revision:review.revision,savedAt:review.savedAt,storeError:''}});
      }
      if (url.pathname.endsWith('/tasks-sync')) review.onenote.taskSync = {status:'saved'};
      return route.fulfill({json:{ok:true}});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/?view=notes`);
    const card = page.getByRole('region',{name:'Speicherort',exact:true});
    const dialog = page.getByRole('dialog');
    const book = dialog.getByRole('combobox',{name:'Notizbuch',exact:true});
    const section = dialog.getByRole('combobox',{name:'Abschnitt',exact:true});
    const done = dialog.getByRole('button',{name:/^(Fertig|Verschieben)$/});
    const open = async () => {
      if (review.onenote.mode === 'onenote') await card.getByRole('button',{name:'Ändern',exact:true}).click();
      else if (review.ended) await card.getByRole('button',{name:'Nach OneNote verschieben …',exact:true}).click();
      else await card.getByRole('radio',{name:'OneNote',exact:true}).click();
      await dialog.waitFor();
      await page.waitForFunction(() => document.querySelector('dialog button.primary:not(:disabled)'));
    };
    await open();
    assert.equal(await dialog.locator('input[type=text]').count(), 2);
    assert.equal(await dialog.locator('select').count(), 0);
    assert.equal(await dialog.getByRole('radio').count(), 0);
    await dialog.getByRole('heading',{name:'OneNote-Ziel wählen',exact:true}).waitFor();
    assert.equal(await book.inputValue(),'Projects');
    assert.equal(await section.inputValue(),'Besprechungen');
    await page.screenshot({path:path.join(reports,'onenote-dialog.png')});
    await book.fill('Ver');
    assert.equal(await page.getByRole('option').count(),1);
    assert.equal(await page.getByRole('option',{name:'Projects',exact:true}).count(),0);
    await page.screenshot({path:path.join(reports,'onenote-search.png')});
    slowBook = 'book-1';
    await page.getByRole('option',{name:'Vertrieb',exact:true}).click();
    await page.waitForFunction(() => document.querySelector('[aria-label="Abschnitt"]').disabled);
    assert.equal(await done.isDisabled(),true);
    while (!releaseSections) await new Promise(resolve => setTimeout(resolve,10));
    releaseSections(); slowBook='';
    await page.waitForFunction(() => document.querySelector('dialog button.primary:not(:disabled)'));
    await dialog.getByRole('button',{name:'Abbrechen',exact:true}).click();
    await dialog.waitFor({state:'hidden'});
    assert.equal(selections.length,0); assert.equal(publishes,0);
    assert.equal(await card.getByRole('radio',{name:'OneNote',exact:true}).evaluate(el => el === document.activeElement),true);

    await open();
    await book.fill('Kein Treffer');
    await dialog.getByText('Kein passendes Notizbuch gefunden.',{exact:true}).waitFor();
    await book.press('Enter'); assert.equal(selections.length,0);
    await book.press('Escape'); assert.equal(await dialog.isVisible(),true);
    assert.equal(await book.inputValue(),'Projects');
    await book.press('Escape'); await dialog.waitFor({state:'hidden'});

    await open();
    await book.fill('V3'); slowBook='book-2'; releaseSections=null;
    await book.press('Enter');
    while (!releaseSections) await new Promise(resolve => setTimeout(resolve,10));
    await book.fill('Projects'); await book.press('Enter');
    await page.waitForFunction(() => document.querySelector('dialog button.primary:not(:disabled)'));
    releaseSections(); slowBook='';
    await section.fill('Archiv'); await section.press('Enter');
    await dialog.getByRole('checkbox').check();
    failSave=true; await done.click();
    await dialog.getByRole('alert').filter({hasText:'nicht gesichert'}).waitFor();
    assert.equal(await book.inputValue(),'Projects'); assert.equal(await section.inputValue(),'Archiv');
    assert.equal(publishes,0);
    failSave=false; await done.click(); await dialog.waitFor({state:'hidden'});
    await card.getByText('Speicherort übernommen.',{exact:true}).waitFor();
    assert.equal(selections.at(-1).book,'book-0');
    assert.equal(selections.at(-1).section,'book-0-archive');
    assert.equal(selections.at(-1).rememberDomain,'kunde.example');
    assert.equal(publishes,0,'During the meeting, Done must only set the destination');

    await open();
    assert.equal(await dialog.getByRole('checkbox').isChecked(),true);
    assert.equal(await section.inputValue(),'Archiv');
    await page.setViewportSize({width:360,height:400});
    await book.fill('Ver');
    const option = page.getByRole('option',{name:'Vertrieb',exact:true});
    const optionBounds = await option.boundingBox();
    assert.ok(optionBounds.y >= 0 && optionBounds.y + optionBounds.height <= 400);
    await book.press('Escape');
    const bounds = await done.boundingBox();
    assert.ok(bounds.y >= 0 && bounds.y + bounds.height <= 400);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true);
    await page.screenshot({path:path.join(reports,'onenote-dialog-small.png')});
    await dialog.getByRole('button',{name:'Abbrechen',exact:true}).click();
    await page.setViewportSize({width:680,height:800});

    failSections=true;
    await card.getByRole('button',{name:'Ändern',exact:true}).click();
    await dialog.getByRole('alert').filter({hasText:'Abschnitte nicht erreichbar'}).waitFor();
    assert.equal(await done.isDisabled(),true);
    failSections=false;
    await dialog.getByRole('button',{name:'Abschnitte erneut laden',exact:true}).click();
    await page.waitForFunction(() => document.querySelector('dialog button.primary:not(:disabled)'));
    await dialog.getByRole('checkbox').uncheck();
    await done.click(); await dialog.waitFor({state:'hidden'});
    assert.equal(selections.at(-1).forgetDomain,'kunde.example');

    failTargets=true;
    await card.getByRole('button',{name:'Ändern',exact:true}).click();
    await dialog.getByRole('alert').filter({hasText:'vorübergehend'}).waitFor();
    assert.equal(await done.isDisabled(),true);
    assert.equal(await dialog.getByRole('radio').count(),0);
    await dialog.getByRole('button',{name:'Abbrechen',exact:true}).click();
    await card.getByRole('radio',{name:'Auf diesem PC',exact:true}).click();
    await page.waitForFunction(() => document.querySelector('input[type=radio][name="storage-meeting"]')?.checked);
    assert.equal(review.onenote.mode,'local'); assert.equal(publishes,0);
    failTargets=false;
    review.phase='complete'; review.ended='2026-09-18T10:30:00'; state.active=false;
    await card.getByRole('button',{name:'Nach OneNote verschieben …',exact:true}).waitFor();
    await card.getByText('Auf diesem PC gespeichert',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Meeting-Fenster schließen',exact:true}).waitFor();
    await page.screenshot({path:path.join(reports,'onenote-local-finished.png')});
    await open();
    await dialog.getByRole('heading',{name:'Nach OneNote verschieben',exact:true}).waitFor();
    await dialog.getByRole('button',{name:'Abbrechen',exact:true}).click();
    assert.equal(deliveries,0);
    await open();
    await page.screenshot({path:path.join(reports,'onenote-move.png')});
    await done.click(); await dialog.waitFor({state:'hidden'});
    await card.getByRole('button',{name:'Status prüfen',exact:true}).waitFor();
    assert.equal(deliveries,1); assert.equal(publishes,0,'Move starts delivery without another Save click');
    assert.equal(await card.getByRole('button',{name:'Ändern',exact:true}).count(),0);
    assert.equal(await card.getByRole('button',{name:'In OneNote speichern',exact:true}).count(),0);
    await card.getByRole('button',{name:'Status prüfen',exact:true}).click();
    await card.getByText('✓ In OneNote gespeichert',{exact:true}).waitFor();
    assert.equal(publishes,1);
    // Regression: automatic OneNote publication must not lock task corrections.
    review.people = [{id:'beate',name:'Beate Muster',email:'beate@example.org',source:'calendar'}];
    review.draft.tasks = [{id:'task',title:'Alternative Formulierung recherchieren und zusenden',owner:'',ownerId:'',recipient:'',due:'',uncertainty:'',questions:[],included:true}];
    review.revision++;
    const owner = page.getByRole('combobox',{name:'Verantwortlich für Aufgabe 1',exact:true});
    await owner.waitFor(); assert.equal(await owner.isDisabled(),false);
    await owner.selectOption('beate');
    await card.getByText('Aufgabenänderungen werden in OneNote gespeichert …',{exact:true}).waitFor();
    assert.equal(review.draft.tasks[0].owner,'Beate Muster');
    assert.equal(review.draft.tasks[0].ownerId,'beate');
    assert.equal(await page.locator('.notes-footer').getByText('✓ In OneNote gespeichert',{exact:true}).count(),0);
    await page.getByRole('button',{name:/Aufgabe 1 bearbeiten:/}).click();
    const titleEditor = page.getByRole('textbox',{name:'Aufgabe 1 bearbeiten',exact:true});
    await titleEditor.fill('Alternative Formulierung für Abschlussstärke recherchieren'); await titleEditor.press('Enter');
    await page.locator('.task-details summary').click();
    await page.getByRole('textbox',{name:'Empfänger',exact:true}).fill('Moritz Muster');
    await page.getByRole('textbox',{name:'Termin',exact:true}).fill('Freitag');
    await page.waitForFunction(() => !document.querySelector('.notes-footer button').disabled);
    assert.equal(review.draft.tasks[0].recipient,'Moritz Muster');
    assert.equal(review.draft.tasks[0].due,'Freitag');
    const inclusion = page.getByRole('checkbox',{name:'In Aufgabenliste aufnehmen',exact:true});
    await inclusion.uncheck(); await page.getByText('0 von 1 ausgewählt',{exact:true}).waitFor();
    await inclusion.check(); await page.getByText('1 von 1 ausgewählt',{exact:true}).waitFor();
    await page.waitForFunction(() => !document.querySelector('.notes-footer button').disabled);
    review.onenote.taskSync = {status:'error',error:'OneNote vorübergehend nicht erreichbar. Änderungen bleiben lokal gesichert.'};
    await card.getByRole('button',{name:'Aufgaben erneut übertragen',exact:true}).click();
    await card.getByText('✓ In OneNote gespeichert',{exact:true}).waitFor();
    // A lost PATCH response offers a status check without another page creation.
    review.onenote.taskSync = {status:'uncertain',error:'Aufgabenänderung noch nicht bestätigt.'};
    await card.getByRole('button',{name:'Aufgabenstatus prüfen',exact:true}).click();
    await card.getByText('✓ In OneNote gespeichert',{exact:true}).waitFor();
    const expand = page.getByRole('button',{name:'Vergrößern',exact:true});
    assert.equal(await expand.locator('svg[aria-hidden=true]').count(),1);
    const expandPath = await expand.locator('path').getAttribute('d');
    await expand.click();
    const shrink = page.getByRole('button',{name:'Verkleinern',exact:true});
    assert.equal(await shrink.locator('svg').count(),1);
    assert.notEqual(await shrink.locator('path').getAttribute('d'),expandPath);
    assert.equal(await owner.isDisabled(),false);
    await shrink.click();
    await page.reload();
    await owner.waitFor();
    assert.equal(await owner.inputValue(),'beate');
    assert.equal(await page.getByRole('button',{name:/Aufgabe 1 bearbeiten:/}).isEnabled(),true);
    assert.equal(deliveries,1); assert.equal(publishes,1);
    await page.screenshot({path:path.join(reports,'onenote-saved.png')});
    await page.getByRole('region',{name:'Aufgabe 1',exact:true}).screenshot({path:path.join(reports,'onenote-task-editable.png')});
    assert.deepEqual(errors,[]);
    console.log('OneNote UI passed: destination flow, task editing after publication, owner selection, optional fields, task inclusion, sync error/retry/status, reload, resize icons, no second page.');
  } catch (error) {
    if (page) await page.screenshot({path:path.join(reports,'onenote-failure.png')}).catch(() => {});
    throw error;
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error);process.exitCode=1});
