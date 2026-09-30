import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import './meeting-notes.css'
import { NotesOneNote, oneNoteSavedLabel, type OneNoteState } from './NotesOneNote'
import { NotesCalendar, type CalendarContext, type CalendarCandidate } from './NotesCalendar'

import { NotesTasks, unresolved, type Draft, type Person, type Task } from './NotesTasks'
import { appLanguage, setAppLanguage, t, uiLocale, type AppLanguage } from './i18n'
type Review = { displayTitle?: string; onenote?: OneNoteState; calendarContext?: CalendarContext | null; calendarSelected?: boolean; calendarAccessNeeded?: boolean; calendarNote?: string; calendarCandidates?: CalendarCandidate[]; peopleAccessNeeded?: boolean; people: Person[]; peopleNote: string; id: string; title: string; started: string; ended: string; status: string; error: string; warning: string;
  draft: Draft | null; tasksEditable?: boolean; busy: boolean; canSummarize: boolean; sourceCharacters: number; savedPath: string;
  documentName?: string; savedAt: string; storeError: string; revision: number; phase: string; editable: boolean; autoRetry: boolean }
type Screen = 'meeting' | 'settings' | 'history'
type NotesState = { health: string; autoStartSuppressed: boolean; storagePath: string; enabled: boolean;
  active: boolean; currentId: string | null; autoStart: boolean; error: string; uiView: Screen; uiRequest: number;
  options: Record<string, string | boolean>; reviews: Review[] }
type Device = { name: string; loopback: boolean; default: boolean }

async function request(url: string, body?: unknown) {
  const response = await fetch(url, body === undefined ? { cache: 'no-store' } : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error(t('Client nicht erreichbar. Änderungen bleiben in diesem Fenster erhalten.', 'Client not reachable. Changes are kept in this window.'))
  const data = await response.json()
  if (data.ok === false) throw new Error(data.error || t('Aktion fehlgeschlagen.', 'Action failed.'))
  return data
}
const post = (url: string, body: unknown = {}) => request(url, body)
const errorText = (e: unknown) => e instanceof Error ? e.message : t('Aktion fehlgeschlagen.', 'Action failed.')
// Manual start needs no Teams presence, so Zoom and other PC audio can be captured too.
const canStart = (state: NotesState) => state.enabled && !state.active && (state.options.managed === true
  ? state.options.setupComplete === true && state.options.onboardingComplete === true
  : state.options.hasSpeechKey === true && state.options.hasChatKey === true)

// Serialize explicit size changes so rapid toggles cannot finish out of order.
let windowFit = Promise.resolve()

type Edit = { operationId: string; tasks?: Record<string, Partial<Task>>; people?: Person[]; text?: Pick<Draft, 'summary' | 'decisions' | 'openQuestions'> }
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)
function overlay(draft: Draft, patch: Edit): Draft {
  const people = new Map(draft.people.map(p => [p.id, p])); patch.people?.forEach(p => people.set(p.id, p))
  return { ...draft, ...patch.text, people: [...people.values()], tasks: draft.tasks.map(t => ({ ...t, ...patch.tasks?.[t.id] })) }
}
function useDraft(review: Review) {
  const [draft, setDraft] = useState(review.draft), [saving, setSaving] = useState(false)
  const [error, setError] = useState(''), [savedAt, setSavedAt] = useState(review.savedAt)
  const latest = useRef(review.draft), revision = useRef(review.revision)
  const queue = useRef<Edit[]>([]), working = useRef(false), blocked = useRef(false), alive = useRef(true)
  async function flush() {
    if (working.current || blocked.current) return
    working.current = true; setSaving(true)
    while (queue.current.length && !blocked.current) {
      const patch = queue.current[0]
      try {
        const data = await post(`/api/notes/${review.id}/edit`, patch)
        queue.current.shift(); revision.current = data.revision
        latest.current = queue.current.reduce(overlay, data.draft as Draft)
        if (alive.current) { setDraft(latest.current); setSavedAt(data.savedAt); setError(data.storeError || '') }
      } catch (e) {
        blocked.current = true
        if (alive.current) setError(errorText(e))
      }
    }
    working.current = false
    if (alive.current) setSaving(false)
  }
  function change(update: Draft | ((current: Draft) => Draft)) {
    if (!latest.current) return
    const previous = latest.current, value = typeof update === 'function' ? update(previous) : update
    const tasks: Record<string, Partial<Task>> = {}
    for (const task of value.tasks) {
      const old = previous.tasks.find(t => t.id === task.id)
      if (!old) continue
      const fields = ['title','owner','ownerId','recipient','due','questions','included'] as const
      const changed = Object.fromEntries(fields.filter(k => !same(old[k], task[k])).map(k => [k, task[k]]))
      if (Object.keys(changed).length) tasks[task.id] = changed
    }
    const patch: Edit = { operationId: crypto.randomUUID() }
    if (Object.keys(tasks).length) patch.tasks = tasks
    const added = value.people.filter(p => !previous.people.some(v => same(v, p)))
    if (added.length) patch.people = added
    if (['summary','decisions','openQuestions'].some(k => previous[k as keyof Draft] !== value[k as keyof Draft]))
      patch.text = { summary: value.summary, decisions: value.decisions, openQuestions: value.openQuestions }
    if (Object.keys(patch).length === 1) return
    latest.current = value; setDraft(value)
    // Coalesce unsent keystrokes; never mutate an in-flight/retried operation.
    if (queue.current.length > 1) {
      const tail = queue.current[queue.current.length - 1]
      if (patch.text) tail.text = patch.text
      for (const [id, fields] of Object.entries(patch.tasks || {})) {
        tail.tasks ||= {}; tail.tasks[id] = { ...tail.tasks[id], ...fields }
      }
      if (patch.people) tail.people = [...new Map([...(tail.people || []), ...patch.people].map(p => [p.id, p])).values()]
    } else queue.current.push(patch)
    void flush()
  }
  useEffect(() => {
    alive.current = true
    const guard = (e: BeforeUnloadEvent) => {
      if (queue.current.length) { e.preventDefault(); e.returnValue = '' }
    }
    window.addEventListener('beforeunload', guard)
    return () => { alive.current = false; window.removeEventListener('beforeunload', guard) }
  }, [])
  useEffect(() => {
    if (!working.current && !queue.current.length && review.revision >= revision.current) {
      revision.current = review.revision; latest.current = review.draft; setDraft(review.draft); setSavedAt(review.savedAt); setError(review.storeError)
    }
  }, [review.revision, review.savedAt, review.storeError])
  return { draft, change, saving, error, savedAt, blocked: blocked.current, retry: () => { blocked.current = false; void flush() } }
}

// Set by MeetingNotes; lets the always-visible navigation controls act without prop drilling.
let refreshNotes: () => Promise<void> = async () => {}
let openSettings: (() => void) | null = null

function SettingsButton() {
  if (!openSettings) return null
  const label = t('Einstellungen', 'Settings')
  return <button className="settings-button" title={label} aria-label={label} onClick={openSettings}>
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </svg>
  </button>
}

function FlagDE() {
  return <svg viewBox="0 0 5 3" aria-hidden="true" focusable="false"><path fill="#000" d="M0 0h5v1H0z" /><path fill="#dd0000" d="M0 1h5v1H0z" /><path fill="#ffce00" d="M0 2h5v1H0z" /></svg>
}
function FlagGB() {
  const id = useId()
  return <svg viewBox="0 0 60 30" aria-hidden="true" focusable="false">
    <clipPath id={id}><path d="M30 15h30v15zv15H0zH0V0zV0h30z" /></clipPath>
    <path fill="#012169" d="M0 0h60v30H0z" />
    <path stroke="#fff" strokeWidth="6" d="M0 0l60 30m0-30L0 30" />
    <path stroke="#c8102e" strokeWidth="4" clipPath={`url(#${id})`} d="M0 0l60 30m0-30L0 30" />
    <path stroke="#fff" strokeWidth="10" d="M30 0v30M0 15h60" />
    <path stroke="#c8102e" strokeWidth="6" d="M30 0v30M0 15h60" />
  </svg>
}
// Language names stay in their own language, so the switch is readable in either UI language.
const LANGUAGES = [['de', 'Deutsch', FlagDE], ['en', 'English', FlagGB]] as const
function LanguageSwitch() {
  const [busy, setBusy] = useState(false), [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const outside = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', outside); document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('mousedown', outside); document.removeEventListener('keydown', escape) }
  }, [open])
  async function choose(language: AppLanguage) {
    setOpen(false)
    if (language === appLanguage()) return
    setBusy(true)
    try { await post('/api/notes/ui-language', { language }); await refreshNotes() } catch { /* The next poll shows the saved language. */ }
    finally { setBusy(false) }
  }
  const [, currentLabel, CurrentFlag] = LANGUAGES.find(([value]) => value === appLanguage()) ?? LANGUAGES[0]
  const label = t('App-Sprache', 'App language')
  return <div className="language-switch" ref={root}>
    <button className="language-toggle" title={`${label}: ${currentLabel}`} aria-label={`${label}: ${currentLabel}`} aria-haspopup="menu" aria-expanded={open} disabled={busy} onClick={() => setOpen(value => !value)}>
      <CurrentFlag /><svg className="language-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M6 9l6 6 6-6" /></svg>
    </button>
    {/* Language names stay in their own language, so the choice is readable in either UI language. */}
    {open && <div className="language-menu" role="menu" aria-label={label}>
      {LANGUAGES.map(([value, name, Flag]) => <button key={value} role="menuitemradio" aria-checked={appLanguage() === value} onClick={() => void choose(value)}><Flag /><span>{name}</span></button>)}
    </div>}
  </div>
}

function Popup({ children, storage, saved, error, onFolder, onHistory, documentName, oneNote, onOneNote, footer, onClose, closeDisabled, expanded = false }: {
  footer?: ReactNode; expanded?: boolean;
  onClose?: () => void; closeDisabled?: boolean;
  oneNote?: OneNoteState; onOneNote?: () => void; children: ReactNode; storage: string; saved: string; error?: boolean; onFolder: () => void; onHistory?: () => void; documentName?: string
}) {
  useEffect(() => {
    windowFit = windowFit.then(() => post('/api/notes/fit', { height: expanded ? 960 : 700, expanded })).then(() => {}, () => {})
  }, [expanded])
  const folder = storage.split(/[\\/]/).filter(Boolean).pop() || 'Meeting-Notizen'
  return <main className={'meeting-notes' + (expanded ? ' summary-expanded' : '')}>
    <nav className="notes-navigation" aria-label={t('Meeting-Navigation', 'Meeting navigation')}><LanguageSwitch />{onHistory && <button onClick={onHistory}>{t('Alle Meetings', 'All meetings')}</button>}<SettingsButton /></nav>
    <div className="notes-scroll"><div className="notes-content">{children}</div></div>
    <footer className="notes-footer">{footer !== undefined ? footer : <>
      <div className="storage-copy"><div className={error ? 'notes-error' : 'storage-status'} role="status">{saved}</div>
        <div className="storage-location">{oneNote?.mode === 'onenote' ? `OneNote · ${oneNote.target?.bookName || t('Ziel noch wählen', 'Choose destination')}` : t(`Auf diesem PC · Ordner „${folder}“`, `On this PC · Folder “${folder}”`)}</div>
        {documentName && oneNote?.mode !== 'onenote' && <div className="storage-filename" title={documentName}>{documentName}</div>}
      </div>
      <div className="onenote-actions">{oneNote?.mode === 'onenote' ? oneNote.status === 'saved' && <button onClick={onOneNote}>{t('OneNote öffnen', 'Open OneNote')}</button> : <button onClick={onFolder}>{t('Ordner öffnen', 'Open folder')}</button>}
        {onClose && <button className="primary" disabled={closeDisabled} onClick={onClose}>{t('Meeting-Fenster schließen', 'Close meeting window')}</button>}</div>
    </>}</footer>
  </main>
}

function elapsed(started: string) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(started).getTime()) / 1000))
  return `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`
}

function documentText(d: Draft) {
  return [d.summary, d.decisions && 'Entscheidungen\n' + d.decisions, d.openQuestions && 'Offene Fragen\n' + d.openQuestions].filter(Boolean).join('\n\n')
}
function Spinner() { return <span className="notes-spinner" aria-hidden="true" /> }
function ResizeIcon({ expanded }: { expanded: boolean }) {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {expanded ? <path d="M4 4l6 6m0-6v6H4m16 10l-6-6m0 6v-6h6" /> : <path d="M10 10L4 4m0 6V4h6m4 10l6 6m0-6v6h-6" />}
  </svg>
}
// Copy progress markers; translated at render time, so a language switch mid-copy stays consistent.
const copying = '\u0000copying', copied = '\u0000copied'
function ReviewPanel({ review, visible, current, state, history, configure, refresh, connectionError }: {
  review: Review; visible: boolean; current: boolean; state: NotesState; history: () => void;
  configure: () => void; refresh: () => void; connectionError: string
}) {
  // Remains mounted across navigation. Stable task IDs preserve active editors.
  const edit = useDraft(review)
  const [expanded, setExpanded] = useState(false)
  const [copyState, setCopyState] = useState(''), [copyError, setCopyError] = useState(false)
  const [message, setMessage] = useState(''), [actionBusy, setActionBusy] = useState(false)
  if (!visible) return null
  const oneNote = review.onenote || { mode: 'local', status: 'ready' } as OneNoteState
  const published = oneNote.status === 'saved'
  const d = edit.draft, live = state.currentId === review.id && state.active && !review.ended
  const finalizing = !!review.ended && review.busy
  const complete = review.phase === 'complete' && !live && !review.busy
  const issue = connectionError || review.error || review.warning || (current && state.error) || ''
  const saveError = edit.error || review.storeError
  const count = d?.tasks.filter(unresolved).length || 0
  const selected = d?.tasks.filter(t => t.included !== false).length || 0
  const title = connectionError ? t('Client nicht erreichbar', 'Client not reachable') : live ? t('Meeting läuft', 'Meeting in progress') : finalizing ? t('Verarbeitung läuft …', 'Processing …') : complete ? t('Notizen fertig', 'Notes ready') : t('Abschluss unvollständig', 'Completion incomplete')
  const statusError = !!connectionError || (!live && !finalizing && !complete)
  const saved = !saveError && oneNote.mode === 'onenote' ? published ? oneNoteSavedLabel(oneNote) : ['preparing', 'sending'].includes(oneNote.status) ? t('Wird in OneNote gespeichert …', 'Saving to OneNote …') : oneNote.status === 'uncertain' ? t('Lokal gesichert · OneNote noch nicht bestätigt', 'Saved locally · OneNote not yet confirmed') : oneNote.error ? t('Lokal gesichert · Noch nicht in OneNote gespeichert', 'Saved locally · Not yet saved to OneNote') : complete ? t('Automatische OneNote-Ablage wird gestartet …', 'Starting automatic save to OneNote …') : t('Entwurf gesichert · OneNote-Ablage nach Meeting-Ende', 'Draft saved · Saved to OneNote after the meeting') : saveError ? t('Noch nicht gespeichert', 'Not saved yet') : edit.saving ? t('Änderungen werden gespeichert …', 'Saving changes …') : !d ? t('Notizen werden automatisch gespeichert', 'Notes are saved automatically') : live || finalizing ? t('✓ Vorläufige Notizen gespeichert', '✓ Preliminary notes saved') : t('✓ Auf diesem PC gespeichert', '✓ Saved on this PC')
  const savedTime = edit.savedAt ? new Date(edit.savedAt).toLocaleTimeString(uiLocale(), { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''
  const saveLabel = saveError ? t('Nicht gespeichert', 'Not saved') : edit.saving ? t('Speichert …', 'Saving …') : savedTime ? t(`Gespeichert um ${savedTime}`, `Saved at ${savedTime}`) : t('Wird automatisch gespeichert', 'Saved automatically')
  const summaryStatus = published ? t('Abgelegter Stand · Weitere Änderungen direkt in OneNote', 'Saved version · Make further changes directly in OneNote') : (issue && !complete) ? t('Verarbeitung prüfen', 'Check processing') : finalizing ? t('Finale Zusammenfassung wird erstellt …', 'Creating final summary …') : d ? complete ? t('Text direkt bearbeiten. Jede Änderung wird automatisch gespeichert.', 'Edit the text directly. Every change is saved automatically.') : t('Wird laufend aktualisiert. Nach Abschluss bearbeitbar.', 'Updated continuously. Editable once complete.') : live ? t('Gespräch wird verarbeitet …', 'Processing conversation …') : t('Keine Zusammenfassung verfügbar.', 'No summary available.')
  async function action(url: string) {
    setActionBusy(true); setMessage('')
    try { await post(url); refresh() } catch (e) { setMessage(errorText(e)) }
    finally { setActionBusy(false) }
  }
  async function copyNotes() {
    setCopyState(copying); setCopyError(false)
    try { await post(`/api/notes/${review.id}/copy`); setCopyState(copied) }
    catch (e) { setCopyState(errorText(e)); setCopyError(true) }
  }
  const notices = <>
    {connectionError && <div className="notice" role="alert">{t('Der aktuelle Stand kann gerade nicht bestätigt werden. Die Verbindung wird automatisch erneut geprüft.', 'The current state cannot be confirmed right now. The connection is checked again automatically.')}</div>}
    {!!saveError && <div className="notice" role="alert"><strong>{t('Notizen noch nicht gespeichert.', 'Notes not saved yet.')}</strong><p>{saveError}</p>{edit.blocked && <button onClick={edit.retry}>{t('Erneut versuchen', 'Try again')}</button>}</div>}
    {(review.error || review.warning || message) && <div className="notice" role="alert"><p>{message || review.error || review.warning}</p>
      {review.autoRetry ? <p>{t('Ein neuer Versuch startet automatisch.', 'A new attempt starts automatically.')}</p> : review.canSummarize && <button disabled={review.busy} onClick={() => void action(`/api/notes/${review.id}/summarize`)}>{t('Erneut versuchen', 'Try again')}</button>}</div>}
    {current && state.error && <div className="notice" role="alert"><p>{state.error}</p><button onClick={configure}>{t('Einstellungen öffnen', 'Open settings')}</button></div>}
    {current && state.health !== 'ok' && !connectionError && <div className="notice" role="alert"><p>{t('Der Teams-Status ist nicht erreichbar. Das Anrufende kann nicht zuverlässig erkannt werden.', 'Teams status is not reachable. The end of the call cannot be detected reliably.')}</p>{state.health === 'auth' && <button onClick={() => void action('/api/sign-in')}>{t('Mit Microsoft anmelden', 'Sign in with Microsoft')}</button>}</div>}
  </>
  return <Popup expanded={expanded} storage={state.storagePath} saved={saved} onHistory={history} onFolder={() => void action('/api/notes/open-folder')}
    footer={<>
      <span className={saveError ? 'notes-error' : 'quiet'} role="status">{saveError ? t('Notizen noch nicht gespeichert.', 'Notes not saved yet.') : edit.saving ? t('Änderungen werden gespeichert …', 'Saving changes …') : live ? t('Aufnahme läuft beim Schließen im Hintergrund weiter.', 'Recording continues in the background when you close.') : finalizing ? t('Verarbeitung läuft beim Schließen im Hintergrund weiter.', 'Processing continues in the background when you close.') : saved}</span>
      <button className="primary" disabled={edit.saving || edit.blocked} onClick={() => void action('/api/notes/close')}>{t('Meeting-Fenster schließen', 'Close meeting window')}</button>
    </>}>
    <div className="notes-status"><span className={'status-label' + (statusError ? ' notes-error' : '')}><span className="status-dot" />{title}</span>
      {live && <><span className="notes-time">{elapsed(review.started)}</span><button disabled={actionBusy} onClick={() => void action('/api/stop')}>{t('Stoppen', 'Stop')}</button></>}
      {current && !live && canStart(state) && <button className="notes-start" disabled={actionBusy} onClick={() => void action('/api/start')}>{t('Neues Meeting starten', 'Start new meeting')}</button>}
    </div>
    <h1>{review.displayTitle || review.title}</h1><div className="meeting-date">{t('Aufzeichnung:', 'Recording:')} {new Date(review.started).toLocaleString(uiLocale(), { dateStyle: 'medium', timeStyle: 'short' })}</div>
    <NotesOneNote id={review.id} value={oneNote} complete={complete} ended={!!review.ended} savedLocally={!!review.savedPath && !saveError} revision={review.revision} disabled={edit.saving || edit.blocked || !!saveError || !!connectionError} refresh={refresh}
      storage={state.storagePath} documentName={review.documentName} onFolder={() => void action('/api/notes/open-folder')} />
    <NotesCalendar key={review.id} id={review.id} selected={review.calendarSelected} context={review.calendarContext}
      candidates={review.calendarCandidates} accessNeeded={review.calendarAccessNeeded} note={review.calendarNote}
      locked={['preparing', 'sending', 'uncertain', 'saved'].includes(oneNote.status)} refresh={refresh} />
    {notices}
    <div>
      <section className="summary-card" aria-label={t('Zusammenfassung', 'Summary')}>
        <div className="summary-heading"><div className="summary-heading-copy"><h2>{live ? t('Laufende Zusammenfassung', 'Live summary') : t('Zusammenfassung', 'Summary')}</h2>{d && (review.editable ? <span className={saveError ? 'notes-error inline-save' : 'inline-save'} role="status">{saveLabel}</span> : <span className="provisional">{published ? t('In OneNote', 'In OneNote') : complete ? t('Übertragung', 'Transferring') : t('Vorläufig', 'Preliminary')}</span>)}</div>
          {d && <button className="summary-size-toggle" aria-expanded={expanded} aria-controls={`summary-${review.id}`} onClick={() => setExpanded(value => !value)}><ResizeIcon expanded={expanded} />{expanded ? t('Verkleinern', 'Collapse') : t('Vergrößern', 'Expand')}</button>}
        </div>
        <div className="summary-state" role="status">{!issue && (finalizing || (!d && live)) && <Spinner />}{summaryStatus}</div>
        <div className="summary-body" id={`summary-${review.id}`}>{d ? (review.editable ? <textarea className="summary-inline-editor" aria-label={t('Zusammenfassung direkt bearbeiten', 'Edit summary directly')} value={documentText(d)} maxLength={64000} onChange={e => edit.change(current => ({ ...current, summary: e.target.value, decisions: '', openQuestions: '' }))} /> : <p className="notes-summary">{documentText(d)}</p>) : <p className="quiet">{live ? t('Die Zusammenfassung erscheint hier automatisch.', 'The summary appears here automatically.') : finalizing ? t('Es liegt noch kein Zwischenstand vor. Bitte warten Sie auf den Abschluss.', 'No interim result yet. Please wait for processing to finish.') : t('Vorhandene Aufgabenvorschläge bleiben erhalten.', 'Existing suggested tasks are kept.')}</p>}</div>
      </section>
      <div className="tasks-heading"><h2>{t('Aufgabenvorschläge', 'Suggested tasks')}</h2>{d && !!d.tasks.length && <span>{t(`${selected} von ${d.tasks.length} ausgewählt`, `${selected} of ${d.tasks.length} selected`)}</span>}</div>
      {!d?.tasks.length && <p className="quiet">{live || finalizing ? t('Noch keine Vorschläge. Vereinbarte Aufgaben erscheinen hier automatisch.', 'No suggestions yet. Agreed tasks appear here automatically.') : t('Keine Nacharbeit vereinbart.', 'No follow-up agreed.')}</p>}
      {!!d?.tasks.length && <p className="quiet tasks-help">{published ? t('Verantwortliche und Angaben können Sie hier ergänzen. Änderungen werden automatisch in OneNote gespeichert; abhaken können Sie die Aufgaben dort.', 'You can add owners and details here. Changes are saved to OneNote automatically; you can tick off the tasks there.') : t('Ausgewählte Aufgaben bleiben in Ihrer Aufgabenliste. Sie können Vorschläge abwählen.', 'Selected tasks stay in your task list. You can deselect suggestions.')}</p>}
      {complete && count > 0 && <p className="task-attention">{t(`Bei ${count === 1 ? 'einer ausgewählten Aufgabe fehlen' : `${count} ausgewählten Aufgaben fehlen`} noch Angaben.`, count === 1 ? 'One selected task is still missing details.' : `${count} selected tasks are still missing details.`)}</p>}
      {d && <NotesTasks draft={d} people={review.people || []} peopleNote={review.peopleNote || ''} editable={review.tasksEditable ?? true} expanded change={edit.change} />}
      {!!d?.tasks.length && !published && <button disabled={actionBusy} onClick={() => void action(`/api/notes/${review.id}/people-refresh`)}>{t('Personen erneut laden', 'Reload people')}</button>}
      {!!d?.tasks.length && !published && review.peopleAccessNeeded && <div className="notice"><p>{t('Teilnehmer fehlen in der Auswahl? Mit Teams-Zugriff kann der Client Anrufereignisse und Namen ermitteln. Microsoft verlangt dafür Leserechte auf Ihre Chats.', 'Participants missing from the list? With Teams access, the client can identify call events and names. Microsoft requires read access to your chats for this.')}</p><button disabled={actionBusy} onClick={() => void action(`/api/notes/${review.id}/people-connect`)}>{t('Teams-Personen verbinden', 'Connect Teams people')}</button></div>}
      {d && <div className="notes-copy"><button disabled={edit.saving || edit.blocked || copyState === copying} onClick={() => void copyNotes()}>{t('Notizen kopieren', 'Copy notes')}</button>
        <span className="quiet">{t('Zusammenfassung und ausgewählte Aufgaben', 'Summary and selected tasks')}</span>
        {copyState && <p className={copyError ? 'notes-error' : 'copy-success'} role={copyError ? 'alert' : 'status'}>{copyState === copying ? t('Wird kopiert …', 'Copying …') : copyState === copied ? t('Kopiert · Notizen und ausgewählte Aufgaben sind in der Zwischenablage.', 'Copied · Notes and selected tasks are on the clipboard.') : copyState}</p>}</div>}
    </div>
  </Popup>
}

type SettingsProps = { state: NotesState; back: () => void; refresh: () => void; history: () => void }

function MeetingLanguageSelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return <label>{t('Meeting-Sprache', 'Meeting language')}<select aria-label={t('Meeting-Sprache', 'Meeting language')} value={value} onChange={e => onChange(e.target.value)}>
    <option value="de-DE">{t('Deutsch', 'German')}</option><option value="en-US">{t('Englisch', 'English')}</option><option value="fr-FR">{t('Französisch', 'French')}</option>
    {!['de-DE', 'en-US', 'fr-FR'].includes(value) && <option value={value}>{value}</option>}
  </select><span className="quiet">{t('Gesprochene Sprache für die Spracherkennung', 'Spoken language for speech recognition')}</span></label>
}

function Settings(props: SettingsProps) {
  return props.state.options.managed === true ? <ManagedSettings {...props} /> : <CommunitySettings {...props} />
}

function ManagedSettings({ state, refresh, history }: SettingsProps) {
  const signedIn = state.options.setupComplete === true
  const finished = state.options.onboardingComplete === true
  const [form, setForm] = useState(() => ({
    language: String(state.options.language || 'de-DE'), mic: String(state.options.mic || ''),
    loopback: String(state.options.loopback || ''), autoStart: state.autoStart,
  }))
  const [devices, setDevices] = useState<Device[]>([])
  const [error, setError] = useState('')
  const [pending, setPending] = useState<'signin' | 'save' | null>(null)
  const busy = pending !== null || state.options.setupBusy === true
  async function loadDevices() {
    try { setDevices((await request('/api/notes/devices')).devices) }
    catch (e) { setError(errorText(e)) }
  }
  async function signIn() {
    setPending('signin'); setError('')
    try { await post('/api/notes/connect'); await refresh() }
    catch (e) { setError(errorText(e)); refresh() }
    finally { setPending(null) }
  }
  async function finish() {
    setPending('save'); setError('')
    try {
      await post(state.active ? '/api/notes/close' : '/api/notes/finish-setup',
        state.active ? {} : { enabled: state.enabled, ...form })
      refresh()
    } catch (e) { setError(errorText(e)) }
    finally { setPending(null) }
  }
  return <Popup storage={state.storagePath} saved="" onFolder={() => {}}
    onHistory={finished && signedIn && !busy ? history : undefined}
    footer={<>
      <p className="quiet setup-footer-copy">{signedIn
        ? t('Das Fenster wird geschlossen. Die App läuft im Infobereich neben der Windows-Uhr weiter.', 'The window closes. The app keeps running in the notification area next to the Windows clock.')
        : t('Erst nach der Anmeldung und „Fertig“ können Meeting-Notizen starten.', 'Meeting notes can start only after you sign in and click “Done”.')}</p>
      {signedIn && <button className="primary" disabled={busy} onClick={() => void finish()}>
        {pending === 'save' ? t('Bitte warten …', 'Please wait …') : state.active ? t('Schließen', 'Close') : t('Fertig', 'Done')}
      </button>}
    </>}>
    <p className="setup-step">{finished ? 'DAS Meeting Assistant' : signedIn ? t('Schritt 2 von 2 · Abschließen', 'Step 2 of 2 · Finish') : t('Schritt 1 von 2 · Anmelden', 'Step 1 of 2 · Sign in')}</p>
    <h1>{signedIn ? finished ? t('Einstellungen', 'Settings') : t('Anmeldung erfolgreich', 'Signed in successfully') : t('Willkommen bei DAS Meeting Assistant', 'Welcome to DAS Meeting Assistant')}</h1>
    {error && <p className="notice" role="alert">{error}</p>}
    {!signedIn ? <>
      <p>{t('Melden Sie sich mit Ihrem Microsoft-Firmenkonto an, um DAS Meeting Assistant zu nutzen. Die Anmeldung ist erforderlich.', 'Sign in with your Microsoft work account to use DAS Meeting Assistant. Signing in is required.')}</p>
      <p className="quiet">{t('Die Microsoft-Anmeldung öffnet sich bei Bedarf im Browser. Kehren Sie danach zu diesem Fenster zurück.', 'Microsoft sign-in opens in your browser if needed. Return to this window afterwards.')}</p>
      <button className="primary" disabled={busy} onClick={() => void signIn()}>{busy ? t('Bitte warten …', 'Please wait …') : t('Mit Microsoft anmelden', 'Sign in with Microsoft')}</button>
      {busy && <p className="setup-progress" role="status"><Spinner /> {t('Anmeldung und DAS-Zugang werden geprüft …', 'Checking sign-in and DAS access …')}</p>}
      <p className="quiet">{t('Wenn Sie dieses Fenster jetzt schließen, bleibt die Einrichtung unvollständig. Sie können sie über das App-Symbol neben der Windows-Uhr fortsetzen.', 'If you close this window now, setup stays incomplete. You can continue it from the app icon next to the Windows clock.')}</p>
    </> : <>
      <p className="setup-success" role="status">{t('✓ Microsoft-Anmeldung und DAS-Zugang sind bereit.', '✓ Microsoft sign-in and DAS access are ready.')}</p>
      {state.active ? <p className="notice">{t('Ein Meeting läuft. Einstellungen können danach geändert werden. Sie können dieses Fenster schließen; die Erfassung läuft weiter.', 'A meeting is in progress. Settings can be changed afterwards. You can close this window; capture continues.')}</p>
        : <p>{t('Sie können die Einstellungen beibehalten und direkt auf „Fertig“ klicken.', 'You can keep the settings and click “Done” right away.')}</p>}
      <p className="quiet">{form.autoStart ? t('Meeting-Notizen starten bei erkannten Teams-Anrufen automatisch.', 'Meeting notes start automatically when a Teams call is detected.') : t('Der automatische Start ist ausgeschaltet. Sie können ihn unter „Einstellungen anpassen“ einschalten.', 'Automatic start is off. You can turn it on under “Adjust settings”.')}</p>
      <details className="setup-options" onToggle={e => { if (e.currentTarget.open) void loadDevices() }}>
        <summary>{t('Einstellungen anpassen', 'Adjust settings')} <span>{t('optional', 'optional')}</span></summary>
        <fieldset disabled={state.active || busy}>
          <label className="checkbox"><input type="checkbox" checked={form.autoStart} onChange={e => setForm({ ...form, autoStart: e.target.checked })} />{t('Bei Teams-Anrufen automatisch starten', 'Start automatically for Teams calls')}</label>
          {(['mic', 'loopback'] as const).map(source => <label key={source}>{source === 'mic' ? t('Mikrofon', 'Microphone') : t('Teams-Wiedergabe / Headset', 'Teams playback / headset')}
            <select aria-label={source === 'mic' ? t('Mikrofon', 'Microphone') : t('Teams-Wiedergabe / Headset', 'Teams playback / headset')} value={form[source]} onChange={e => setForm({ ...form, [source]: e.target.value })}>
              <option value="">{t('Windows-Standardgerät', 'Windows default device')}</option>
              {form[source] && !devices.some(d => d.loopback === (source === 'loopback') && d.name === form[source]) && <option value={form[source]}>{form[source]}{t(' (aktuell nicht verfügbar)', ' (currently unavailable)')}</option>}
              {devices.filter(d => d.loopback === (source === 'loopback')).map((d, i) => <option value={d.name} key={i}>{d.name}</option>)}
            </select></label>)}
          <button onClick={() => void loadDevices()}>{t('Geräte neu laden', 'Reload devices')}</button>
          <MeetingLanguageSelect value={form.language} onChange={language => setForm({ ...form, language })} />
          <button onClick={() => void signIn()}>{t('DAS-Verbindung erneut prüfen', 'Check DAS connection again')}</button>
          <p className="quiet">{t('DAS stellt die Sprachverarbeitung und Zusammenfassungen bereit. Audio und Transkript werden vom Client nicht als Dateien gespeichert.', 'DAS provides speech processing and summaries. The client does not save audio or transcripts as files.')}</p>
        </fieldset>
      </details>
      <p className="quiet">{t('Einstellungen können Sie später über das App-Symbol neben der Windows-Uhr wieder öffnen.', 'You can reopen settings later from the app icon next to the Windows clock.')}</p>
    </>}
  </Popup>
}

function CommunitySettings({ state, back, refresh, history }: SettingsProps) {
  const [form, setForm] = useState<Record<string, string>>(() => ({ region: 'westeurope', language: 'de-DE', mic: '', loopback: '', endpoint: '', model: '',
    ...Object.fromEntries(Object.entries(state.options).filter(([k, v]) => ['region','language','mic','loopback','endpoint','model'].includes(k) && typeof v === 'string')), speechKey: '', chatKey: '' }) as Record<string, string>)
  const [enabled, setEnabled] = useState(state.enabled)
  const [devices, setDevices] = useState<Device[]>([])
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  async function load() { try { setDevices((await request('/api/notes/devices')).devices) } catch (e) { setMessage(errorText(e)) } }
  useEffect(() => { void load() }, [])
  async function configure() {
    setBusy(true)
    try { await post('/api/notes/configure', { enabled, ...form }); refresh(); back() }
    catch (e) { setMessage(errorText(e)) } finally { setBusy(false) }
  }
  return <Popup storage={state.storagePath} onHistory={history} saved={t('Speicherort für Meeting-Notizen', 'Storage location for meeting notes')} onFolder={() => void post('/api/notes/open-folder').catch(e => setMessage(errorText(e)))}>
    <button className="back" onClick={back}>{t('Zurück zum Meeting', 'Back to meeting')}</button><h1>{t('Einstellungen', 'Settings')}</h1>
    {(message || state.options.credentialError) && <p className="notice" role="alert">{message || state.options.credentialError}</p>}
    {state.active && <p className="notice">{t('Geräte und Zugänge können nach dem Meeting geändert werden.', 'Devices and access can be changed after the meeting.')}</p>}
    <button disabled={busy} onClick={() => post(`/api/auto-start/${state.autoStart ? 'off' : 'on'}`).then(refresh).catch(e => setMessage(errorText(e)))}>{state.autoStart ? t('Automatischen Start ausschalten', 'Turn off automatic start') : t('Automatischen Start einschalten', 'Turn on automatic start')}</button>
    <fieldset disabled={state.active || busy}>
      <label className="checkbox"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />{t('Meeting-Notizen statt Dateiaufzeichnung', 'Meeting notes instead of file recording')}</label>
      {(['mic', 'loopback'] as const).map(source => <label key={source}>{source === 'mic' ? t('Mikrofon', 'Microphone') : t('Teams-Wiedergabe / Headset', 'Teams playback / headset')}
        <select value={form[source]} onChange={e => setForm({ ...form, [source]: e.target.value })}><option value="">{t('Windows-Standardgerät', 'Windows default device')}</option>
          {devices.filter(d => d.loopback === (source === 'loopback')).map((d, i) => <option value={d.name} key={i}>{d.name}</option>)}
        </select></label>)}
      <button onClick={load}>{t('Geräte neu laden', 'Reload devices')}</button>
      <MeetingLanguageSelect value={form.language} onChange={language => setForm({ ...form, language })} />
      <h2>{t('Azure-Verbindungen', 'Azure connections')}</h2>
      {(['region', 'speechKey', 'endpoint', 'model', 'chatKey'] as const).map(key => <label key={key}>{{region:t('Speech-Region', 'Speech region'),speechKey:t('Speech-Schlüssel', 'Speech key'),endpoint:t('Textmodell-Endpunkt', 'Text model endpoint'),model:t('Deployment', 'Deployment'),chatKey:t('Textmodell-Schlüssel', 'Text model key')}[key]}
        <input type={key.endsWith('Key') ? 'password' : 'text'} autoComplete="off" value={form[key]} maxLength={2048}
          placeholder={key.endsWith('Key') && state.options[key === 'speechKey' ? 'hasSpeechKey' : 'hasChatKey'] ? state.options[key + 'Saved'] ? t('Verschlüsselt gespeichert · leer lassen zum Beibehalten', 'Saved encrypted · leave empty to keep') : t('Für diesen Lauf geladen · mit Einstellungen speichern', 'Loaded for this session · save with settings') : ''}
          onChange={e => setForm({ ...form, [key]: e.target.value })} />
      </label>)}
      {<div className="notes-more">{(['speechKey', 'chatKey'] as const).map(key => state.options[key === 'speechKey' ? 'hasSpeechKey' : 'hasChatKey'] && <button key={key} onClick={async () => {
        setBusy(true)
        try { await post('/api/notes/credentials/remove', { key }); setForm(f => ({ ...f, [key]: '' })); refresh(); setMessage(t('Schlüssel entfernt.', 'Key removed.')) }
        catch (e) { setMessage(errorText(e)) } finally { setBusy(false) }
      }}>{key === 'speechKey' ? t('Speech-Schlüssel entfernen', 'Remove speech key') : t('Textmodell-Schlüssel entfernen', 'Remove text model key')}</button>)}</div>}
      <p className="quiet">{t('Schlüssel werden mit Windows verschlüsselt im eigenen Benutzerprofil gespeichert und beim Start geladen. Zwischenstände und Abschlussnotizen werden direkt in Azure erstellt. Audio und Transkript werden nicht als Dateien gespeichert.', 'Keys are encrypted with Windows, saved in your own user profile and loaded at startup. Interim results and final notes are created directly in Azure. Audio and transcripts are not saved as files.')}</p>
      <button className="primary" onClick={configure}>{t('Einstellungen übernehmen', 'Apply settings')}</button>
    </fieldset>
  </Popup>
}

export function MeetingNotes() {
  const [state, setState] = useState<NotesState | null>(null)
  const [screen, setScreen] = useState<Screen>('meeting')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [connectionError, setConnectionError] = useState('')
  const sequence = useRef(-1)
  const mounted = useRef(true)
  const fetching = useRef(false)
  async function refresh() {
    if (fetching.current) return
    fetching.current = true
    try {
      const data: NotesState = await request('/api/notes')
      if (!mounted.current) return
      setState(data)
      setConnectionError('')
      if ((data.uiRequest ?? 0) !== sequence.current) {
        sequence.current = data.uiRequest ?? 0
        setScreen(data.uiView || 'meeting'); setSelected(null)
      }
    } catch (e) { if (mounted.current) setConnectionError(errorText(e)) }
    finally { fetching.current = false }
  }
  refreshNotes = refresh
  useEffect(() => {
    mounted.current = true; void refresh()
    const timer = window.setInterval(refresh, 1000)
    return () => { mounted.current = false; window.clearInterval(timer) }
  }, [])
  const history = () => { setMessage(''); setSearch(''); setScreen('history') }
  const configure = () => { setMessage(''); setScreen('settings') }
  const back = () => { setMessage(''); setScreen('meeting'); setSelected(null) }
  // Set before children render, so every screen of this pass uses the saved app language.
  setAppLanguage(state?.options.uiLanguage)
  const currentId = state?.currentId || state?.reviews[0]?.id
  const displayedId = selected || currentId
  const needsSetup = state?.options.managed === true && (!state.options.setupComplete || !state.options.onboardingComplete)
  const visibleScreen = needsSetup ? 'settings' : screen
  openSettings = state && visibleScreen !== 'settings' ? configure : null
  const historyReviews = state?.reviews.filter(r => (r.title + ' ' + new Date(r.started).toLocaleDateString(uiLocale())).toLocaleLowerCase().includes(search.toLocaleLowerCase())) || []
  async function action(url: string) { try { await post(url); setMessage(''); await refresh() } catch (e) { setMessage(errorText(e)) } }
  const idle = () => {
    const managed = state?.options.managed === true
    const missing = state && (managed ? !state.options.setupComplete : !state.options.hasSpeechKey || !state.options.hasChatKey)
    const health = state?.health && state.health !== 'ok'
    const heading = !state ? t('Verbindung zum Client …', 'Connecting to client …') : !state.enabled ? t('Meeting-Notizen einrichten', 'Set up meeting notes') : missing ? managed ? t('DAS-Zugang einrichten', 'Set up DAS access') : t('Azure-Zugang fehlt', 'Azure access missing') : !state.autoStart ? t('Automatischer Start ist aus', 'Automatic start is off') : health ? t('Teams-Verbindung fehlt', 'Teams connection missing') : state.autoStartSuppressed ? t('Für diesen Anruf pausiert', 'Paused for this call') : t('Bereit für Teams-Anrufe', 'Ready for Teams calls')
    return <Popup storage={state?.storagePath || ''} onHistory={history} saved={t('Ohne gemerktes Kundenziel: Auf diesem PC', 'Without a remembered customer destination: On this PC')} onFolder={() => void action('/api/notes/open-folder')} onClose={() => void action('/api/notes/close')}>
      <div className="notes-status"><span className="status-label"><span className="status-dot" />{heading}</span></div>
      {connectionError || message || state?.error ? <p className="notice" role="alert">{connectionError || message || state?.error}</p> : <p className="quiet">{state?.enabled && !missing && state.autoStart && !health && !state.autoStartSuppressed ? t('Beim nächsten Teams-Anruf entstehen hier automatisch Ihre Notizen. Andere Gespräche, z. B. Zoom, mit „Jetzt starten“ erfassen.', 'Your notes appear here automatically during the next Teams call. Capture other conversations, e.g. Zoom, with “Start now”.') : state && canStart(state) ? t('Mit „Jetzt starten“ werden Mikrofon und PC-Wiedergabe sofort erfasst, z. B. für Zoom.', '“Start now” captures the microphone and PC playback immediately, e.g. for Zoom.') : t('Die Erfassung kann erst starten, wenn die Verbindung bereit ist.', 'Capture can start only once the connection is ready.')}</p>}
      {state && (!state.enabled || missing) ? <button className="primary" onClick={configure}>{t('Zugang einrichten', 'Set up access')}</button>
        : state && <div className="notes-start-actions">
          {canStart(state) && <button className="primary" onClick={() => void action('/api/start')}>{t('Jetzt starten', 'Start now')}</button>}
          {!state.autoStart ? <button onClick={() => void action('/api/auto-start/on')}>{t('Automatischen Start einschalten', 'Turn on automatic start')}</button>
            : health ? <button onClick={() => void action(managed ? '/api/notes/connect' : '/api/sign-in')}>{t('Mit Microsoft anmelden', 'Sign in with Microsoft')}</button>
            : (state.autoStartSuppressed || state.error) ? <button onClick={configure}>{t('Einstellungen öffnen', 'Open settings')}</button> : null}
        </div>}
    </Popup>
  }
  return <>
    {state?.reviews.map(review => <ReviewPanel key={review.id} review={review} state={state}
      visible={visibleScreen === 'meeting' && review.id === displayedId} current={!selected} configure={configure} refresh={refresh} connectionError={connectionError}
      history={history} />)}
    {visibleScreen === 'meeting' && !state?.reviews.some(r => r.id === displayedId) && idle()}
    {visibleScreen === 'settings' && state && <Settings state={state} back={back} refresh={refresh} history={history} />}
    {visibleScreen === 'history' && state && <Popup storage={state.storagePath} saved={t('Gespeicherte Meeting-Notizen', 'Saved meeting notes')} onFolder={() => void action('/api/notes/open-folder')}>
      <button className="back" onClick={back}>{t('Zum aktuellen Meeting', 'To current meeting')}</button><h1>{t('Alle Meetings', 'All meetings')}</h1>
      <label>{t('Meeting suchen', 'Search meetings')}<input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder={t('Titel oder Datum', 'Title or date')} /></label>
      {message && <p className="notice">{message}</p>}
      {!state.reviews.length && <p className="quiet">{t('Noch keine gespeicherten Meetings.', 'No saved meetings yet.')}</p>}
      {state.reviews.length >= 100 && <p className="quiet">{t('Die letzten 100 Meetings. Ältere Dokumente finden Sie über „Ordner öffnen“.', 'The last 100 meetings. Find older documents via “Open folder”.')}</p>}
      {!!state.reviews.length && !historyReviews.length && <p className="quiet">{t('Kein Meeting mit diesem Titel oder Datum gefunden.', 'No meeting found with this title or date.')}</p>}
      {historyReviews.map(r => <div className="history-row" key={r.id}><div><strong>{r.title}</strong><p className="quiet">{new Date(r.started).toLocaleString(uiLocale())}</p>
        <p className="quiet">{r.onenote?.status === 'saved' ? t('In OneNote gespeichert', 'Saved to OneNote') : r.onenote?.mode === 'onenote' ? r.onenote.error || r.onenote.status === 'uncertain' ? t('Lokal gesichert · OneNote noch nicht bestätigt', 'Saved locally · OneNote not yet confirmed') : t('OneNote-Ablage ausstehend', 'OneNote save pending') : r.ended && r.phase === 'complete' && !r.storeError ? t('Auf diesem PC gespeichert', 'Saved on this PC') : t('Vorläufige Notizen', 'Preliminary notes')}</p>
        {(r.error || r.storeError) && <p className="notes-error">{r.storeError ? t('Nicht gespeichert', 'Not saved') : t('Unvollständig', 'Incomplete')}</p>}</div>
        <button onClick={() => { setSelected(r.id); setScreen('meeting') }}>{t('Öffnen', 'Open')}</button></div>)}
    </Popup>}
  </>
}
