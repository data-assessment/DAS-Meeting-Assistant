import { useEffect, useId, useRef, useState } from 'react'
import { SearchableSelect } from './SearchableSelect'

export type OneNoteState = { mode: 'local' | 'onenote'; status: 'ready' | 'preparing' | 'sending' | 'uncertain' | 'saved';
  taskSync?: { status: 'saved' | 'pending' | 'sending' | 'uncertain' | 'error' | 'conflict'; error?: string };
  autoSave?: boolean; reason?: string; notice?: string; localNotice?: string;
  error?: string; url?: string; target?: { book: string; bookName: string; sectionId: string; sectionName: string; account: string } }
type Book = { id: string; label: string; sectionsUrl: string }
type Section = { id: string; name: string }
type Targets = { account: string; notebooks: Book[]; suggestedBook: string; suggestedSection?: string; reason: string; domain: string; warning: string; rememberDomain?: boolean }
async function post(url: string, body: unknown = {}) {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!response.ok) throw new Error('Client nicht erreichbar.')
  const result = await response.json()
  if (!result.ok) throw new Error(result.error || 'OneNote nicht erreichbar.')
  return result
}
const message = (error: unknown) => error instanceof Error ? error.message : 'OneNote nicht erreichbar.'

export function oneNoteSavedLabel(value: OneNoteState) {
  const status = value.taskSync?.status
  return status === 'pending' || status === 'sending' ? 'Aufgabenänderungen werden in OneNote gespeichert …'
    : status === 'uncertain' ? 'Aufgaben lokal gesichert · OneNote-Status prüfen'
    : status === 'error' || status === 'conflict' ? 'Aufgaben lokal gesichert · OneNote noch nicht aktualisiert'
    : '✓ In OneNote gespeichert'
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
      <div><p className="destination-eyebrow">MEETING-NOTIZEN</p><h1 id={titleId} ref={heading} tabIndex={-1}>{complete ? 'Nach OneNote verschieben' : 'OneNote-Ziel wählen'}</h1></div>
      <button className="destination-close" aria-label="Auswahl abbrechen" disabled={saving} onClick={onClose}>×</button>
    </header>
    <div className="destination-body">
      <p id={helpId} className="quiet">{complete ? 'Wählen Sie das Ziel für die fertigen Notizen. Es wird eine neue Seite angelegt.' : 'Hier wird nach Meeting-Ende automatisch eine neue Seite angelegt.'}</p>
      {error && <p className="notice" role="alert">{error}</p>}
      <>
        {loading && <p className="destination-loading" role="status"><span className="notes-spinner" />Notizbücher werden geladen …</p>}
        {targets && <>
          {targets.warning && <p className="notice">{targets.warning}</p>}
          <div className="destination-field"><span>Notizbuch</span>
            <SearchableSelect ariaLabel="Notizbuch" value={book} disabled={saving} placeholder="Notizbuch auswählen oder suchen …"
              emptyMessage="Kein passendes Notizbuch gefunden." toggleLabel="Notizbücher anzeigen"
              options={targets.notebooks.map(b => ({ value: b.sectionsUrl, label: b.label }))}
              onChange={next => { if (next !== book) { sectionGeneration.current++; setSection(''); setSections([]); setBook(next) }; setError('') }} />
            {book === targets.suggestedBook && targets.reason && <p className="destination-suggestion">{targets.reason}</p>}
          </div>
          <div className="destination-field"><span>Abschnitt</span>
            <SearchableSelect ariaLabel="Abschnitt" value={section} disabled={saving || loadingSections || !book}
              placeholder={loadingSections ? 'Abschnitte werden geladen …' : book ? 'Abschnitt auswählen oder suchen …' : 'Zuerst ein Notizbuch wählen'}
              emptyMessage="Kein passender Abschnitt gefunden." toggleLabel="Abschnitte anzeigen"
              options={sections.map(s => ({ value: s.id, label: s.name }))} onChange={setSection} />
          </div>
          {sectionError && <div className="notice" role="alert"><p>{sectionError}</p><button disabled={saving || loadingSections} onClick={() => setSectionRetry(n => n + 1)}>Abschnitte erneut laden</button></div>}
          {book && !loadingSections && !sectionError && !sections.length && <p className="quiet">Dieses Notizbuch enthält keine erreichbaren Abschnitte. Wählen Sie ein anderes Notizbuch oder legen Sie in OneNote einen Abschnitt an.</p>}
          {targets.domain && <><label className="checkbox destination-remember"><input type="checkbox" disabled={saving} checked={remember} onChange={e => setRemember(e.target.checked)} /><span>Dieses Ziel für Termine mit <strong>{targets.domain}</strong> merken</span></label><p className="quiet">Weitere Termine mit dieser Kundendomäne werden automatisch in diesem Abschnitt gespeichert.</p></>}
          {selectedBook && selectedSection && <div className="destination-preview"><span>Ausgewählter Speicherort</span><strong>{selectedBook.label} › {selectedSection.name}</strong></div>}
        </>}
        <details className="destination-help" open={!loading && !targets ? true : undefined}>
          <summary>Kein passendes Notizbuch?</summary>
          <p className="quiet">Sie können ein anderes Notizbuch wählen oder abbrechen und lokal speichern. Neue Notizbücher legen Sie in OneNote an; laden Sie die Auswahl danach neu. Bei Ladefehlern kann die Liste unvollständig sein.</p>
          <div className="onenote-actions">
            <button disabled={loading || saving} onClick={() => void load()}>Liste neu laden</button>
            {(error || targets?.warning) && <button disabled={loading || saving} onClick={() => void load(true)}>Microsoft-Zugriff prüfen</button>}
          </div>
        </details>
      </>
    </div>
    <footer className="destination-footer">
      <p className="quiet">{complete ? 'Ihre lokale Notizdatei bleibt erhalten, bis OneNote die Ablage bestätigt hat.' : '„Fertig“ übernimmt das Ziel. Die fertigen Notizen werden nach Meeting-Ende automatisch gespeichert.'}</p>
      <div><button disabled={saving} onClick={onClose}>Abbrechen</button><button className="primary" disabled={!canFinish} onClick={() => void finish()}>{saving ? 'Bitte warten …' : complete ? 'Verschieben' : 'Fertig'}</button></div>
    </footer>
  </dialog>
}

export function NotesOneNote({ id, value, complete, ended, savedLocally, revision, disabled, refresh, storage, documentName, onFolder }: {
  storage: string; documentName?: string; onFolder: () => void;
  id: string; value: OneNoteState; complete: boolean; ended: boolean; savedLocally: boolean; revision: number; disabled: boolean; refresh: () => void | Promise<void>
}) {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const folder = storage.split(/[\\/]/).filter(Boolean).pop() || 'Meeting-Notizen'
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
  return <section className="notes-onenote" aria-label="Speicherort">
    <div className="onenote-heading"><div>{ended && <span className="destination-eyebrow">SPEICHERORT</span>}<strong>{!ended ? 'Speicherort nach dem Meeting' : value.status === 'saved' ? 'In OneNote gespeichert' : complete && savedLocally && value.mode === 'local' ? 'Auf diesem PC gespeichert' : value.mode === 'onenote' ? 'OneNote' : 'Auf diesem PC'}</strong></div>
      {value.status === 'saved' && <button onClick={() => void post(base + '/open').catch(e => setError(message(e)))}>OneNote öffnen</button>}
      {!locked && value.mode === 'onenote' && <button disabled={busy || disabled} onClick={start}>{value.target ? 'Ändern' : 'Ziel wählen'}</button>}</div>
    {!ended && !locked && <fieldset className="destination-methods" disabled={busy || disabled}><legend className="sr-only">Speicherort auswählen</legend>
      <label className={value.mode === 'local' ? 'selected' : ''}><input type="radio" name={`storage-${id}`} checked={value.mode === 'local'} onChange={() => void local()} /><span>Auf diesem PC</span></label>
      <label className={value.mode === 'onenote' ? 'selected' : ''}><input type="radio" name={`storage-${id}`} checked={value.mode === 'onenote'} onChange={start} /><span>OneNote</span></label>
    </fieldset>}
    {notice && <p className="copy-success" role="status">{notice}</p>}
    {value.mode === 'onenote' ? <>
      {value.target && <p className="onenote-target">{value.target.bookName} › {value.target.sectionName}</p>}
      {value.status === 'saved' ? <><p className="quiet" role="status">{oneNoteSavedLabel(value)}</p><p className="quiet">Aufgabenänderungen werden automatisch übernommen. Die Zusammenfassung bearbeiten und Aufgaben abhaken können Sie in OneNote.</p>
          {value.taskSync?.error && <p className="notes-error" role="alert">{value.taskSync.error}</p>}
          {['error', 'conflict', 'uncertain'].includes(value.taskSync?.status || '') && <button disabled={busy || disabled} onClick={() => void syncTasks()}>{busy ? 'Bitte warten …' : value.taskSync?.status === 'error' ? 'Aufgaben erneut übertragen' : 'Aufgabenstatus prüfen'}</button>}
        </>
        : ['preparing', 'sending'].includes(value.status) || busy ? <p className="destination-loading" role="status"><span className="notes-spinner" />{value.status === 'uncertain' ? 'Übertragung wird geprüft …' : 'Wird in OneNote gespeichert …'}</p>
        : value.status === 'uncertain' ? <p className="notice" role="status">Übertragung noch nicht bestätigt. „Status prüfen“ erzeugt keine weitere Seite.</p>
        : <p className="quiet">{value.error ? 'Noch nicht in OneNote gespeichert. Ihre Notizen bleiben lokal gesichert.' : !value.target ? 'Wählen Sie ein Notizbuch und einen Abschnitt.' : complete ? 'Die automatische Ablage in OneNote wird gestartet …' : 'Wird nach Meeting-Ende automatisch in diesem Abschnitt gespeichert.'}</p>}
      {value.reason && !locked && <p className="quiet">{value.reason}</p>}
      {!locked && value.target && value.error && <button className="primary" disabled={busy || disabled || !complete} onClick={() => void publish()}>Erneut versuchen</button>}
      {value.status === 'uncertain' && <button disabled={busy} onClick={() => void publish()}>Status prüfen</button>}
      {value.status === 'uncertain' && <button disabled={busy} onClick={() => void post(base + '/open').catch(e => setError(message(e)))}>OneNote öffnen</button>}
      {ended && !locked && <button disabled={busy || disabled} onClick={() => void local()}>Auf diesem PC behalten</button>}
    </> : <><p className="onenote-target">Ordner „{folder}“</p><p className="quiet">{complete ? savedLocally ? 'Sie können die Notizen jetzt nach OneNote verschieben.' : 'Die lokale Ablage ist noch nicht bestätigt. Beachten Sie den Speicherstatus.' : 'Wird nach Meeting-Ende automatisch hier gespeichert.'}</p>
      {documentName && savedLocally && <div className="storage-filename">{documentName}</div>}
      {ended && <div className="onenote-local-actions"><button disabled={busy || disabled || !complete} onClick={start}>Nach OneNote verschieben …</button>{savedLocally && <button onClick={onFolder}>Ordner öffnen</button>}</div>}</>}
    {(value.notice || value.localNotice) && <p className="notice" role="status">{value.localNotice || value.notice}</p>}
    {(error || value.error) && <p className="notes-error" role="alert">{error || value.error}</p>}
    {open && !locked && <DestinationDialog base={base} value={value} complete={complete} disabled={disabled} onClose={() => setOpen(false)}
      onSaved={async () => { setOpen(false); await refresh(); setNotice(complete ? 'Die Ablage in OneNote wird gestartet.' : 'Speicherort übernommen.'); }} />}
  </section>
}
