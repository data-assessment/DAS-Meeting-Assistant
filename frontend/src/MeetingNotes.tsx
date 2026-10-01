import { createContext, useContext, useEffect, useId, useReducer, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import './meeting-notes.css'
import { NotesOneNote, oneNoteSavedLabel, type OneNoteState } from './NotesOneNote'
import { NotesCalendar, type CalendarContext, type CalendarCandidate } from './NotesCalendar'

import { NotesTasks, unresolved, type Draft, type Person, type Task } from './NotesTasks'
import { appLanguage, cancelAppLanguage, chooseAppLanguage, setAppLanguage, subscribeAppLanguage, t, tIn, uiLocale, type AppLanguage } from './i18n'
type Review = { displayTitle?: string; language?: string; onenote?: OneNoteState; calendarContext?: CalendarContext | null; calendarSelected?: boolean; calendarAccessNeeded?: boolean; calendarNote?: string; calendarCandidates?: CalendarCandidate[]; peopleAccessNeeded?: boolean; people: Person[]; peopleNote: string; id: string; title: string; started: string; ended: string; status: string; error: string; warning: string;
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
  if (!response.ok) throw new Error(t('errors.clientUnreachableChangesKept'))
  const data = await response.json()
  if (data.ok === false) throw new Error(data.error || t('errors.actionFailed'))
  return data
}
const post = (url: string, body: unknown = {}) => request(url, body)
const errorText = (e: unknown) => e instanceof Error ? e.message : t('errors.actionFailed')
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

// Provided by MeetingNotes to the always-visible navigation controls in every Popup.
const Navigation = createContext<{ refresh: () => Promise<void>; openSettings: (() => void) | null }>({
  refresh: async () => {}, openSettings: null,
})

function SettingsButton() {
  const { openSettings } = useContext(Navigation)
  if (!openSettings) return null
  const label = t('navigation.settings')
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
  const { refresh } = useContext(Navigation)
  const [busy, setBusy] = useState(false), [open, setOpen] = useState(false), [error, setError] = useState('')
  const root = useRef<HTMLDivElement>(null), toggle = useRef<HTMLButtonElement>(null)
  const items = () => [...(root.current?.querySelectorAll<HTMLButtonElement>('[role=menuitemradio]') ?? [])]
  useEffect(() => {
    if (!open) return
    // Menu keyboard pattern: focus the checked item on open; close on a click outside.
    items().find(item => item.getAttribute('aria-checked') === 'true')?.focus()
    const outside = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', outside)
    return () => document.removeEventListener('mousedown', outside)
  }, [open])
  function close() { setOpen(false); toggle.current?.focus() }
  function keys(e: KeyboardEvent) {
    const list = items(), index = list.indexOf(document.activeElement as HTMLButtonElement)
    const move = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: list.length - 1 }[e.key]
    if (e.key === 'Escape' && open) { e.preventDefault(); close() }
    else if (e.key === 'Tab' && open) setOpen(false)  // also Shift+Tab back onto the toggle
    else if (move !== undefined && open) { e.preventDefault(); list[(move + list.length) % list.length]?.focus() }
    else if (e.key === 'ArrowDown' && !open && !busy) { e.preventDefault(); setError(''); setOpen(true) }
  }
  async function choose(language: AppLanguage) {
    close()
    const previous = appLanguage()
    if (language === previous || busy) return
    setBusy(true); setError('')
    chooseAppLanguage(language)
    try { await post('/api/notes/ui-language', { language }) }
    catch (e) { cancelAppLanguage(previous); setError(errorText(e)) }
    finally { setBusy(false); await refresh() }
  }
  const [, currentLabel, CurrentFlag] = LANGUAGES.find(([value]) => value === appLanguage()) ?? LANGUAGES[0]
  const label = t('navigation.appLanguage')
  return <>
    <div className="language-switch" ref={root} onKeyDown={keys}
      onBlur={e => { if (open && !root.current?.contains(e.relatedTarget as Node)) setOpen(false) }}>
      {/* aria-disabled instead of disabled while saving, so keyboard focus stays on the toggle. */}
      <button ref={toggle} className="language-toggle" title={`${label}: ${currentLabel}`} aria-label={`${label}: ${currentLabel}`} aria-haspopup="menu" aria-expanded={open} aria-disabled={busy}
        onClick={() => { if (busy) return; setError(''); setOpen(value => !value) }}>
        <CurrentFlag /><svg className="language-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M6 9l6 6 6-6" /></svg>
      </button>
      {open && <div className="language-menu" role="menu" aria-label={label}>
        {LANGUAGES.map(([value, name, Flag]) => <button key={value} role="menuitemradio" tabIndex={-1} aria-checked={appLanguage() === value} onClick={() => void choose(value)}><Flag /><span>{name}</span></button>)}
      </div>}
    </div>
    {/* In the flow of the bar, so a failed save never covers the content below. */}
    {error && <p className="notes-error language-error" role="alert">{error}</p>}
  </>
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
    <nav className="notes-navigation" aria-label={t('navigation.label')}><LanguageSwitch />{onHistory && <button onClick={onHistory}>{t('navigation.allMeetings')}</button>}<SettingsButton /></nav>
    <div className="notes-scroll"><div className="notes-content">{children}</div></div>
    <footer className="notes-footer">{footer !== undefined ? footer : <>
      <div className="storage-copy"><div className={error ? 'notes-error' : 'storage-status'} role="status">{saved}</div>
        <div className="storage-location">{oneNote?.mode === 'onenote' ? `OneNote · ${oneNote.target?.bookName || t('meeting.storage.chooseDestination')}` : t('meeting.storage.localFolder', { folder })}</div>
        {documentName && oneNote?.mode !== 'onenote' && <div className="storage-filename" title={documentName}>{documentName}</div>}
      </div>
      <div className="onenote-actions">{oneNote?.mode === 'onenote' ? oneNote.status === 'saved' && <button onClick={onOneNote}>{t('common.openOneNote')}</button> : <button onClick={onFolder}>{t('common.openFolder')}</button>}
        {onClose && <button className="primary" disabled={closeDisabled} onClick={onClose}>{t('common.closeMeetingWindow')}</button>}</div>
    </>}</footer>
  </main>
}

function elapsed(started: string) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(started).getTime()) / 1000))
  return `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`
}

// Part of the notes themselves (edited and saved back into the summary), so the headings
// follow the meeting's notes language, not the app language.
function documentText(d: Draft, language?: string) {
  return [d.summary, d.decisions && tIn(language, 'summary.sections.decisions') + '\n' + d.decisions,
    d.openQuestions && tIn(language, 'summary.sections.openQuestions') + '\n' + d.openQuestions].filter(Boolean).join('\n\n')
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
  const title = connectionError ? t('meeting.status.clientUnreachable') : live ? t('meeting.status.inProgress') : finalizing ? t('meeting.status.processing') : complete ? t('meeting.status.notesReady') : t('meeting.status.incomplete')
  const statusError = !!connectionError || (!live && !finalizing && !complete)
  const saved = !saveError && oneNote.mode === 'onenote' ? published ? oneNoteSavedLabel(oneNote) : ['preparing', 'sending'].includes(oneNote.status) ? t('meeting.save.savingToOneNote') : oneNote.status === 'uncertain' ? t('common.savedLocallyOneNoteUnconfirmed') : oneNote.error ? t('meeting.save.savedLocallyNotInOneNote') : complete ? t('meeting.save.startingOneNoteSave') : t('meeting.save.draftSavedOneNoteLater') : saveError ? t('meeting.save.notSavedYet') : edit.saving ? t('common.savingChanges') : !d ? t('meeting.save.savedAutomatically') : live || finalizing ? t('meeting.save.preliminarySaved') : t('meeting.save.savedOnPc')
  const savedTime = edit.savedAt ? new Date(edit.savedAt).toLocaleTimeString(uiLocale(), { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''
  const saveLabel = saveError ? t('common.notSaved') : edit.saving ? t('summary.saving') : savedTime ? t('summary.savedAt', { time: savedTime }) : t('summary.savedAutomatically')
  const summaryStatus = published ? t('summary.status.savedVersion') : (issue && !complete) ? t('summary.status.checkProcessing') : finalizing ? t('summary.status.creatingFinal') : d ? complete ? t('summary.status.editDirectly') : t('summary.status.updatedContinuously') : live ? t('summary.status.processingConversation') : t('summary.status.noneAvailable')
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
    {connectionError && <div className="notice" role="alert">{t('meeting.notices.connectionUnconfirmed')}</div>}
    {!!saveError && <div className="notice" role="alert"><strong>{t('common.notesNotSaved')}</strong><p>{saveError}</p>{edit.blocked && <button onClick={edit.retry}>{t('common.retry')}</button>}</div>}
    {(review.error || review.warning || message) && <div className="notice" role="alert"><p>{message || review.error || review.warning}</p>
      {review.autoRetry ? <p>{t('meeting.notices.autoRetry')}</p> : review.canSummarize && <button disabled={review.busy} onClick={() => void action(`/api/notes/${review.id}/summarize`)}>{t('common.retry')}</button>}</div>}
    {current && state.error && <div className="notice" role="alert"><p>{state.error}</p><button onClick={configure}>{t('common.openSettings')}</button></div>}
    {current && state.health !== 'ok' && !connectionError && <div className="notice" role="alert"><p>{t('meeting.notices.teamsUnreachable')}</p>{state.health === 'auth' && <button onClick={() => void action('/api/sign-in')}>{t('common.signInWithMicrosoft')}</button>}</div>}
  </>
  return <Popup expanded={expanded} storage={state.storagePath} saved={saved} onHistory={history} onFolder={() => void action('/api/notes/open-folder')}
    footer={<>
      <span className={saveError ? 'notes-error' : 'quiet'} role="status">{saveError ? t('common.notesNotSaved') : edit.saving ? t('common.savingChanges') : live ? t('meeting.footer.recordingContinues') : finalizing ? t('meeting.footer.processingContinues') : saved}</span>
      <button className="primary" disabled={edit.saving || edit.blocked} onClick={() => void action('/api/notes/close')}>{t('common.closeMeetingWindow')}</button>
    </>}>
    <div className="notes-status"><span className={'status-label' + (statusError ? ' notes-error' : '')}><span className="status-dot" />{title}</span>
      {live && <><span className="notes-time">{elapsed(review.started)}</span><button disabled={actionBusy} onClick={() => void action('/api/stop')}>{t('meeting.stop')}</button></>}
      {current && !live && canStart(state) && <button className="notes-start" disabled={actionBusy} onClick={() => void action('/api/start')}>{t('meeting.startNew')}</button>}
    </div>
    <h1>{review.displayTitle || review.title}</h1><div className="meeting-date">{t('meeting.recording')} {new Date(review.started).toLocaleString(uiLocale(), { dateStyle: 'medium', timeStyle: 'short' })}</div>
    <NotesOneNote id={review.id} value={oneNote} complete={complete} ended={!!review.ended} savedLocally={!!review.savedPath && !saveError} revision={review.revision} disabled={edit.saving || edit.blocked || !!saveError || !!connectionError} refresh={refresh}
      storage={state.storagePath} documentName={review.documentName} onFolder={() => void action('/api/notes/open-folder')} />
    <NotesCalendar key={review.id} id={review.id} selected={review.calendarSelected} context={review.calendarContext}
      candidates={review.calendarCandidates} accessNeeded={review.calendarAccessNeeded} note={review.calendarNote}
      locked={['preparing', 'sending', 'uncertain', 'saved'].includes(oneNote.status)} refresh={refresh} />
    {notices}
    <div>
      <section className="summary-card" aria-label={t('summary.title')}>
        <div className="summary-heading"><div className="summary-heading-copy"><h2>{live ? t('summary.live') : t('summary.title')}</h2>{d && (review.editable ? <span className={saveError ? 'notes-error inline-save' : 'inline-save'} role="status">{saveLabel}</span> : <span className="provisional">{published ? t('summary.inOneNote') : complete ? t('summary.transferring') : t('summary.preliminary')}</span>)}</div>
          {d && <button className="summary-size-toggle" aria-expanded={expanded} aria-controls={`summary-${review.id}`} onClick={() => setExpanded(value => !value)}><ResizeIcon expanded={expanded} />{expanded ? t('summary.collapse') : t('summary.expand')}</button>}
        </div>
        <div className="summary-state" role="status">{!issue && (finalizing || (!d && live)) && <Spinner />}{summaryStatus}</div>
        <div className="summary-body" id={`summary-${review.id}`}>{d ? (review.editable ? <textarea className="summary-inline-editor" aria-label={t('summary.editLabel')} value={documentText(d, review.language)} maxLength={64000} onChange={e => edit.change(current => ({ ...current, summary: e.target.value, decisions: '', openQuestions: '' }))} /> : <p className="notes-summary">{documentText(d, review.language)}</p>) : <p className="quiet">{live ? t('summary.appearsAutomatically') : finalizing ? t('summary.noInterim') : t('summary.tasksKept')}</p>}</div>
      </section>
      <div className="tasks-heading"><h2>{t('meeting.tasks.title')}</h2>{d && !!d.tasks.length && <span>{t('meeting.tasks.selectedOf', { selected, total: d.tasks.length })}</span>}</div>
      {!d?.tasks.length && <p className="quiet">{live || finalizing ? t('meeting.tasks.noneYet') : t('meeting.tasks.noFollowUp')}</p>}
      {!!d?.tasks.length && <p className="quiet tasks-help">{published ? t('meeting.tasks.helpPublished') : t('meeting.tasks.helpLocal')}</p>}
      {complete && count > 0 && <p className="task-attention">{t('meeting.tasks.missingDetails', { count })}</p>}
      {d && <NotesTasks draft={d} people={review.people || []} peopleNote={review.peopleNote || ''} editable={review.tasksEditable ?? true} expanded change={edit.change} />}
      {!!d?.tasks.length && !published && <button disabled={actionBusy} onClick={() => void action(`/api/notes/${review.id}/people-refresh`)}>{t('meeting.tasks.reloadPeople')}</button>}
      {!!d?.tasks.length && !published && review.peopleAccessNeeded && <div className="notice"><p>{t('meeting.tasks.peopleAccessHint')}</p><button disabled={actionBusy} onClick={() => void action(`/api/notes/${review.id}/people-connect`)}>{t('meeting.tasks.connectPeople')}</button></div>}
      {d && <div className="notes-copy"><button disabled={edit.saving || edit.blocked || copyState === copying} onClick={() => void copyNotes()}>{t('meeting.copy.action')}</button>
        <span className="quiet">{t('meeting.copy.hint')}</span>
        {copyState && <p className={copyError ? 'notes-error' : 'copy-success'} role={copyError ? 'alert' : 'status'}>{copyState === copying ? t('meeting.copy.copying') : copyState === copied ? t('meeting.copy.copied') : copyState}</p>}</div>}
    </div>
  </Popup>
}

type SettingsProps = { state: NotesState; back: () => void; backTo: Screen; refresh: () => void; history: () => void }

function BackButton({ back, backTo }: Pick<SettingsProps, 'back' | 'backTo'>) {
  return <button className="back" onClick={back}>{backTo === 'history' ? t('settings.backToMeetings') : t('settings.backToMeeting')}</button>
}

// Always a list of the common languages. Community also offers "Other …" with a text field for
// any locale such as de-CH or it-IT; Managed keeps the fixed list.
function MeetingLanguageSelect({ value, onChange, free = false }: { value: string; onChange: (value: string) => void; free?: boolean }) {
  const label = t('settings.meetingLanguage'), customLabel = t('settings.meetingLanguageCustom')
  const known = [['de-DE', t('settings.languages.german')], ['en-US', t('settings.languages.english')], ['fr-FR', t('settings.languages.french')]]
  const listed = known.some(([code]) => code === value)
  const [other, setOther] = useState(free && !listed)
  const custom = free && (other || !listed)
  return <>
    <label>{label}<select aria-label={label} value={custom ? 'other' : value}
      onChange={e => { if (e.target.value === 'other') setOther(true); else { setOther(false); onChange(e.target.value) } }}>
      {known.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
      {free ? <option value="other">{t('settings.languages.other')}</option>
        : !listed && <option value={value}>{value}</option>}
    </select><span className="quiet">{t('settings.meetingLanguageHint')}</span></label>
    {custom && <label>{customLabel}<input aria-label={customLabel} autoComplete="off" maxLength={16} value={value} onChange={e => onChange(e.target.value)} /></label>}
  </>
}

function Settings(props: SettingsProps) {
  return props.state.options.managed === true ? <ManagedSettings {...props} /> : <CommunitySettings {...props} />
}

// Business dictionary (speech recognition and spelling) and company background for the notes.
// Saved on its own with the user settings; also editable during a meeting (applies to the next
// meeting for recognition, to the next summary for the notes).
function CompanyContext() {
  const [terms, setTerms] = useState(''), [context, setContext] = useState('')
  const [loaded, setLoaded] = useState(false), [busy, setBusy] = useState(false)
  const [message, setMessage] = useState(''), [failed, setFailed] = useState(false)
  // Shows what is stored: the backend removes duplicate terms and normalizes spacing.
  async function load() {
    const data = await request('/api/settings')
    setTerms((Array.isArray(data.values?.STT_DICTIONARY) ? data.values.STT_DICTIONARY : []).join('\n'))
    setContext(String(data.values?.COMPANY_CONTEXT ?? '')); setLoaded(true)
  }
  useEffect(() => { load().catch(e => { setMessage(errorText(e)); setFailed(true) }) }, [])
  async function save() {
    setBusy(true); setMessage(''); setFailed(false)
    try {
      await post('/api/settings', { values: { STT_DICTIONARY: terms.split('\n').map(term => term.trim()).filter(Boolean), COMPANY_CONTEXT: context } })
      await load()
      setMessage(t('settings.context.saved'))
    } catch (e) { setMessage(errorText(e)); setFailed(true) }
    finally { setBusy(false) }
  }
  return <section className="company-context" aria-labelledby="company-context-title">
    <h2 id="company-context-title">{t('settings.context.title')}</h2>
    <p className="quiet">{t('settings.context.intro')}</p>
    <label>{t('settings.context.dictionary')}
      <textarea rows={5} aria-label={t('settings.context.dictionary')} value={terms} disabled={!loaded || busy} placeholder={t('settings.context.dictionaryPlaceholder')} onChange={e => setTerms(e.target.value)} />
      <span className="quiet">{t('settings.context.dictionaryHint')}</span></label>
    <label>{t('settings.context.text')}
      <textarea rows={7} maxLength={4000} aria-label={t('settings.context.text')} value={context} disabled={!loaded || busy} placeholder={t('settings.context.textPlaceholder')} onChange={e => setContext(e.target.value)} />
      <span className="quiet">{t('settings.context.textHint', { used: context.length, max: 4000 })}</span></label>
    {message && <p className={failed ? 'notes-error' : 'copy-success'} role={failed ? 'alert' : 'status'}>{message}</p>}
    <button disabled={!loaded || busy} onClick={() => void save()}>{busy ? t('common.pleaseWait') : t('settings.context.save')}</button>
  </section>
}

function ManagedSettings({ state, back, backTo, refresh, history }: SettingsProps) {
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
        ? t('setup.closesToTray')
        : t('setup.signInFirst')}</p>
      {signedIn && <button className="primary" disabled={busy} onClick={() => void finish()}>
        {pending === 'save' ? t('common.pleaseWait') : state.active ? t('common.close') : t('common.done')}
      </button>}
    </>}>
    {/* After setup, settings are also opened from a meeting (gear): leave them without "Fertig". */}
    {finished && signedIn && !busy && <BackButton back={back} backTo={backTo} />}
    <p className="setup-step">{finished ? 'DAS Meeting Assistant' : signedIn ? t('setup.stepFinish') : t('setup.stepSignIn')}</p>
    <h1>{signedIn ? finished ? t('navigation.settings') : t('setup.signedIn') : t('setup.welcome')}</h1>
    {error && <p className="notice" role="alert">{error}</p>}
    {!signedIn ? <>
      <p>{t('setup.signInIntro')}</p>
      <p className="quiet">{t('setup.browserHint')}</p>
      <button className="primary" disabled={busy} onClick={() => void signIn()}>{busy ? t('common.pleaseWait') : t('common.signInWithMicrosoft')}</button>
      {busy && <p className="setup-progress" role="status"><Spinner /> {t('setup.checking')}</p>}
      <p className="quiet">{t('setup.incompleteHint')}</p>
    </> : <>
      <p className="setup-success" role="status">{t('setup.ready')}</p>
      {state.active ? <p className="notice">{t('setup.meetingInProgress')}</p>
        : <p>{t('setup.keepSettings')}</p>}
      <p className="quiet">{form.autoStart ? t('setup.autoStartOn') : t('setup.autoStartOff')}</p>
      <details className="setup-options" onToggle={e => { if (e.currentTarget.open) void loadDevices() }}>
        <summary>{t('settings.adjust')} <span>{t('settings.optional')}</span></summary>
        <fieldset disabled={state.active || busy}>
          <label className="checkbox"><input type="checkbox" checked={form.autoStart} onChange={e => setForm({ ...form, autoStart: e.target.checked })} />{t('settings.autoStartTeams')}</label>
          {(['mic', 'loopback'] as const).map(source => <label key={source}>{source === 'mic' ? t('settings.microphone') : t('settings.teamsPlayback')}
            <select aria-label={source === 'mic' ? t('settings.microphone') : t('settings.teamsPlayback')} value={form[source]} onChange={e => setForm({ ...form, [source]: e.target.value })}>
              <option value="">{t('settings.windowsDefaultDevice')}</option>
              {form[source] && !devices.some(d => d.loopback === (source === 'loopback') && d.name === form[source]) && <option value={form[source]}>{form[source]}{t('settings.currentlyUnavailable')}</option>}
              {devices.filter(d => d.loopback === (source === 'loopback')).map((d, i) => <option value={d.name} key={i}>{d.name}</option>)}
            </select></label>)}
          <button onClick={() => void loadDevices()}>{t('settings.reloadDevices')}</button>
          <MeetingLanguageSelect value={form.language} onChange={language => setForm({ ...form, language })} />
          <button onClick={() => void signIn()}>{t('settings.checkDasConnection')}</button>
          <p className="quiet">{t('settings.dasPrivacy')}</p>
        </fieldset>
      </details>
      <CompanyContext />
      <p className="quiet">{t('settings.reopenLater')}</p>
    </>}
  </Popup>
}

function CommunitySettings({ state, back, backTo, refresh, history }: SettingsProps) {
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
  return <Popup storage={state.storagePath} onHistory={history} saved={t('settings.storageLocation')} onFolder={() => void post('/api/notes/open-folder').catch(e => setMessage(errorText(e)))}>
    <BackButton back={back} backTo={backTo} /><h1>{t('navigation.settings')}</h1>
    {(message || state.options.credentialError) && <p className="notice" role="alert">{message || state.options.credentialError}</p>}
    {state.active && <p className="notice">{t('settings.changeAfterMeeting')}</p>}
    <button disabled={busy} onClick={() => post(`/api/auto-start/${state.autoStart ? 'off' : 'on'}`).then(refresh).catch(e => setMessage(errorText(e)))}>{state.autoStart ? t('settings.turnOffAutoStart') : t('common.turnOnAutoStart')}</button>
    <fieldset disabled={state.active || busy}>
      <label className="checkbox"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />{t('settings.notesInsteadOfRecording')}</label>
      {(['mic', 'loopback'] as const).map(source => <label key={source}>{source === 'mic' ? t('settings.microphone') : t('settings.teamsPlayback')}
        <select value={form[source]} onChange={e => setForm({ ...form, [source]: e.target.value })}><option value="">{t('settings.windowsDefaultDevice')}</option>
          {devices.filter(d => d.loopback === (source === 'loopback')).map((d, i) => <option value={d.name} key={i}>{d.name}</option>)}
        </select></label>)}
      <button onClick={load}>{t('settings.reloadDevices')}</button>
      <MeetingLanguageSelect free value={form.language} onChange={language => setForm({ ...form, language })} />
      <h2>{t('settings.azureConnections')}</h2>
      {(['region', 'speechKey', 'endpoint', 'model', 'chatKey'] as const).map(key => <label key={key}>{{region:t('settings.speechRegion'),speechKey:t('settings.speechKey'),endpoint:t('settings.textModelEndpoint'),model:t('settings.deployment'),chatKey:t('settings.textModelKey')}[key]}
        <input type={key.endsWith('Key') ? 'password' : 'text'} autoComplete="off" value={form[key]} maxLength={2048}
          placeholder={key.endsWith('Key') && state.options[key === 'speechKey' ? 'hasSpeechKey' : 'hasChatKey'] ? state.options[key + 'Saved'] ? t('settings.keySavedEncrypted') : t('settings.keyLoadedForSession') : ''}
          onChange={e => setForm({ ...form, [key]: e.target.value })} />
      </label>)}
      {<div className="notes-more">{(['speechKey', 'chatKey'] as const).map(key => state.options[key === 'speechKey' ? 'hasSpeechKey' : 'hasChatKey'] && <button key={key} onClick={async () => {
        setBusy(true)
        try { await post('/api/notes/credentials/remove', { key }); setForm(f => ({ ...f, [key]: '' })); refresh(); setMessage(t('settings.keyRemoved')) }
        catch (e) { setMessage(errorText(e)) } finally { setBusy(false) }
      }}>{key === 'speechKey' ? t('settings.removeSpeechKey') : t('settings.removeTextModelKey')}</button>)}</div>}
      <p className="quiet">{t('settings.keysPrivacy')}</p>
      <button className="primary" onClick={configure}>{t('settings.apply')}</button>
    </fieldset>
    <CompanyContext />
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
        // Opened by the app (tray, onboarding): settings then return to the current meeting.
        setScreen(data.uiView || 'meeting'); setSelected(null); setReturnTo({ screen: 'meeting', selected: null })
      }
    } catch (e) { if (mounted.current) setConnectionError(errorText(e)) }
    finally { fetching.current = false }
  }
  const [, rerender] = useReducer((n: number) => n + 1, 0)
  useEffect(() => subscribeAppLanguage(rerender), [])
  useEffect(() => {
    mounted.current = true; void refresh()
    const timer = window.setInterval(refresh, 1000)
    return () => { mounted.current = false; window.clearInterval(timer) }
  }, [])
  // Settings return to where they were opened from, including a meeting picked in the history.
  const [returnTo, setReturnTo] = useState<{ screen: Screen; selected: string | null }>({ screen: 'meeting', selected: null })
  const history = () => { setMessage(''); setSearch(''); setScreen('history') }
  const configure = () => { setMessage(''); setReturnTo({ screen: screen === 'settings' ? 'meeting' : screen, selected }); setScreen('settings') }
  const back = () => { setMessage(''); setScreen('meeting'); setSelected(null) }
  const closeSettings = () => { setMessage(''); setScreen(returnTo.screen); setSelected(returnTo.selected) }
  // Set before children render, so every screen of this pass uses the saved app language.
  setAppLanguage(state?.options.uiLanguage)
  const currentId = state?.currentId || state?.reviews[0]?.id
  const displayedId = selected || currentId
  const needsSetup = state?.options.managed === true && (!state.options.setupComplete || !state.options.onboardingComplete)
  const visibleScreen = needsSetup ? 'settings' : screen
  const navigation = { refresh, openSettings: state && visibleScreen !== 'settings' ? configure : null }
  const historyReviews = state?.reviews.filter(r => (r.title + ' ' + new Date(r.started).toLocaleDateString(uiLocale())).toLocaleLowerCase().includes(search.toLocaleLowerCase())) || []
  async function action(url: string) { try { await post(url); setMessage(''); await refresh() } catch (e) { setMessage(errorText(e)) } }
  const idle = () => {
    const managed = state?.options.managed === true
    const missing = state && (managed ? !state.options.setupComplete : !state.options.hasSpeechKey || !state.options.hasChatKey)
    const health = state?.health && state.health !== 'ok'
    const heading = !state ? t('idle.connecting') : !state.enabled ? t('idle.setUpNotes') : missing ? managed ? t('idle.setUpDas') : t('idle.azureMissing') : !state.autoStart ? t('idle.autoStartOff') : health ? t('idle.teamsMissing') : state.autoStartSuppressed ? t('idle.paused') : t('idle.ready')
    return <Popup storage={state?.storagePath || ''} onHistory={history} saved={t('idle.fallbackDestination')} onFolder={() => void action('/api/notes/open-folder')} onClose={() => void action('/api/notes/close')}>
      <div className="notes-status"><span className="status-label"><span className="status-dot" />{heading}</span></div>
      {connectionError || message || state?.error ? <p className="notice" role="alert">{connectionError || message || state?.error}</p> : <p className="quiet">{state?.enabled && !missing && state.autoStart && !health && !state.autoStartSuppressed ? t('idle.hintReady') : state && canStart(state) ? t('idle.hintManual') : t('idle.hintNotReady')}</p>}
      {state && (!state.enabled || missing) ? <button className="primary" onClick={configure}>{t('idle.setUpAccess')}</button>
        : state && <div className="notes-start-actions">
          {canStart(state) && <button className="primary" onClick={() => void action('/api/start')}>{t('idle.startNow')}</button>}
          {!state.autoStart ? <button onClick={() => void action('/api/auto-start/on')}>{t('common.turnOnAutoStart')}</button>
            : health ? <button onClick={() => void action(managed ? '/api/notes/connect' : '/api/sign-in')}>{t('common.signInWithMicrosoft')}</button>
            : (state.autoStartSuppressed || state.error) ? <button onClick={configure}>{t('common.openSettings')}</button> : null}
        </div>}
    </Popup>
  }
  return <Navigation.Provider value={navigation}>
    {state?.reviews.map(review => <ReviewPanel key={review.id} review={review} state={state}
      visible={visibleScreen === 'meeting' && review.id === displayedId} current={!selected} configure={configure} refresh={refresh} connectionError={connectionError}
      history={history} />)}
    {visibleScreen === 'meeting' && !state?.reviews.some(r => r.id === displayedId) && idle()}
    {visibleScreen === 'settings' && state && <Settings state={state} back={closeSettings} backTo={returnTo.screen} refresh={refresh} history={history} />}
    {visibleScreen === 'history' && state && <Popup storage={state.storagePath} saved={t('history.savedNotes')} onFolder={() => void action('/api/notes/open-folder')}>
      <button className="back" onClick={back}>{t('history.toCurrent')}</button><h1>{t('navigation.allMeetings')}</h1>
      <label>{t('history.search')}<input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder={t('history.searchPlaceholder')} /></label>
      {message && <p className="notice">{message}</p>}
      {!state.reviews.length && <p className="quiet">{t('history.empty')}</p>}
      {state.reviews.length >= 100 && <p className="quiet">{t('history.last100')}</p>}
      {!!state.reviews.length && !historyReviews.length && <p className="quiet">{t('history.noMatch')}</p>}
      {historyReviews.map(r => <div className="history-row" key={r.id}><div><strong>{r.title}</strong><p className="quiet">{new Date(r.started).toLocaleString(uiLocale())}</p>
        <p className="quiet">{r.onenote?.status === 'saved' ? t('history.savedToOneNote') : r.onenote?.mode === 'onenote' ? r.onenote.error || r.onenote.status === 'uncertain' ? t('common.savedLocallyOneNoteUnconfirmed') : t('history.oneNotePending') : r.ended && r.phase === 'complete' && !r.storeError ? t('history.savedOnPc') : t('history.preliminary')}</p>
        {(r.error || r.storeError) && <p className="notes-error">{r.storeError ? t('common.notSaved') : t('history.incomplete')}</p>}</div>
        <button onClick={() => { setSelected(r.id); setScreen('meeting') }}>{t('common.open')}</button></div>)}
    </Popup>}
  </Navigation.Provider>
}
