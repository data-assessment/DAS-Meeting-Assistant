import { useEffect, useId, useRef, useState } from 'react'
import { SearchableSelect } from './SearchableSelect'
import { t } from './i18n'

export type OneNoteState = { mode: 'local' | 'onenote'; status: 'ready' | 'preparing' | 'sending' | 'uncertain' | 'saved';
  taskSync?: { status: 'saved' | 'pending' | 'sending' | 'uncertain' | 'error' | 'conflict'; error?: string };
  autoSave?: boolean; reason?: string; notice?: string; localNotice?: string;
  error?: string; url?: string; target?: { book: string; bookName: string; sectionId: string; sectionName: string; account: string } }
type Book = { id: string; label: string; sectionsUrl: string }
type Section = { id: string; name: string }
type Targets = { account: string; notebooks: Book[]; suggestedBook: string; suggestedSection?: string; reason: string; domain: string; warning: string; rememberDomain?: boolean }
async function post(url: string, body: unknown = {}) {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!response.ok) throw new Error(t('Client nicht erreichbar.', 'Client not reachable.'))
  const result = await response.json()
  if (!result.ok) throw new Error(result.error || t('OneNote nicht erreichbar.', 'OneNote not reachable.'))
  return result
}
const message = (error: unknown) => error instanceof Error ? error.message : t('OneNote nicht erreichbar.', 'OneNote not reachable.')

export function oneNoteSavedLabel(value: OneNoteState) {
  const status = value.taskSync?.status
  return status === 'pending' || status === 'sending' ? t('Aufgabenänderungen werden in OneNote gespeichert …', 'Saving task changes to OneNote …')
    : status === 'uncertain' ? t('Aufgaben lokal gesichert · OneNote-Status prüfen', 'Tasks saved locally · Check OneNote status')
    : status === 'error' || status === 'conflict' ? t('Aufgaben lokal gesichert · OneNote noch nicht aktualisiert', 'Tasks saved locally · OneNote not yet updated')
    : t('✓ In OneNote gespeichert', '✓ Saved to OneNote')
}

function DestinationDialog({ base, value, complete, disabled, onClose, onSaved }: {
  base: string; value: OneNoteState; complete: boolean; disabled: boolean; onClose: () => void; onSaved: () => Promise<void>
}) {
  const dialog = useRef<HTMLDialogElement>(null), heading = useRef<HTMLHeadingElement>(null)
  const titleId = useId(), helpId = useId()
  const [targets, setTargets] = useState<Targets | null>(null), [book, setBook] = useState('')
  const [sections, setSections] = useState<Section[]>([]), [section, setSection] = useState('')
  const [remember, setRemember] = useState(false), [error, setError] = useState('')
  const [loading, setLoading] = useState(true), [loadingSections, setLoadingSections] = useState(false)
  const [saving, setSaving] = useState(false), [sectionRetry, setSectionRetry] = useState(0)
  const [sectionError, setSectionError] = useState('')
  const catalogGeneration = useRef(0), sectionGeneration = useRef(0)

  async function load(connect = false) {
    const current = ++catalogGeneration.current
    sectionGeneration.current++
    setLoading(true); setError(''); setTargets(null); setBook(''); setSection(''); setSections([])
    try {
      const result: Targets = await post(base + '/targets', { connect })
      if (current !== catalogGeneration.current) return
      setTargets(result); setBook(result.suggestedBook); setRemember(result.rememberDomain === true)
    } catch (e) { if (current === catalogGeneration.current) setError(message(e)) }
    finally { if (current === catalogGeneration.current) setLoading(false) }
  }
  useEffect(() => {
    const previous = document.activeElement
    const element = dialog.current!
    element.showModal(); heading.current?.focus()
    void load()
    return () => {
      catalogGeneration.current++; sectionGeneration.current++
      element.close()
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus()
    }
  }, [])
  useEffect(() => {
    const current = ++sectionGeneration.current
    setSections([]); setSection(''); setSectionError(''); setLoadingSections(false)
    if (!book || !targets) return
    setLoadingSections(true)
    void post('/api/notes/onenote/sections', { account: targets.account, book }).then(result => {
      if (current !== sectionGeneration.current) return
      setSections(result.sections)
      const remembered = value.target?.account === targets.account && value.target?.book === book ? value.target.sectionId : book === targets.suggestedBook && targets.suggestedSection ? targets.suggestedSection : result.suggestedSection
      setSection(result.sections.some((s: Section) => s.id === remembered) ? remembered : '')
    }).catch(e => { if (current === sectionGeneration.current) setSectionError(message(e)) })
      .finally(() => { if (current === sectionGeneration.current) setLoadingSections(false) })
    return () => { sectionGeneration.current++ }
  }, [book, targets, sectionRetry])

  async function finish() {
    setSaving(true); setError('')
    try {
      await post(base + '/select', { mode: 'onenote', account: targets?.account, book, section,
        rememberDomain: remember ? targets?.domain : '', forgetDomain: !remember ? targets?.domain : '' })
      await onSaved()
    } catch (e) { setError(message(e)); setSaving(false) }
  }
  const selectedBook = targets?.notebooks.find(b => b.sectionsUrl === book)
  const selectedSection = sections.find(s => s.id === section)
  const canFinish = !saving && !disabled && !loading && !loadingSections && !!selectedBook && !!selectedSection
  return <dialog ref={dialog} className="notes-destination-dialog" aria-labelledby={titleId} aria-describedby={helpId}
    onCancel={event => { event.preventDefault(); if (!saving) onClose() }}>
    <header className="destination-header">
      <div><p className="destination-eyebrow">{t('MEETING-NOTIZEN', 'MEETING NOTES')}</p><h1 id={titleId} ref={heading} tabIndex={-1}>{complete ? t('Nach OneNote verschieben', 'Move to OneNote') : t('OneNote-Ziel wählen', 'Choose OneNote destination')}</h1></div>
      <button className="destination-close" aria-label={t('Auswahl abbrechen', 'Cancel selection')} disabled={saving} onClick={onClose}>×</button>
    </header>
    <div className="destination-body">
      <p id={helpId} className="quiet">{complete ? t('Wählen Sie das Ziel für die fertigen Notizen. Es wird eine neue Seite angelegt.', 'Choose the destination for the finished notes. A new page will be created.') : t('Hier wird nach Meeting-Ende automatisch eine neue Seite angelegt.', 'A new page will be created here automatically after the meeting ends.')}</p>
      {error && <p className="notice" role="alert">{error}</p>}
      <>
        {loading && <p className="destination-loading" role="status"><span className="notes-spinner" />{t('Notizbücher werden geladen …', 'Loading notebooks …')}</p>}
        {targets && <>
          {targets.warning && <p className="notice">{targets.warning}</p>}
          <div className="destination-field"><span>{t('Notizbuch', 'Notebook')}</span>
            <SearchableSelect ariaLabel={t('Notizbuch', 'Notebook')} value={book} disabled={saving} placeholder={t('Notizbuch auswählen oder suchen …', 'Select or search notebook …')}
              emptyMessage={t('Kein passendes Notizbuch gefunden.', 'No matching notebook found.')} toggleLabel={t('Notizbücher anzeigen', 'Show notebooks')}
              options={targets.notebooks.map(b => ({ value: b.sectionsUrl, label: b.label }))}
              onChange={next => { if (next !== book) { sectionGeneration.current++; setSection(''); setSections([]); setBook(next) }; setError('') }} />
            {book === targets.suggestedBook && targets.reason && <p className="destination-suggestion">{targets.reason}</p>}
          </div>
          <div className="destination-field"><span>{t('Abschnitt', 'Section')}</span>
            <SearchableSelect ariaLabel={t('Abschnitt', 'Section')} value={section} disabled={saving || loadingSections || !book}
              placeholder={loadingSections ? t('Abschnitte werden geladen …', 'Loading sections …') : book ? t('Abschnitt auswählen oder suchen …', 'Select or search section …') : t('Zuerst ein Notizbuch wählen', 'Choose a notebook first')}
              emptyMessage={t('Kein passender Abschnitt gefunden.', 'No matching section found.')} toggleLabel={t('Abschnitte anzeigen', 'Show sections')}
              options={sections.map(s => ({ value: s.id, label: s.name }))} onChange={setSection} />
          </div>
          {sectionError && <div className="notice" role="alert"><p>{sectionError}</p><button disabled={saving || loadingSections} onClick={() => setSectionRetry(n => n + 1)}>{t('Abschnitte erneut laden', 'Reload sections')}</button></div>}
          {book && !loadingSections && !sectionError && !sections.length && <p className="quiet">{t('Dieses Notizbuch enthält keine erreichbaren Abschnitte. Wählen Sie ein anderes Notizbuch oder legen Sie in OneNote einen Abschnitt an.', 'This notebook has no accessible sections. Choose another notebook or create a section in OneNote.')}</p>}
          {targets.domain && <><label className="checkbox destination-remember"><input type="checkbox" disabled={saving} checked={remember} onChange={e => setRemember(e.target.checked)} /><span>{t('Dieses Ziel für Termine mit ', 'Remember this destination for meetings with ')}<strong>{targets.domain}</strong>{t(' merken', '')}</span></label><p className="quiet">{t('Weitere Termine mit dieser Kundendomäne werden automatisch in diesem Abschnitt gespeichert.', 'Future meetings with this customer domain will be saved to this section automatically.')}</p></>}
          {selectedBook && selectedSection && <div className="destination-preview"><span>{t('Ausgewählter Speicherort', 'Selected location')}</span><strong>{selectedBook.label} › {selectedSection.name}</strong></div>}
        </>}
        <details className="destination-help" open={!loading && !targets ? true : undefined}>
          <summary>{t('Kein passendes Notizbuch?', 'No suitable notebook?')}</summary>
          <p className="quiet">{t('Sie können ein anderes Notizbuch wählen oder abbrechen und lokal speichern. Neue Notizbücher legen Sie in OneNote an; laden Sie die Auswahl danach neu. Bei Ladefehlern kann die Liste unvollständig sein.', 'You can choose another notebook or cancel and save locally. Create new notebooks in OneNote, then reload the list. If loading fails, the list may be incomplete.')}</p>
          <div className="onenote-actions">
            <button disabled={loading || saving} onClick={() => void load()}>{t('Liste neu laden', 'Reload list')}</button>
            {(error || targets?.warning) && <button disabled={loading || saving} onClick={() => void load(true)}>{t('Microsoft-Zugriff prüfen', 'Check Microsoft access')}</button>}
          </div>
        </details>
      </>
    </div>
    <footer className="destination-footer">
      <p className="quiet">{complete ? t('Ihre lokale Notizdatei bleibt erhalten, bis OneNote die Ablage bestätigt hat.', 'Your local notes file is kept until OneNote confirms the upload.') : t('„Fertig“ übernimmt das Ziel. Die fertigen Notizen werden nach Meeting-Ende automatisch gespeichert.', '“Done” applies the destination. The finished notes will be saved automatically after the meeting ends.')}</p>
      <div><button disabled={saving} onClick={onClose}>{t('Abbrechen', 'Cancel')}</button><button className="primary" disabled={!canFinish} onClick={() => void finish()}>{saving ? t('Bitte warten …', 'Please wait …') : complete ? t('Verschieben', 'Move') : t('Fertig', 'Done')}</button></div>
    </footer>
  </dialog>
}

export function NotesOneNote({ id, value, complete, ended, savedLocally, revision, disabled, refresh, storage, documentName, onFolder }: {
  storage: string; documentName?: string; onFolder: () => void;
  id: string; value: OneNoteState; complete: boolean; ended: boolean; savedLocally: boolean; revision: number; disabled: boolean; refresh: () => void | Promise<void>
}) {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const folder = storage.split(/[\\/]/).filter(Boolean).pop() || t('Meeting-Notizen', 'Meeting notes')
  const base = `/api/notes/${id}/onenote`
  const locked = ['preparing', 'sending', 'uncertain', 'saved'].includes(value.status)
  async function publish() {
    setBusy(true); setError(''); setNotice('')
    try { await post(base + '/publish', { revision }) }
    catch (e) { setError(message(e)) }
    finally { setBusy(false); refresh() }
  }
  async function syncTasks() {
    setBusy(true); setError('')
    try { await post(base + '/tasks-sync') }
    catch (e) { setError(message(e)) }
    finally { setBusy(false); refresh() }
  }
  const start = () => { setError(''); setNotice(''); setOpen(true) }
  async function local() {
    setBusy(true); setError('')
    try { await post(base + '/select', { mode: 'local' }); await refresh() }
    catch (e) { setError(message(e)) }
    finally { setBusy(false) }
  }
  return <section className="notes-onenote" aria-label={t('Speicherort', 'Storage location')}>
    <div className="onenote-heading"><div>{ended && <span className="destination-eyebrow">{t('SPEICHERORT', 'STORAGE LOCATION')}</span>}<strong>{!ended ? t('Speicherort nach dem Meeting', 'Storage location after the meeting') : value.status === 'saved' ? t('In OneNote gespeichert', 'Saved to OneNote') : complete && savedLocally && value.mode === 'local' ? t('Auf diesem PC gespeichert', 'Saved on this PC') : value.mode === 'onenote' ? 'OneNote' : t('Auf diesem PC', 'On this PC')}</strong></div>
      {value.status === 'saved' && <button onClick={() => void post(base + '/open').catch(e => setError(message(e)))}>{t('OneNote öffnen', 'Open OneNote')}</button>}
      {!locked && value.mode === 'onenote' && <button disabled={busy || disabled} onClick={start}>{value.target ? t('Ändern', 'Change') : t('Ziel wählen', 'Choose destination')}</button>}</div>
    {!ended && !locked && <fieldset className="destination-methods" disabled={busy || disabled}><legend className="sr-only">{t('Speicherort auswählen', 'Choose storage location')}</legend>
      <label className={value.mode === 'local' ? 'selected' : ''}><input type="radio" name={`storage-${id}`} checked={value.mode === 'local'} onChange={() => void local()} /><span>{t('Auf diesem PC', 'On this PC')}</span></label>
      <label className={value.mode === 'onenote' ? 'selected' : ''}><input type="radio" name={`storage-${id}`} checked={value.mode === 'onenote'} onChange={start} /><span>OneNote</span></label>
    </fieldset>}
    {notice && <p className="copy-success" role="status">{notice}</p>}
    {value.mode === 'onenote' ? <>
      {value.target && <p className="onenote-target">{value.target.bookName} › {value.target.sectionName}</p>}
      {value.status === 'saved' ? <><p className="quiet" role="status">{oneNoteSavedLabel(value)}</p><p className="quiet">{t('Aufgabenänderungen werden automatisch übernommen. Die Zusammenfassung bearbeiten und Aufgaben abhaken können Sie in OneNote.', 'Task changes are applied automatically. You can edit the summary and tick off tasks in OneNote.')}</p>
          {value.taskSync?.error && <p className="notes-error" role="alert">{value.taskSync.error}</p>}
          {['error', 'conflict', 'uncertain'].includes(value.taskSync?.status || '') && <button disabled={busy || disabled} onClick={() => void syncTasks()}>{busy ? t('Bitte warten …', 'Please wait …') : value.taskSync?.status === 'error' ? t('Aufgaben erneut übertragen', 'Resend tasks') : t('Aufgabenstatus prüfen', 'Check task status')}</button>}
        </>
        : ['preparing', 'sending'].includes(value.status) || busy ? <p className="destination-loading" role="status"><span className="notes-spinner" />{value.status === 'uncertain' ? t('Übertragung wird geprüft …', 'Checking transfer …') : t('Wird in OneNote gespeichert …', 'Saving to OneNote …')}</p>
        : value.status === 'uncertain' ? <p className="notice" role="status">{t('Übertragung noch nicht bestätigt. „Status prüfen“ erzeugt keine weitere Seite.', 'Transfer not yet confirmed. “Check status” does not create another page.')}</p>
        : <p className="quiet">{value.error ? t('Noch nicht in OneNote gespeichert. Ihre Notizen bleiben lokal gesichert.', 'Not yet saved to OneNote. Your notes remain saved locally.') : !value.target ? t('Wählen Sie ein Notizbuch und einen Abschnitt.', 'Choose a notebook and a section.') : complete ? t('Die automatische Ablage in OneNote wird gestartet …', 'Starting automatic save to OneNote …') : t('Wird nach Meeting-Ende automatisch in diesem Abschnitt gespeichert.', 'Will be saved to this section automatically after the meeting ends.')}</p>}
      {value.reason && !locked && <p className="quiet">{value.reason}</p>}
      {!locked && value.target && value.error && <button className="primary" disabled={busy || disabled || !complete} onClick={() => void publish()}>{t('Erneut versuchen', 'Try again')}</button>}
      {value.status === 'uncertain' && <button disabled={busy} onClick={() => void publish()}>{t('Status prüfen', 'Check status')}</button>}
      {value.status === 'uncertain' && <button disabled={busy} onClick={() => void post(base + '/open').catch(e => setError(message(e)))}>{t('OneNote öffnen', 'Open OneNote')}</button>}
      {ended && !locked && <button disabled={busy || disabled} onClick={() => void local()}>{t('Auf diesem PC behalten', 'Keep on this PC')}</button>}
    </> : <><p className="onenote-target">{t(`Ordner „${folder}“`, `Folder “${folder}”`)}</p><p className="quiet">{complete ? savedLocally ? t('Sie können die Notizen jetzt nach OneNote verschieben.', 'You can now move the notes to OneNote.') : t('Die lokale Ablage ist noch nicht bestätigt. Beachten Sie den Speicherstatus.', 'Local save not yet confirmed. Check the save status.') : t('Wird nach Meeting-Ende automatisch hier gespeichert.', 'Will be saved here automatically after the meeting ends.')}</p>
      {documentName && savedLocally && <div className="storage-filename">{documentName}</div>}
      {ended && <div className="onenote-local-actions"><button disabled={busy || disabled || !complete} onClick={start}>{t('Nach OneNote verschieben …', 'Move to OneNote …')}</button>{savedLocally && <button onClick={onFolder}>{t('Ordner öffnen', 'Open folder')}</button>}</div>}</>}
    {(value.notice || value.localNotice) && <p className="notice" role="status">{value.localNotice || value.notice}</p>}
    {(error || value.error) && <p className="notes-error" role="alert">{error || value.error}</p>}
    {open && !locked && <DestinationDialog base={base} value={value} complete={complete} disabled={disabled} onClose={() => setOpen(false)}
      onSaved={async () => { setOpen(false); await refresh(); setNotice(complete ? t('Die Ablage in OneNote wird gestartet.', 'Saving to OneNote has started.') : t('Speicherort übernommen.', 'Storage location applied.')); }} />}
  </section>
}
