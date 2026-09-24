import { useEffect, useRef, useState, type ReactNode } from 'react'
import './meeting-notes.css'
import { NotesOneNote, oneNoteSavedLabel, type OneNoteState } from './NotesOneNote'
import { NotesCalendar, type CalendarContext, type CalendarCandidate } from './NotesCalendar'

import { NotesTasks, unresolved, type Draft, type Person, type Task } from './NotesTasks'
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
  if (!response.ok) throw new Error('Client nicht erreichbar. Änderungen bleiben in diesem Fenster erhalten.')
  const data = await response.json()
  if (data.ok === false) throw new Error(data.error || 'Aktion fehlgeschlagen.')
  return data
}
const post = (url: string, body: unknown = {}) => request(url, body)
const errorText = (e: unknown) => e instanceof Error ? e.message : 'Aktion fehlgeschlagen.'

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
    {onHistory && <nav className="notes-navigation" aria-label="Meeting-Navigation"><button onClick={onHistory}>Alle Meetings</button></nav>}
    <div className="notes-scroll"><div className="notes-content">{children}</div></div>
    <footer className="notes-footer">{footer !== undefined ? footer : <>
      <div className="storage-copy"><div className={error ? 'notes-error' : 'storage-status'} role="status">{saved}</div>
        <div className="storage-location">{oneNote?.mode === 'onenote' ? `OneNote · ${oneNote.target?.bookName || 'Ziel noch wählen'}` : `Auf diesem PC · Ordner „${folder}“`}</div>
        {documentName && oneNote?.mode !== 'onenote' && <div className="storage-filename" title={documentName}>{documentName}</div>}
      </div>
      <div className="onenote-actions">{oneNote?.mode === 'onenote' ? oneNote.status === 'saved' && <button onClick={onOneNote}>OneNote öffnen</button> : <button onClick={onFolder}>Ordner öffnen</button>}
        {onClose && <button className="primary" disabled={closeDisabled} onClick={onClose}>Meeting-Fenster schließen</button>}</div>
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
  const title = connectionError ? 'Client nicht erreichbar' : live ? 'Meeting läuft' : finalizing ? 'Verarbeitung läuft …' : complete ? 'Notizen fertig' : 'Abschluss unvollständig'
  const statusError = !!connectionError || (!live && !finalizing && !complete)
  const saved = !saveError && oneNote.mode === 'onenote' ? published ? oneNoteSavedLabel(oneNote) : ['preparing', 'sending'].includes(oneNote.status) ? 'Wird in OneNote gespeichert …' : oneNote.status === 'uncertain' ? 'Lokal gesichert · OneNote noch nicht bestätigt' : oneNote.error ? 'Lokal gesichert · Noch nicht in OneNote gespeichert' : complete ? 'Automatische OneNote-Ablage wird gestartet …' : 'Entwurf gesichert · OneNote-Ablage nach Meeting-Ende' : saveError ? 'Noch nicht gespeichert' : edit.saving ? 'Änderungen werden gespeichert …' : !d ? 'Notizen werden automatisch gespeichert' : live || finalizing ? '✓ Vorläufige Notizen gespeichert' : '✓ Auf diesem PC gespeichert'
  const savedTime = edit.savedAt ? new Date(edit.savedAt).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''
  const saveLabel = saveError ? 'Nicht gespeichert' : edit.saving ? 'Speichert …' : savedTime ? `Gespeichert um ${savedTime}` : 'Wird automatisch gespeichert'
  const summaryStatus = published ? 'Abgelegter Stand · Weitere Änderungen direkt in OneNote' : (issue && !complete) ? 'Verarbeitung prüfen' : finalizing ? 'Finale Zusammenfassung wird erstellt …' : d ? complete ? 'Text direkt bearbeiten. Jede Änderung wird automatisch gespeichert.' : 'Wird laufend aktualisiert. Nach Abschluss bearbeitbar.' : live ? 'Gespräch wird verarbeitet …' : 'Keine Zusammenfassung verfügbar.'
  async function action(url: string) {
    setActionBusy(true); setMessage('')
    try { await post(url); refresh() } catch (e) { setMessage(errorText(e)) }
    finally { setActionBusy(false) }
  }
  async function copyNotes() {
    setCopyState('Wird kopiert …'); setCopyError(false)
    try { await post(`/api/notes/${review.id}/copy`); setCopyState('Kopiert · Notizen und ausgewählte Aufgaben sind in der Zwischenablage.') }
    catch (e) { setCopyState(errorText(e)); setCopyError(true) }
  }
  const notices = <>
    {connectionError && <div className="notice" role="alert">Der aktuelle Stand kann gerade nicht bestätigt werden. Die Verbindung wird automatisch erneut geprüft.</div>}
    {!!saveError && <div className="notice" role="alert"><strong>Notizen noch nicht gespeichert.</strong><p>{saveError}</p>{edit.blocked && <button onClick={edit.retry}>Erneut versuchen</button>}</div>}
    {(review.error || review.warning || message) && <div className="notice" role="alert"><p>{message || review.error || review.warning}</p>
      {review.autoRetry ? <p>Ein neuer Versuch startet automatisch.</p> : review.canSummarize && <button disabled={review.busy} onClick={() => void action(`/api/notes/${review.id}/summarize`)}>Erneut versuchen</button>}</div>}
    {current && state.error && <div className="notice" role="alert"><p>{state.error}</p><button onClick={configure}>Einstellungen öffnen</button></div>}
    {current && state.health !== 'ok' && !connectionError && <div className="notice" role="alert"><p>Der Teams-Status ist nicht erreichbar. Das Anrufende kann nicht zuverlässig erkannt werden.</p>{state.health === 'auth' && <button onClick={() => void action('/api/sign-in')}>Mit Microsoft anmelden</button>}</div>}
  </>
  return <Popup expanded={expanded} storage={state.storagePath} saved={saved} onHistory={history} onFolder={() => void action('/api/notes/open-folder')}
    footer={<>
      <span className={saveError ? 'notes-error' : 'quiet'} role="status">{saveError ? 'Notizen noch nicht gespeichert.' : edit.saving ? 'Änderungen werden gespeichert …' : live ? 'Aufnahme läuft beim Schließen im Hintergrund weiter.' : finalizing ? 'Verarbeitung läuft beim Schließen im Hintergrund weiter.' : saved}</span>
      <button className="primary" disabled={edit.saving || edit.blocked} onClick={() => void action('/api/notes/close')}>Meeting-Fenster schließen</button>
    </>}>
    <div className="notes-status"><span className={'status-label' + (statusError ? ' notes-error' : '')}><span className="status-dot" />{title}</span>
      {live && <><span className="notes-time">{elapsed(review.started)}</span><button disabled={actionBusy} onClick={() => void action('/api/stop')}>Stoppen</button></>}
    </div>
    <h1>{review.displayTitle || review.title}</h1><div className="meeting-date">Aufzeichnung: {new Date(review.started).toLocaleString('de-DE', { dateStyle: 'medium', timeStyle: 'short' })}</div>
    <NotesOneNote id={review.id} value={oneNote} complete={complete} ended={!!review.ended} savedLocally={!!review.savedPath && !saveError} revision={review.revision} disabled={edit.saving || edit.blocked || !!saveError || !!connectionError} refresh={refresh}
      storage={state.storagePath} documentName={review.documentName} onFolder={() => void action('/api/notes/open-folder')} />
    <NotesCalendar key={review.id} id={review.id} selected={review.calendarSelected} context={review.calendarContext}
      candidates={review.calendarCandidates} accessNeeded={review.calendarAccessNeeded} note={review.calendarNote}
      locked={['preparing', 'sending', 'uncertain', 'saved'].includes(oneNote.status)} refresh={refresh} />
    {notices}
    <div>
      <section className="summary-card" aria-label="Zusammenfassung">
        <div className="summary-heading"><div className="summary-heading-copy"><h2>{live ? 'Laufende Zusammenfassung' : 'Zusammenfassung'}</h2>{d && (review.editable ? <span className={saveError ? 'notes-error inline-save' : 'inline-save'} role="status">{saveLabel}</span> : <span className="provisional">{published ? 'In OneNote' : complete ? 'Übertragung' : 'Vorläufig'}</span>)}</div>
          {d && <button className="summary-size-toggle" aria-expanded={expanded} aria-controls={`summary-${review.id}`} onClick={() => setExpanded(value => !value)}><ResizeIcon expanded={expanded} />{expanded ? 'Verkleinern' : 'Vergrößern'}</button>}
        </div>
        <div className="summary-state" role="status">{!issue && (finalizing || (!d && live)) && <Spinner />}{summaryStatus}</div>
        <div className="summary-body" id={`summary-${review.id}`}>{d ? (review.editable ? <textarea className="summary-inline-editor" aria-label="Zusammenfassung direkt bearbeiten" value={documentText(d)} maxLength={64000} onChange={e => edit.change(current => ({ ...current, summary: e.target.value, decisions: '', openQuestions: '' }))} /> : <p className="notes-summary">{documentText(d)}</p>) : <p className="quiet">{live ? 'Die Zusammenfassung erscheint hier automatisch.' : finalizing ? 'Es liegt noch kein Zwischenstand vor. Bitte warten Sie auf den Abschluss.' : 'Vorhandene Aufgabenvorschläge bleiben erhalten.'}</p>}</div>
      </section>
      <div className="tasks-heading"><h2>Aufgabenvorschläge</h2>{d && !!d.tasks.length && <span>{selected} von {d.tasks.length} ausgewählt</span>}</div>
      {!d?.tasks.length && <p className="quiet">{live || finalizing ? 'Noch keine Vorschläge. Vereinbarte Aufgaben erscheinen hier automatisch.' : 'Keine Nacharbeit vereinbart.'}</p>}
      {!!d?.tasks.length && <p className="quiet tasks-help">{published ? 'Verantwortliche und Angaben können Sie hier ergänzen. Änderungen werden automatisch in OneNote gespeichert; abhaken können Sie die Aufgaben dort.' : 'Ausgewählte Aufgaben bleiben in Ihrer Aufgabenliste. Sie können Vorschläge abwählen.'}</p>}
      {complete && count > 0 && <p className="task-attention">Bei {count === 1 ? 'einer ausgewählten Aufgabe fehlen' : `${count} ausgewählten Aufgaben fehlen`} noch Angaben.</p>}
      {d && <NotesTasks draft={d} people={review.people || []} peopleNote={review.peopleNote || ''} editable={review.tasksEditable ?? true} expanded change={edit.change} />}
      {!!d?.tasks.length && !published && <button disabled={actionBusy} onClick={() => void action(`/api/notes/${review.id}/people-refresh`)}>Personen erneut laden</button>}
      {!!d?.tasks.length && !published && review.peopleAccessNeeded && <div className="notice"><p>Teilnehmer fehlen in der Auswahl? Mit Teams-Zugriff kann der Client Anrufereignisse und Namen ermitteln. Microsoft verlangt dafür Leserechte auf Ihre Chats.</p><button disabled={actionBusy} onClick={() => void action(`/api/notes/${review.id}/people-connect`)}>Teams-Personen verbinden</button></div>}
      {d && <div className="notes-copy"><button disabled={edit.saving || edit.blocked || copyState === 'Wird kopiert …'} onClick={() => void copyNotes()}>Notizen kopieren</button>
        <span className="quiet">Zusammenfassung und ausgewählte Aufgaben</span>
        {copyState && <p className={copyError ? 'notes-error' : 'copy-success'} role={copyError ? 'alert' : 'status'}>{copyState}</p>}</div>}
    </div>
  </Popup>
}

type SettingsProps = { state: NotesState; back: () => void; refresh: () => void; history: () => void }

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
        ? 'Das Fenster wird geschlossen. Die App läuft im Infobereich neben der Windows-Uhr weiter.'
        : 'Erst nach der Anmeldung und „Fertig“ können Meeting-Notizen starten.'}</p>
      {signedIn && <button className="primary" disabled={busy} onClick={() => void finish()}>
        {pending === 'save' ? 'Bitte warten …' : state.active ? 'Schließen' : 'Fertig'}
      </button>}
    </>}>
    <p className="setup-step">{finished ? 'DAS Meeting Assistant' : signedIn ? 'Schritt 2 von 2 · Abschließen' : 'Schritt 1 von 2 · Anmelden'}</p>
    <h1>{signedIn ? finished ? 'Einstellungen' : 'Anmeldung erfolgreich' : 'Willkommen bei DAS Meeting Assistant'}</h1>
    {error && <p className="notice" role="alert">{error}</p>}
    {!signedIn ? <>
      <p>Melden Sie sich mit Ihrem Microsoft-Firmenkonto an, um DAS Meeting Assistant zu nutzen. Die Anmeldung ist erforderlich.</p>
      <p className="quiet">Die Microsoft-Anmeldung öffnet sich bei Bedarf im Browser. Kehren Sie danach zu diesem Fenster zurück.</p>
      <button className="primary" disabled={busy} onClick={() => void signIn()}>{busy ? 'Bitte warten …' : 'Mit Microsoft anmelden'}</button>
      {busy && <p className="setup-progress" role="status"><Spinner /> Anmeldung und DAS-Zugang werden geprüft …</p>}
      <p className="quiet">Wenn Sie dieses Fenster jetzt schließen, bleibt die Einrichtung unvollständig. Sie können sie über das App-Symbol neben der Windows-Uhr fortsetzen.</p>
    </> : <>
      <p className="setup-success" role="status">✓ Microsoft-Anmeldung und DAS-Zugang sind bereit.</p>
      {state.active ? <p className="notice">Ein Meeting läuft. Einstellungen können danach geändert werden. Sie können dieses Fenster schließen; die Erfassung läuft weiter.</p>
        : <p>Sie können die Einstellungen beibehalten und direkt auf „Fertig“ klicken.</p>}
      <p className="quiet">{form.autoStart ? 'Meeting-Notizen starten bei erkannten Teams-Anrufen automatisch.' : 'Der automatische Start ist ausgeschaltet. Sie können ihn unter „Einstellungen anpassen“ einschalten.'}</p>
      <details className="setup-options" onToggle={e => { if (e.currentTarget.open) void loadDevices() }}>
        <summary>Einstellungen anpassen <span>optional</span></summary>
        <fieldset disabled={state.active || busy}>
          <label className="checkbox"><input type="checkbox" checked={form.autoStart} onChange={e => setForm({ ...form, autoStart: e.target.checked })} />Bei Teams-Anrufen automatisch starten</label>
          {(['mic', 'loopback'] as const).map(source => <label key={source}>{source === 'mic' ? 'Mikrofon' : 'Teams-Wiedergabe / Headset'}
            <select aria-label={source === 'mic' ? 'Mikrofon' : 'Teams-Wiedergabe / Headset'} value={form[source]} onChange={e => setForm({ ...form, [source]: e.target.value })}>
              <option value="">Windows-Standardgerät</option>
              {form[source] && !devices.some(d => d.loopback === (source === 'loopback') && d.name === form[source]) && <option value={form[source]}>{form[source]} (aktuell nicht verfügbar)</option>}
              {devices.filter(d => d.loopback === (source === 'loopback')).map((d, i) => <option value={d.name} key={i}>{d.name}</option>)}
            </select></label>)}
          <button onClick={() => void loadDevices()}>Geräte neu laden</button>
          <label>Sprache<select aria-label="Sprache" value={form.language} onChange={e => setForm({ ...form, language: e.target.value })}>
            <option value="de-DE">Deutsch</option><option value="en-US">Englisch</option><option value="fr-FR">Französisch</option>
            {!['de-DE', 'en-US', 'fr-FR'].includes(form.language) && <option value={form.language}>{form.language}</option>}
          </select></label>
          <button onClick={() => void signIn()}>DAS-Verbindung erneut prüfen</button>
          <p className="quiet">DAS stellt die Sprachverarbeitung und Zusammenfassungen bereit. Audio und Transkript werden vom Client nicht als Dateien gespeichert.</p>
        </fieldset>
      </details>
      <p className="quiet">Einstellungen können Sie später über das App-Symbol neben der Windows-Uhr wieder öffnen.</p>
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
  return <Popup storage={state.storagePath} onHistory={history} saved="Speicherort für Meeting-Notizen" onFolder={() => void post('/api/notes/open-folder').catch(e => setMessage(errorText(e)))}>
    <button className="back" onClick={back}>Zurück zum Meeting</button><h1>Einstellungen</h1>
    {(message || state.options.credentialError) && <p className="notice" role="alert">{message || state.options.credentialError}</p>}
    {state.active && <p className="notice">Geräte und Zugänge können nach dem Meeting geändert werden.</p>}
    <button disabled={busy} onClick={() => post(`/api/auto-start/${state.autoStart ? 'off' : 'on'}`).then(refresh).catch(e => setMessage(errorText(e)))}>{state.autoStart ? 'Automatischen Start ausschalten' : 'Automatischen Start einschalten'}</button>
    <fieldset disabled={state.active || busy}>
      <label className="checkbox"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />Meeting-Notizen statt Dateiaufzeichnung</label>
      {(['mic', 'loopback'] as const).map(source => <label key={source}>{source === 'mic' ? 'Mikrofon' : 'Teams-Wiedergabe / Headset'}
        <select value={form[source]} onChange={e => setForm({ ...form, [source]: e.target.value })}><option value="">Windows-Standardgerät</option>
          {devices.filter(d => d.loopback === (source === 'loopback')).map((d, i) => <option value={d.name} key={i}>{d.name}</option>)}
        </select></label>)}
      <button onClick={load}>Geräte neu laden</button>
      <h2>Azure-Verbindungen</h2>
      {(['region', 'language', 'speechKey', 'endpoint', 'model', 'chatKey'] as const).map(key => <label key={key}>{{region:'Speech-Region',language:'Sprache',speechKey:'Speech-Schlüssel',endpoint:'Textmodell-Endpunkt',model:'Deployment',chatKey:'Textmodell-Schlüssel'}[key]}
        <input type={key.endsWith('Key') ? 'password' : 'text'} autoComplete="off" value={form[key]} maxLength={2048}
          placeholder={key.endsWith('Key') && state.options[key === 'speechKey' ? 'hasSpeechKey' : 'hasChatKey'] ? state.options[key + 'Saved'] ? 'Verschlüsselt gespeichert · leer lassen zum Beibehalten' : 'Für diesen Lauf geladen · mit Einstellungen speichern' : ''}
          onChange={e => setForm({ ...form, [key]: e.target.value })} />
      </label>)}
      {<div className="notes-more">{(['speechKey', 'chatKey'] as const).map(key => state.options[key === 'speechKey' ? 'hasSpeechKey' : 'hasChatKey'] && <button key={key} onClick={async () => {
        setBusy(true)
        try { await post('/api/notes/credentials/remove', { key }); setForm(f => ({ ...f, [key]: '' })); refresh(); setMessage('Schlüssel entfernt.') }
        catch (e) { setMessage(errorText(e)) } finally { setBusy(false) }
      }}>{key === 'speechKey' ? 'Speech-Schlüssel entfernen' : 'Textmodell-Schlüssel entfernen'}</button>)}</div>}
      <p className="quiet">Schlüssel werden mit Windows verschlüsselt im eigenen Benutzerprofil gespeichert und beim Start geladen. Zwischenstände und Abschlussnotizen werden direkt in Azure erstellt. Audio und Transkript werden nicht als Dateien gespeichert.</p>
      <button className="primary" onClick={configure}>Einstellungen übernehmen</button>
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
  useEffect(() => {
    mounted.current = true; void refresh()
    const timer = window.setInterval(refresh, 1000)
    return () => { mounted.current = false; window.clearInterval(timer) }
  }, [])
  const history = () => { setMessage(''); setSearch(''); setScreen('history') }
  const configure = () => { setMessage(''); setScreen('settings') }
  const back = () => { setMessage(''); setScreen('meeting'); setSelected(null) }
  const currentId = state?.currentId || state?.reviews[0]?.id
  const displayedId = selected || currentId
  const needsSetup = state?.options.managed === true && (!state.options.setupComplete || !state.options.onboardingComplete)
  const visibleScreen = needsSetup ? 'settings' : screen
  const historyReviews = state?.reviews.filter(r => (r.title + ' ' + new Date(r.started).toLocaleDateString('de-DE')).toLocaleLowerCase().includes(search.toLocaleLowerCase())) || []
  async function action(url: string) { try { await post(url); setMessage(''); await refresh() } catch (e) { setMessage(errorText(e)) } }
  const idle = () => {
    const managed = state?.options.managed === true
    const missing = state && (managed ? !state.options.setupComplete : !state.options.hasSpeechKey || !state.options.hasChatKey)
    const health = state?.health && state.health !== 'ok'
    const heading = !state ? 'Verbindung zum Client …' : !state.enabled ? 'Meeting-Notizen einrichten' : missing ? managed ? 'DAS-Zugang einrichten' : 'Azure-Zugang fehlt' : !state.autoStart ? 'Automatischer Start ist aus' : health ? 'Teams-Verbindung fehlt' : state.autoStartSuppressed ? 'Für diesen Anruf pausiert' : 'Bereit für Teams-Anrufe'
    return <Popup storage={state?.storagePath || ''} onHistory={history} saved="Ohne gemerktes Kundenziel: Auf diesem PC" onFolder={() => void action('/api/notes/open-folder')} onClose={() => void action('/api/notes/close')}>
      <div className="notes-status"><span className="status-label"><span className="status-dot" />{heading}</span></div>
      {connectionError || message || state?.error ? <p className="notice" role="alert">{connectionError || message || state?.error}</p> : <p className="quiet">{state?.enabled && !missing && state.autoStart && !health && !state.autoStartSuppressed ? 'Beim nächsten Teams-Anruf entstehen hier automatisch Ihre Notizen.' : 'Die Erfassung kann erst starten, wenn die Verbindung bereit ist.'}</p>}
      {state && (!state.enabled || missing) ? <button className="primary" onClick={configure}>Zugang einrichten</button>
        : state && !state.autoStart ? <button className="primary" onClick={() => void action('/api/auto-start/on')}>Automatischen Start einschalten</button>
        : state && health ? <button className="primary" onClick={() => void action(managed ? '/api/notes/connect' : '/api/sign-in')}>Mit Microsoft anmelden</button>
        : state && (state.autoStartSuppressed || state.error) ? <button onClick={configure}>Einstellungen öffnen</button> : null}
    </Popup>
  }
  return <>
    {state?.reviews.map(review => <ReviewPanel key={review.id} review={review} state={state}
      visible={visibleScreen === 'meeting' && review.id === displayedId} current={!selected} configure={configure} refresh={refresh} connectionError={connectionError}
      history={history} />)}
    {visibleScreen === 'meeting' && !state?.reviews.some(r => r.id === displayedId) && idle()}
    {visibleScreen === 'settings' && state && <Settings state={state} back={back} refresh={refresh} history={history} />}
    {visibleScreen === 'history' && state && <Popup storage={state.storagePath} saved="Gespeicherte Meeting-Notizen" onFolder={() => void action('/api/notes/open-folder')}>
      <button className="back" onClick={back}>Zum aktuellen Meeting</button><h1>Alle Meetings</h1>
      <label>Meeting suchen<input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Titel oder Datum" /></label>
      {message && <p className="notice">{message}</p>}
      {!state.reviews.length && <p className="quiet">Noch keine gespeicherten Meetings.</p>}
      {state.reviews.length >= 100 && <p className="quiet">Die letzten 100 Meetings. Ältere Dokumente finden Sie über „Ordner öffnen“.</p>}
      {!!state.reviews.length && !historyReviews.length && <p className="quiet">Kein Meeting mit diesem Titel oder Datum gefunden.</p>}
      {historyReviews.map(r => <div className="history-row" key={r.id}><div><strong>{r.title}</strong><p className="quiet">{new Date(r.started).toLocaleString('de-DE')}</p>
        <p className="quiet">{r.onenote?.status === 'saved' ? 'In OneNote gespeichert' : r.onenote?.mode === 'onenote' ? r.onenote.error || r.onenote.status === 'uncertain' ? 'Lokal gesichert · OneNote noch nicht bestätigt' : 'OneNote-Ablage ausstehend' : r.ended && r.phase === 'complete' && !r.storeError ? 'Auf diesem PC gespeichert' : 'Vorläufige Notizen'}</p>
        {(r.error || r.storeError) && <p className="notes-error">{r.storeError ? 'Nicht gespeichert' : 'Unvollständig'}</p>}</div>
        <button onClick={() => { setSelected(r.id); setScreen('meeting') }}>Öffnen</button></div>)}
    </Popup>}
  </>
}
