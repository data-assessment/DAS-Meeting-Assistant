const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const clone = value => JSON.parse(JSON.stringify(value));
(async () => {
 const browser = await chromium.launch({ channel: 'msedge', headless: true });
 const page = await browser.newPage({ viewport: { width: 680, height: 750 } });
 const errors = []; page.on('pageerror', error => errors.push(error.message));
 const task = (id, title, owner='') => ({ included:true,suggested:true,id,title,owner,ownerId:'',recipient:'',due:'',uncertainty:'',questions:[] });
 const a = task('one','Migration prüfen'), b = task('two','Review und Merge','Robin');
 b.questions=[{id:'order',label:'Export für welches Zielsystem?',field:'context',options:['CSV','OneNote'],answer:''}];
 const review={id:'meeting',title:'Deployment-Abstimmung',started:'2026-09-14T10:00:00',ended:'2026-09-14T10:30:00',status:'Gespräch beendet',error:'',warning:'',busy:false,canSummarize:false,sourceCharacters:0,savedPath:'test.json',savedAt:'2026-09-14T10:30:00',storeError:'',revision:1,phase:'complete',editable:true,autoRetry:false,peopleAccessNeeded:true,peopleNote:'',people:[{id:'robin',name:'Robin',email:'robin@example.org',source:'calendar'},{id:'moritz',name:'Moritz',email:'moritz@example.org',source:'call'}],draft:{summary:'Migration vorbereiten.\n\nTechnik:\n- Bestandsdaten prüfen.\n- Rollback vorsehen.\n\nEin weiterer Termin dient der Abstimmung.',decisions:'',openQuestions:'',people:[],tasks:[a,b,task('three','Angebot prüfen'),task('four','Weiteren Termin vorbereiten')]}};
 const state={health:'ok',autoStartSuppressed:false,storagePath:'C:/Test/Meeting-Notizen',enabled:true,active:false,currentId:null,autoStart:true,error:'',uiView:'meeting',uiRequest:1,options:{hasSpeechKey:true,hasChatKey:true},reviews:[review]};
 let delay=0,fail=false,copyFail=false,copied='';
 const fits=[],closes=[];
 await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url());
   if(url.pathname==='/api/notes/fit'){const data=route.request().postDataJSON();fits.push(data);await page.setViewportSize(data.expanded?{width:980,height:1000}:{width:680,height:750});return route.fulfill({json:{ok:true}});}
   if(url.pathname==='/api/notes/close'){closes.push(true);return route.fulfill({json:{ok:true}});}
   if(url.pathname==='/api/notes')return route.fulfill({json:clone(state)});
   if(url.pathname.endsWith('/copy')){if(copyFail)return route.fulfill({json:{ok:false,error:'Zwischenablage nicht verfügbar'}});copied='# '+review.title+'\n'+review.draft.summary+'\n'+review.draft.tasks.filter(t=>t.included!==false).map(t=>t.title).join('\n');return route.fulfill({json:{ok:true}});}
   if(url.pathname.endsWith('/calendar-select')){review.calendarSelected=true;review.title='Erkannter Kundentermin';review.people.push({id:'invitee',name:'Eva Einladung',email:'eva@example.org',source:'calendar'});return route.fulfill({json:{ok:true}});}
   if(url.pathname.endsWith('/people-refresh')){review.people.push({id:'contact',name:'Klara Kontakt',email:'contact@example.org',source:'contact'});return route.fulfill({json:{ok:true}});}
   if(url.pathname.endsWith('/people-connect')){review.peopleAccessNeeded=false;return route.fulfill({json:{ok:true}});}
   if(url.pathname.endsWith('/edit')){
     const data=route.request().postDataJSON();if(delay)await new Promise(r=>setTimeout(r,delay));
     if(fail)return route.fulfill({json:{ok:false,error:'Speichern fehlgeschlagen'}});
     Object.assign(review.draft,data.text||{});
     review.draft.tasks=review.draft.tasks.map(t=>({...t,...data.tasks?.[t.id]}));
     if(data.people)review.draft.people.push(...clone(data.people));
     review.revision++;
     return route.fulfill({json:{ok:true,draft:clone(review.draft),revision:review.revision,savedAt:'2026-09-14T10:31:00',storeError:''}});
   }
   return route.fulfill({json:{ok:true}});
 });
 const finalDraft=clone(review.draft); review.draft=null; review.ended=''; review.editable=false;review.tasksEditable=true;review.phase='live';state.active=true;state.currentId=review.id;
 await page.goto('http://127.0.0.1:8877/?view=notes');
 await page.getByText('Gespräch wird verarbeitet …',{exact:true}).waitFor();
 assert.equal(await page.locator('.notes-spinner').count(),1);
 assert.equal(await page.getByRole('checkbox').count(),0);
 assert.equal(await page.getByText('Aufnahme läuft beim Schließen im Hintergrund weiter.',{exact:true}).count(),1);
 const cardHeight=await page.locator('.summary-card').evaluate(e=>e.getBoundingClientRect().height);
 const headingGap=await page.locator('.tasks-heading').evaluate(e=>e.offsetTop-document.querySelector('.summary-card').offsetTop);
 review.draft=finalDraft;review.revision++;
 await page.getByText('Vorläufig',{exact:true}).waitFor();
 assert.equal(await page.locator('.notes-summary').textContent(),review.draft.summary);
 assert.equal(await page.locator('.notes-summary').evaluate(e=>getComputedStyle(e).whiteSpace),'pre-wrap');
 assert.equal(await page.locator('.summary-card').evaluate(e=>e.getBoundingClientRect().height),cardHeight);
 assert.equal(await page.locator('.tasks-heading').evaluate(e=>e.offsetTop-document.querySelector('.summary-card').offsetTop),headingGap);
 assert.equal(await page.getByRole('tab').count(),0);
 if(process.env.NOTES_SCREENSHOT)await page.screenshot({path:process.env.NOTES_SCREENSHOT.replace('.png','-live.png')});
 await page.getByRole('button',{name:'Vergrößern',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('main').classList.contains('summary-expanded'));
 assert.equal(await page.getByRole('region',{name:'Speicherort',exact:true}).isVisible(),true);
 assert.equal(await page.locator('.tasks-heading').isVisible(),true);
 assert.ok(await page.locator('.summary-card').evaluate(e=>e.getBoundingClientRect().height)>cardHeight);
 const storageBeforeSummary=()=>page.evaluate(()=>document.querySelector('.notes-onenote').getBoundingClientRect().top<document.querySelector('.summary-card').getBoundingClientRect().top);
 assert.equal(await storageBeforeSummary(),true);
 assert.equal(await page.getByRole('button',{name:'Verkleinern',exact:true}).getAttribute('aria-expanded'),'true');
 assert.ok(fits.some(size=>size.expanded&&size.height===960));
 if(process.env.NOTES_SCREENSHOT)await page.screenshot({path:process.env.NOTES_SCREENSHOT.replace('.png','-expanded-live.png')});

 assert.equal(await page.getByRole('button',{name:'Bearbeiten',exact:true}).count(),0);
 await page.getByRole('button',{name:'Verkleinern',exact:true}).click();
 const inclusion=page.getByRole('checkbox',{name:'In Aufgabenliste aufnehmen',exact:true}).first();
 await inclusion.uncheck();await page.waitForTimeout(150);assert.equal(review.draft.tasks[0].included,false);
 await page.getByText('3 von 4 ausgewählt',{exact:true}).waitFor();
 assert.equal(await page.getByText('Abgewählt',{exact:true}).count(),1);
 await inclusion.check();
 review.ended='2026-09-14T10:30:00';review.busy=true;state.active=false;
 await page.getByText('Finale Zusammenfassung wird erstellt …',{exact:true}).waitFor();
 assert.equal(await page.locator('.summary-card').evaluate(e=>e.getBoundingClientRect().height),cardHeight);
 // Storage controls change at meeting end; summary/task spacing stays stable.
 assert.equal(await page.locator('.tasks-heading').evaluate(e=>e.offsetTop-document.querySelector('.summary-card').offsetTop),headingGap);
 await page.getByRole('button',{name:/Aufgabe 1 bearbeiten:/}).click();
 const liveEditor=page.getByRole('textbox',{name:'Aufgabe 1 bearbeiten',exact:true});
 await liveEditor.fill('Migration prüfen – während Abschluss korrigiert');await page.waitForTimeout(150);
 review.draft.summary='Finaler verdichteter Stand';review.revision++;review.phase='complete';review.busy=false;review.editable=true;
 await page.getByText('Text direkt bearbeiten. Jede Änderung wird automatisch gespeichert.',{exact:true}).waitFor();
 if(process.env.NOTES_SCREENSHOT)await page.screenshot({path:process.env.NOTES_SCREENSHOT.replace('.png','-done.png')});
 assert.equal(await liveEditor.inputValue(),'Migration prüfen – während Abschluss korrigiert');
 assert.equal(await liveEditor.evaluate(e=>e===document.activeElement),true);
 await liveEditor.press('Enter');
 assert.equal(await page.locator('.notes-spinner').count(),0);
 await page.getByRole('button',{name:'Vergrößern',exact:true}).click();

 const document=page.getByRole('textbox',{name:'Zusammenfassung direkt bearbeiten',exact:true});
 const editorNode=await document.elementHandle();
 assert.equal(await page.getByRole('button',{name:'Nach OneNote verschieben …',exact:true}).isVisible(),true);
 assert.equal(await storageBeforeSummary(),true);
 if(process.env.NOTES_SCREENSHOT)await page.screenshot({path:process.env.NOTES_SCREENSHOT.replace('.png','-expanded-done.png')});

 delay=500;
 const longText=('Ein kurzer thematischer Absatz mit korrigierter Erkennung.\n\n- Erster Aspekt.\n- Zweiter Aspekt.\n\n').repeat(300);
 await document.fill(longText);await document.press('End');await document.press('Enter');await document.press('a');
 await page.locator('.summary-heading').getByText('Speichert …',{exact:true}).waitFor();
 await page.waitForTimeout(1300);delay=0;
 await page.locator('.summary-heading').getByText(/^Gespeichert um /).waitFor();
 assert.ok(review.draft.summary.includes('\na'));
 assert.equal(await document.evaluate(e=>e.scrollHeight>e.clientHeight),true);
 assert.equal(await page.locator('.notes-scroll').evaluate(e=>getComputedStyle(e).overflowY),'auto');
 await page.getByRole('button',{name:'Verkleinern',exact:true}).click();
 await page.getByRole('button',{name:'Vergrößern',exact:true}).click();

 assert.equal(await document.inputValue(),review.draft.summary);
 assert.equal(await editorNode.evaluate(e=>e===window.document.querySelector('.summary-inline-editor')),true,'Resizing must preserve the editor node and undo history');
 await page.getByRole('button',{name:'Verkleinern',exact:true}).click();

 const title=page.getByRole('button',{name:/Aufgabe 1 bearbeiten:/});await title.waitFor();
 assert.equal(await page.getByRole('button',{name:'Angaben klären',exact:true}).count(),0);
 const before=page.getByRole('button',{name:'CSV',exact:true});
 await title.click();await page.getByRole('textbox',{name:'Aufgabe 1 bearbeiten',exact:true}).fill('Migration gemeinsam prüfen');
 await before.click();await page.waitForTimeout(150);
 assert.equal(review.draft.tasks[0].title,'Migration gemeinsam prüfen');
 assert.equal(review.draft.tasks[1].questions[0].answer,'CSV');
 assert.equal(await page.getByRole('button',{name:'✓ CSV',exact:true}).getAttribute('aria-pressed'),'true');
 await page.getByRole('button',{name:'OneNote',exact:true}).click();
 await page.getByLabel('Verantwortlich für Aufgabe 2',{exact:true}).selectOption('moritz');await page.waitForTimeout(150);
 assert.equal(review.draft.tasks[1].owner,'Moritz');assert.equal(review.draft.tasks[1].questions[0].answer,'OneNote');
 await page.getByLabel('Verantwortlich für Aufgabe 1',{exact:true}).selectOption('__add');
 await page.getByLabel('Name der weiteren Person').fill('Klara Beispiel');
 await page.getByRole('button',{name:'Person hinzufügen',exact:true}).click();await page.waitForTimeout(100);
 assert.equal(review.draft.people[0].name,'Klara Beispiel');assert.equal(review.draft.tasks[0].ownerId,review.draft.people[0].id);

 await page.getByRole('button',{name:'Teams-Personen verbinden',exact:true}).click();
 await page.getByRole('button',{name:'Teams-Personen verbinden',exact:true}).waitFor({state:'hidden'});
 assert.equal(await page.locator('optgroup[label="Aus dem Teams-Anruf"] option').first().textContent(),'Moritz');
 assert.equal(await page.locator('input[aria-label="Termin"]:visible').count(),0);
 await page.getByRole('button',{name:/Aufgabe 4 bearbeiten:/}).click();
 await page.getByRole('textbox',{name:'Aufgabe 4 bearbeiten',exact:true}).fill('Versehentliche Änderung');
 await page.getByRole('textbox',{name:'Aufgabe 4 bearbeiten',exact:true}).press('Escape');await page.waitForTimeout(100);
 assert.equal(review.draft.tasks[3].title,'Weiteren Termin vorbereiten');
 delay=300;
 await title.click();await page.getByRole('textbox',{name:'Aufgabe 1 bearbeiten',exact:true}).fill('Letzte Änderung');
 await page.getByRole('button',{name:'CSV',exact:true}).click();
 await page.waitForTimeout(850);assert.equal(review.draft.tasks[0].title,'Letzte Änderung');assert.equal(review.draft.tasks[1].questions[0].answer,'CSV');
 delay=0;await page.reload();await title.waitFor();assert.equal(await page.getByRole('button',{name:'✓ CSV',exact:true}).getAttribute('aria-pressed'),'true');
 assert.equal(await page.getByLabel('Verantwortlich für Aufgabe 1',{exact:true}).inputValue(),review.draft.people[0].id);
 fail=true;await page.getByRole('button',{name:'OneNote',exact:true}).click();await page.getByRole('button',{name:'Erneut versuchen',exact:true}).waitFor();
 fail=false;await page.getByRole('button',{name:'Erneut versuchen',exact:true}).click();await page.waitForTimeout(150);assert.equal(review.draft.tasks[1].questions[0].answer,'OneNote');
 // Direct editing in the overview; no separate mode or ambiguous save button.
 const inline=page.getByRole('textbox',{name:'Zusammenfassung direkt bearbeiten',exact:true});
 await inline.fill('Direkt in der Übersicht korrigiert.');await page.waitForTimeout(150);
 assert.equal(review.draft.summary,'Direkt in der Übersicht korrigiert.');
 assert.equal(await page.getByRole('button',{name:'Leseansicht',exact:true}).count(),0);
 review.warning='Ein Audioteil war kurz unterbrochen.';await page.getByText(review.warning,{exact:true}).waitFor();
 assert.equal(await page.locator('.status-label').getAttribute('class'),'status-label');
 assert.equal(await page.locator('.status-label').textContent(),'Notizen fertig');
 await inclusion.uncheck();await page.waitForTimeout(150);
 await page.getByRole('button',{name:'Notizen kopieren',exact:true}).click();
 await page.getByText('Kopiert · Notizen und ausgewählte Aufgaben sind in der Zwischenablage.',{exact:true}).waitFor();
 assert.ok(copied.startsWith('# Deployment-Abstimmung'));assert.ok(!copied.includes('Letzte Änderung'));
 copyFail=true;await page.getByRole('button',{name:'Notizen kopieren',exact:true}).click();
 await page.getByText('Zwischenablage nicht verfügbar',{exact:true}).waitFor();
 assert.equal(await page.locator('.status-label').getAttribute('class'),'status-label');
 const older=clone(review);older.id='older';older.title='Älteres Kundengespräch';older.warning='';older.draft.tasks=[];
 state.reviews.push(older);await page.waitForTimeout(1100);
 await page.getByRole('button',{name:'Alle Meetings',exact:true}).click();
 await page.getByRole('heading',{name:'Alle Meetings',exact:true}).waitFor();
 await page.getByRole('searchbox',{name:'Meeting suchen',exact:true}).fill('Älteres');
 await page.getByRole('button',{name:'Öffnen',exact:true}).click();
 await page.getByRole('heading',{name:'Älteres Kundengespräch',exact:true}).waitFor();
 assert.equal(await page.locator('main:visible').count(),1);
 await page.getByRole('button',{name:'Alle Meetings',exact:true}).click();
 await page.getByRole('button',{name:'Zum aktuellen Meeting',exact:true}).click();
 await page.getByRole('heading',{name:'Deployment-Abstimmung',exact:true}).waitFor();
 if(process.env.NOTES_SCREENSHOT)await page.screenshot({path:process.env.NOTES_SCREENSHOT.replace('.png','-normal.png')});
 await page.getByRole('button',{name:'Personen erneut laden',exact:true}).click();
 await page.locator('optgroup[label="Aus Teams-Chats · Teilnahme nicht bestätigt"] option').first().waitFor({state:'attached'});
 await page.getByLabel('Verantwortlich für Aufgabe 1',{exact:true}).selectOption('contact');await page.waitForTimeout(150);
 assert.equal(review.draft.tasks[0].owner,'Klara Kontakt');
 review.calendarCandidates=[{id:'one',title:'Kundentermin',start:'2026-09-15T10:00:00Z'},{id:'two',title:'Anderer Termin',start:'2026-09-15T10:00:00Z'}];
 await page.getByLabel('Welcher Kalendertermin gehört zu diesem Gespräch?',{exact:true}).selectOption('one');
 await page.getByRole('heading',{name:'Erkannter Kundentermin',exact:true}).waitFor();
 assert.equal(await page.getByLabel('Verantwortlich für Aufgabe 1',{exact:true}).inputValue(),'contact');
 assert.ok(await page.locator('optgroup[label="Aus der Kalendereinladung"] option').allTextContents().then(v=>v.includes('Eva Einladung')));
 await page.setViewportSize({width:360,height:640});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
 assert.equal(await page.locator('main:visible').count(),1);assert.deepEqual(errors,[]);
 await page.getByRole('button',{name:'Meeting-Fenster schließen',exact:true}).click();
 assert.equal(closes.length,1);
 assert.equal(fits.at(-1).expanded,false);
 assert.equal(await page.getByRole('button',{name:'Zur Übersicht',exact:true}).count(),0);
 if(process.env.NOTES_SCREENSHOT) await page.screenshot({path:process.env.NOTES_SCREENSHOT,fullPage:true});
 console.log('PASS: Markdown copy success/failure, visible searchable meeting history, direct summary editor, explicit save receipt, green completion despite warning, four phases, fixed summary, task edits through finalization without focus loss, selection, long full-document editor,  inline/blur-click, persistent choices, person selection/add, optional fields collapsed, Escape, delayed save queue, reload, retry, narrow layout.');
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1)});
