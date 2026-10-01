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
  if (!response.ok) throw new Error(t('oneNote.errors.clientUnreachable'))
  const result = await response.json()
  if (!result.ok) throw new Error(result.error || t('oneNote.errors.oneNoteUnreachable'))
  return result
}
const message = (error: unknown) => error instanceof Error ? error.message : t('oneNote.errors.oneNoteUnreachable')

export function oneNoteSavedLabel(value: OneNoteState) {
  const status = value.taskSync?.status
  return status === 'pending' || status === 'sending' ? t('oneNote.taskSync.saving')
    : status === 'uncertain' ? t('oneNote.taskSync.uncertain')
    : status === 'error' || status === 'conflict' ? t('oneNote.taskSync.notUpdated')
    : t('oneNote.taskSync.saved')
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
      <div><p className="destination-eyebrow">{t('oneNote.destination.eyebrow')}</p><h1 id={titleId} ref={heading} tabIndex={-1}>{complete ? t('oneNote.destination.titleMove') : t('oneNote.destination.titleChoose')}</h1></div>
      <button className="destination-close" aria-label={t('oneNote.destination.cancelSelection')} disabled={saving} onClick={onClose}>×</button>
    </header>
    <div className="destination-body">
      <p id={helpId} className="quiet">{complete ? t('oneNote.destination.helpMove') : t('oneNote.destination.helpChoose')}</p>
      {error && <p className="notice" role="alert">{error}</p>}
      <>
        {loading && <p className="destination-loading" role="status"><span className="notes-spinner" />{t('oneNote.destination.loadingNotebooks')}</p>}
        {targets && <>
          {targets.warning && <p className="notice">{targets.warning}</p>}
          <div className="destination-field"><span>{t('oneNote.destination.notebook')}</span>
            <SearchableSelect ariaLabel={t('oneNote.destination.notebook')} value={book} disabled={saving} placeholder={t('oneNote.destination.notebookPlaceholder')}
              emptyMessage={t('oneNote.destination.notebookEmpty')} toggleLabel={t('oneNote.destination.showNotebooks')}
              options={targets.notebooks.map(b => ({ value: b.sectionsUrl, label: b.label }))}
              onChange={next => { if (next !== book) { sectionGeneration.current++; setSection(''); setSections([]); setBook(next) }; setError('') }} />
            {book === targets.suggestedBook && targets.reason && <p className="destination-suggestion">{targets.reason}</p>}
          </div>
          <div className="destination-field"><span>{t('oneNote.destination.section')}</span>
            <SearchableSelect ariaLabel={t('oneNote.destination.section')} value={section} disabled={saving || loadingSections || !book}
              placeholder={loadingSections ? t('oneNote.destination.loadingSections') : book ? t('oneNote.destination.sectionPlaceholder') : t('oneNote.destination.chooseNotebookFirst')}
              emptyMessage={t('oneNote.destination.sectionEmpty')} toggleLabel={t('oneNote.destination.showSections')}
              options={sections.map(s => ({ value: s.id, label: s.name }))} onChange={setSection} />
          </div>
          {sectionError && <div className="notice" role="alert"><p>{sectionError}</p><button disabled={saving || loadingSections} onClick={() => setSectionRetry(n => n + 1)}>{t('oneNote.destination.reloadSections')}</button></div>}
          {book && !loadingSections && !sectionError && !sections.length && <p className="quiet">{t('oneNote.destination.noSections')}</p>}
          {targets.domain && <><label className="checkbox destination-remember"><input type="checkbox" disabled={saving} checked={remember} onChange={e => setRemember(e.target.checked)} /><span>{t('oneNote.destination.rememberBefore')}<strong>{targets.domain}</strong>{t('oneNote.destination.rememberAfter')}</span></label><p className="quiet">{t('oneNote.destination.rememberHelp')}</p></>}
          {selectedBook && selectedSection && <div className="destination-preview"><span>{t('oneNote.destination.selectedLocation')}</span><strong>{selectedBook.label} › {selectedSection.name}</strong></div>}
        </>}
        <details className="destination-help" open={!loading && !targets ? true : undefined}>
          <summary>{t('oneNote.destination.noSuitableNotebook')}</summary>
          <p className="quiet">{t('oneNote.destination.noSuitableNotebookHelp')}</p>
          <div className="onenote-actions">
            <button disabled={loading || saving} onClick={() => void load()}>{t('oneNote.destination.reloadList')}</button>
            {(error || targets?.warning) && <button disabled={loading || saving} onClick={() => void load(true)}>{t('oneNote.destination.checkAccess')}</button>}
          </div>
        </details>
      </>
    </div>
    <footer className="destination-footer">
      <p className="quiet">{complete ? t('oneNote.destination.footerMove') : t('oneNote.destination.footerChoose')}</p>
      <div><button disabled={saving} onClick={onClose}>{t('oneNote.destination.cancel')}</button><button className="primary" disabled={!canFinish} onClick={() => void finish()}>{saving ? t('oneNote.status.pleaseWait') : complete ? t('oneNote.destination.move') : t('oneNote.destination.done')}</button></div>
    </footer>
  </dialog>
}

export function NotesOneNote({ id, value, complete, ended, savedLocally, revision, disabled, refresh, storage, documentName, onFolder }: {
  storage: string; documentName?: string; onFolder: () => void;
  id: string; value: OneNoteState; complete: boolean; ended: boolean; savedLocally: boolean; revision: number; disabled: boolean; refresh: () => void | Promise<void>
}) {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const folder = storage.split(/[\\/]/).filter(Boolean).pop() || t('oneNote.storage.defaultFolder')
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
  return <section className="notes-onenote" aria-label={t('oneNote.storage.label')}>
    <div className="onenote-heading"><div>{ended && <span className="destination-eyebrow">{t('oneNote.storage.eyebrow')}</span>}<strong>{!ended ? t('oneNote.storage.afterMeeting') : value.status === 'saved' ? t('oneNote.storage.savedToOneNote') : complete && savedLocally && value.mode === 'local' ? t('oneNote.storage.savedOnPc') : value.mode === 'onenote' ? 'OneNote' : t('oneNote.storage.onPc')}</strong></div>
      {value.status === 'saved' && <button onClick={() => void post(base + '/open').catch(e => setError(message(e)))}>{t('oneNote.storage.openOneNote')}</button>}
      {!locked && value.mode === 'onenote' && <button disabled={busy || disabled} onClick={start}>{value.target ? t('oneNote.storage.change') : t('oneNote.storage.chooseDestination')}</button>}</div>
    {!ended && !locked && <fieldset className="destination-methods" disabled={busy || disabled}><legend className="sr-only">{t('oneNote.storage.chooseLocation')}</legend>
      <label className={value.mode === 'local' ? 'selected' : ''}><input type="radio" name={`storage-${id}`} checked={value.mode === 'local'} onChange={() => void local()} /><span>{t('oneNote.storage.onPc')}</span></label>
      <label className={value.mode === 'onenote' ? 'selected' : ''}><input type="radio" name={`storage-${id}`} checked={value.mode === 'onenote'} onChange={start} /><span>OneNote</span></label>
    </fieldset>}
    {notice && <p className="copy-success" role="status">{notice}</p>}
    {value.mode === 'onenote' ? <>
      {value.target && <p className="onenote-target">{value.target.bookName} › {value.target.sectionName}</p>}
      {value.status === 'saved' ? <><p className="quiet" role="status">{oneNoteSavedLabel(value)}</p><p className="quiet">{t('oneNote.taskSync.autoApplied')}</p>
          {value.taskSync?.error && <p className="notes-error" role="alert">{value.taskSync.error}</p>}
          {['error', 'conflict', 'uncertain'].includes(value.taskSync?.status || '') && <button disabled={busy || disabled} onClick={() => void syncTasks()}>{busy ? t('oneNote.status.pleaseWait') : value.taskSync?.status === 'error' ? t('oneNote.taskSync.resend') : t('oneNote.taskSync.checkStatus')}</button>}
        </>
        : ['preparing', 'sending'].includes(value.status) || busy ? <p className="destination-loading" role="status"><span className="notes-spinner" />{value.status === 'uncertain' ? t('oneNote.status.checkingTransfer') : t('oneNote.status.saving')}</p>
        : value.status === 'uncertain' ? <p className="notice" role="status">{t('oneNote.status.unconfirmed')}</p>
        : <p className="quiet">{value.error ? t('oneNote.status.notYetSaved') : !value.target ? t('oneNote.status.chooseTarget') : complete ? t('oneNote.status.autoSaveStarting') : t('oneNote.status.savedAfterMeeting')}</p>}
      {value.reason && !locked && <p className="quiet">{value.reason}</p>}
      {!locked && value.target && value.error && <button className="primary" disabled={busy || disabled || !complete} onClick={() => void publish()}>{t('oneNote.status.retry')}</button>}
      {value.status === 'uncertain' && <button disabled={busy} onClick={() => void publish()}>{t('oneNote.status.checkStatus')}</button>}
      {value.status === 'uncertain' && <button disabled={busy} onClick={() => void post(base + '/open').catch(e => setError(message(e)))}>{t('oneNote.storage.openOneNote')}</button>}
      {ended && !locked && <button disabled={busy || disabled} onClick={() => void local()}>{t('oneNote.status.keepOnPc')}</button>}
    </> : <><p className="onenote-target">{t('oneNote.local.folder', { folder })}</p><p className="quiet">{complete ? savedLocally ? t('oneNote.local.canMove') : t('oneNote.local.unconfirmed') : t('oneNote.local.savedAfterMeeting')}</p>
      {documentName && savedLocally && <div className="storage-filename">{documentName}</div>}
      {ended && <div className="onenote-local-actions"><button disabled={busy || disabled || !complete} onClick={start}>{t('oneNote.local.moveToOneNote')}</button>{savedLocally && <button onClick={onFolder}>{t('oneNote.local.openFolder')}</button>}</div>}</>}
    {(value.notice || value.localNotice) && <p className="notice" role="status">{value.localNotice || value.notice}</p>}
    {(error || value.error) && <p className="notes-error" role="alert">{error || value.error}</p>}
    {open && !locked && <DestinationDialog base={base} value={value} complete={complete} disabled={disabled} onClose={() => setOpen(false)}
      onSaved={async () => { setOpen(false); await refresh(); setNotice(complete ? t('oneNote.storage.started') : t('oneNote.storage.applied')); }} />}
  </section>
}
