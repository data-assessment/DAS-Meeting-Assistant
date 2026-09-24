import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMeetingState, useElapsedSeconds, type JobInfo, type MeetingCandidate } from './hooks'
import { SettingsPanel } from './Settings'
import { MeetingNotes } from './MeetingNotes'
import { OneNoteDialog } from './OneNoteDialog'
import { RetryTranscriptionDialog } from './RetryTranscriptionDialog'
import { DocsDialog } from './DocsDialog'
import { UsageDialog } from './UsageDialog'

const LANGS = [
  ['auto', 'Auto'], ['de', 'DE'], ['en', 'EN'], ['fr', 'FR'], ['es', 'ES'], ['it', 'IT'],
]
const TARGETS = LANGS.filter(([v]) => v !== 'auto')

function formatDuration(totalSeconds: number): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  const h = Math.floor(totalSeconds / 3600)
  const m = Math.floor(totalSeconds / 60) % 60
  const s = totalSeconds % 60
  return `${pad(h)}:${pad(m)}:${pad(s)}`
}
const lineText = (raw: string) => raw.replace(/^•\s*/, '').trim()

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function formatCandidateTime(value: string | null): string {
  if (!value) return ''
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)
  const timestamp = hasTimezone ? value : `${value}Z`
  return new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function candidateLabel(candidate: MeetingCandidate): string {
  const time = formatCandidateTime(candidate.start)
  return `${time ? `${time} · ` : ''}${candidate.subject || 'Meeting'}`
}

/** One background finalize job: title, its own status line, and meeting duration. */
function JobRow({ job }: { job: JobInfo }) {
  const elapsed = useElapsedSeconds(job.startedAt, true)
  const secs = job.durationSeconds ?? elapsed
  return (
    <div className="job">
      <span className="spinner" aria-hidden="true" />
      <div className="job-copy">
        <span className="job-title">{job.title}</span>
        <span className="job-detail">{job.detail}</span>
      </div>
      <span className="job-time">{formatDuration(secs)}</span>
    </div>
  )
}

function setMeetingCandidate(id: string) { fetch(`/api/meeting-candidate/${id}`, { method: 'POST' }).catch(() => {}) }
function signIn() { fetch('/api/sign-in', { method: 'POST' }).catch(() => {}) }
function startRec() { fetch('/api/start', { method: 'POST' }).catch(() => {}) }
function stopRec() { fetch('/api/stop', { method: 'POST' }).catch(() => {}) }
function keepRecording() { fetch('/api/keep-recording', { method: 'POST' }).catch(() => {}) }
function closeView(view: 'settings' | 'transcript' | 'docs') { fetch(`/api/${view}-window/close`, { method: 'POST' }).catch(() => window.close()) }
function setPopupOnTop(onTop: boolean) {
  fetch('/api/popup-window/on-top', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ onTop }),
  }).catch(() => {})
}
function openLiveTranscript() { fetch('/api/transcript-window/show', { method: 'POST' }).catch(() => {}) }
function setLiveTranscript(enabled: boolean) { fetch(`/api/live/${enabled ? 'on' : 'off'}`, { method: 'POST' }).catch(() => {}) }
function openTranscripts() { fetch('/api/open-transcripts', { method: 'POST' }).catch(() => {}) }
function setAutoMode(enabled: boolean) { fetch(`/api/auto-start/${enabled ? 'on' : 'off'}`, { method: 'POST' }).catch(() => {}) }

function MicIcon() {
  return (
    <svg className="mic-icon" viewBox="0 0 82.05 122.88" aria-hidden="true">
      <path d="M59.89,20.83V52.3c0,27-37.73,27-37.73,0V20.83c0-27.77,37.73-27.77,37.73,0Zm-14.18,76V118.2a4.69,4.69,0,0,1-9.37,0V96.78a40.71,40.71,0,0,1-12.45-3.51A41.63,41.63,0,0,1,12.05,85L12,84.91A41.31,41.31,0,0,1,3.12,71.68,40.73,40.73,0,0,1,0,56a4.67,4.67,0,0,1,8-3.31l.1.1A4.68,4.68,0,0,1,9.37,56a31.27,31.27,0,0,0,2.4,12.06A32,32,0,0,0,29,85.28a31.41,31.41,0,0,0,24.13,0,31.89,31.89,0,0,0,10.29-6.9l.08-.07a32,32,0,0,0,6.82-10.22A31.27,31.27,0,0,0,72.68,56a4.69,4.69,0,0,1,9.37,0,40.65,40.65,0,0,1-3.12,15.65A41.45,41.45,0,0,1,70,85l-.09.08a41.34,41.34,0,0,1-11.75,8.18,40.86,40.86,0,0,1-12.46,3.51Z" />
    </svg>
  )
}

function measureVisibleHeight(el: HTMLElement): number {
  const style = window.getComputedStyle(el)
  const gap = parseFloat(style.rowGap || style.gap || '0') || 0
  const children = Array.from(el.children).filter((child) => {
    const html = child as HTMLElement
    return html.offsetParent !== null && html.getBoundingClientRect().height > 0
  }) as HTMLElement[]
  const content = children.reduce((sum, child) => {
    const shouldMeasureContents = child.classList.contains('settings')
      || child.classList.contains('settings-body')
    return sum + (shouldMeasureContents ? measureVisibleHeight(child) : child.getBoundingClientRect().height)
  }, 0)
  const gaps = Math.max(0, children.length - 1) * gap
  return Math.ceil(
    (parseFloat(style.paddingTop) || 0) + content + gaps + (parseFloat(style.paddingBottom) || 0),
  )
}

export default function App() {
  const routeView = new URLSearchParams(window.location.search).get('view')
  const popupRef = useRef<HTMLDivElement>(null)
  const { active, title, startedAt, jobs, backgroundTranscriptionStatus, liveTranscript,
    meetingDetected, meetingCandidates, selectedMeetingCandidateId,
    meetingCandidateStatus, meetingCandidateNote, health,
    liveOn, liveTranslated,
    usageEnabled,
    recordingsCanRetry, transcriptsCanOpen, lastTranscriptReady, oneNoteCanSave, autoStart,
    manual, stopSuggestion, notesEnabled, notesError, notesCount } = useMeetingState()
  const healthLabel =
    health === 'offline' ? "Offline — can't reach Microsoft"
      : health === 'auth' ? 'Sign-in required'
      : health === 'error' ? 'Presence error'
      : null
  const elapsed = useElapsedSeconds(startedAt, active)
  const showLive = active || liveOn || Boolean(liveTranscript)
  const [showOneNote, setShowOneNote] = useState(false)
  const [showRetry, setShowRetry] = useState(false)
  const [showDocs, setShowDocs] = useState(false)
  const [showUsage, setShowUsage] = useState(false)
  const toggleLiveTranscript = () => {
    if (liveOn) setLiveTranscript(false)
    else openLiveTranscript()
  }

  // Continuous live translation: each line translated once (cached), incrementally,
  // so incoming text is never lost while the model works. Lines not yet translated
  // show raw until their translation arrives.
  const [transTo, setTransTo] = useState('')          // '' = off
  const cache = useRef<Map<string, string>>(new Map())
  const pending = useRef<Set<string>>(new Set())
  const [ver, setVer] = useState(0)
  const bump = () => setVer((v) => v + 1)

  useEffect(() => { cache.current.clear(); pending.current.clear(); bump() }, [transTo])
  useEffect(() => { if (!liveTranscript) { cache.current.clear(); pending.current.clear() } }, [liveTranscript])

  useEffect(() => {
    // The translate live model already returns translated text — don't translate again.
    if (!transTo || !liveTranscript || liveTranslated) return
    for (const raw of liveTranscript.split('\n')) {
      const ln = lineText(raw)
      if (!ln || cache.current.has(ln) || pending.current.has(ln)) continue
      pending.current.add(ln)
      fetch('/api/transform', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: ln, mode: 'translate', lang: transTo }),
      })
        .then((r) => r.json())
        .then((j) => { cache.current.set(ln, j.text || ln); pending.current.delete(ln); bump() })
        .catch(() => pending.current.delete(ln))
    }
  }, [liveTranscript, transTo])

  const translated = useMemo(() => {
    if (!transTo || liveTranslated) return liveTranscript
    return liveTranscript.split('\n').map((raw) => {
      const ln = lineText(raw)
      return ln ? '• ' + (cache.current.get(ln) ?? ln) : raw
    }).join('\n')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveTranscript, transTo, liveTranslated, ver])

  const display = translated
  const [findOpen, setFindOpen] = useState(false)
  const [findQuery, setFindQuery] = useState('')
  const [findIndex, setFindIndex] = useState(0)
  const findInputRef = useRef<HTMLInputElement>(null)

  const openFind = () => {
    if (routeView !== 'transcript') {
      openLiveTranscript()
      return
    }
    setFindOpen(true)
    requestAnimationFrame(() => findInputRef.current?.select())
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
        event.preventDefault()
        openFind()
      } else if (event.key === 'Escape' && findOpen) {
        event.preventDefault()
        setFindOpen(false)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [findOpen])

  useEffect(() => { setFindIndex(0) }, [findQuery])

  const findNeedle = findQuery.trim()
  const highlightedLive = useMemo(() => {
    if (!findNeedle) return { nodes: display as ReactNode, count: 0 }
    const regex = new RegExp(escapeRegExp(findNeedle), 'ig')
    const nodes: ReactNode[] = []
    let lastIndex = 0
    let matchIndex = 0
    for (const match of display.matchAll(regex)) {
      const index = match.index ?? 0
      if (index > lastIndex) nodes.push(display.slice(lastIndex, index))
      const active = matchIndex === findIndex
      nodes.push(
        <mark key={`${index}-${matchIndex}`} className={`find-hit${active ? ' active' : ''}`}
          data-find-active={active ? 'true' : undefined}>
          {match[0]}
        </mark>,
      )
      lastIndex = index + match[0].length
      matchIndex += 1
    }
    if (lastIndex < display.length) nodes.push(display.slice(lastIndex))
    return { nodes, count: matchIndex }
  }, [display, findNeedle, findIndex])

  useEffect(() => {
    if (highlightedLive.count && findIndex >= highlightedLive.count) setFindIndex(0)
  }, [highlightedLive.count, findIndex])

  useEffect(() => {
    const activeHit = liveRef.current?.querySelector('[data-find-active="true"]')
    if (activeHit instanceof HTMLElement) {
      stickToBottom.current = false
      activeHit.scrollIntoView({ block: 'center' })
    }
  }, [findIndex, highlightedLive.count, findNeedle])

  const moveFind = (step: number) => {
    if (!highlightedLive.count) return
    setFindIndex((index) => (index + step + highlightedLive.count) % highlightedLive.count)
  }
  const findPosition = highlightedLive.count ? findIndex + 1 : 0

  useEffect(() => {
    if (!showLive) {
      setFindOpen(false)
    }
  }, [showLive])

  // Stick to the tail until the user scrolls up. The decision must be made from the
  // scroll position BEFORE new text arrives (an onScroll handler), not after — once
  // the content has grown, a single large update can look "scrolled away" and wrongly
  // stop following even while the user sits at the bottom.
  const liveRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const onLiveScroll = () => {
    const el = liveRef.current
    if (el) stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }
  useEffect(() => {
    const el = liveRef.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [display])
  // Re-arm following whenever the live view (re)appears, e.g. a new meeting.
  useEffect(() => { stickToBottom.current = true }, [showLive])

  const hasProcessing = jobs.length > 0
  const hasPostActions = !active && !hasProcessing && lastTranscriptReady
  const displayTitle = active || meetingDetected
    ? title || 'Meeting'
    : hasPostActions ? 'Transcript ready' : 'DAS Meeting Assistant'
  const stateTone = healthLabel ? 'warn' : active ? 'rec' : meetingDetected ? 'ready' : hasProcessing ? 'busy' : hasPostActions ? 'ready' : 'idle'
  const stateLabel = healthLabel ?? (
    active ? 'Recording'
      : meetingDetected ? 'Meeting detected'
        : hasProcessing ? 'Processing'
          : hasPostActions ? 'Ready'
          : 'Idle'
  )
  const stateDetail = active
    ? `${manual ? 'Manual recording' : 'Auto recording'} · ${formatDuration(elapsed)}`
    : meetingDetected
      ? 'On-demand start is waiting for you'
      : hasProcessing
        ? `${jobs.length} transcript${jobs.length === 1 ? '' : 's'} in progress`
        : hasPostActions
          ? 'Choose an export destination'
        : 'Ready for the next meeting'

  const headLabel = liveTranslated
    ? `Live · translated → ${transTo.toUpperCase() || 'EN'} (model)`
    : transTo ? `Live · translated → ${transTo.toUpperCase()}` : 'Live transcript'
  const selectedCandidate = meetingCandidates.find((c) => c.id === selectedMeetingCandidateId) ?? null
  const showMeetingMapping = (active || meetingDetected) && meetingCandidateStatus !== 'idle'

  useEffect(() => {
    if (routeView === 'settings' || routeView === 'transcript') return
    const el = popupRef.current
    if (!el) return
    // Each resize blocks the GUI thread until it repaints, so this must converge
    // fast and then go quiet. Triggers are coalesced into one frame, and heights
    // requested in the last moment are remembered so a layout settling into a
    // repeating cycle (the classic scrollbar flip-flop) stops instead of resizing
    // forever. That memory has to expire: a dialog legitimately returns to an
    // earlier height (error view -> Refresh -> form again), and remembering it
    // permanently left the window stuck at the wrong size showing a scrollbar.
    // A resize loop cycles within a frame or two, so anything older than this
    // cannot be part of one.
    const OSCILLATION_MS = 700
    // Backstop for a cycle longer than that memory: too many resizes in a short
    // span means the layout will not converge, so stop and let content scroll.
    const BURST_MS = 5000
    const BURST_LIMIT = 60
    const recent: { height: number, at: number }[] = []
    const posted: number[] = []
    let giveUp = false
    let frame = 0
    const fit = () => {
      frame = 0
      if (giveUp) return
      const now = performance.now()
      while (recent.length && now - recent[0].at > OSCILLATION_MS) recent.shift()
      while (posted.length && now - posted[0] > BURST_MS) posted.shift()
      const height = measureVisibleHeight(el)
      if (recent.some((seen) => Math.abs(height - seen.height) < 3)) return
      if (posted.length >= BURST_LIMIT) { giveUp = true; return }
      recent.push({ height, at: now })
      posted.push(now)
      fetch('/api/popup-window/fit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ height }),
      }).catch(() => {})
    }
    const scheduleFit = () => {
      if (frame) return
      frame = window.requestAnimationFrame(fit)
    }
    // Dialog bodies are flex-stretched scroll containers, so their own box never
    // changes size; watch the rows that do. Content that appears later (spinner
    // -> form, detected topics, created results) arrives as a mutation instead.
    const observer = new ResizeObserver(scheduleFit)
    const observeRows = () => {
      observer.disconnect()
      observer.observe(el)
      el.querySelectorAll<HTMLElement>('.settings, .settings-body, .settings-body > *')
        .forEach((child) => observer.observe(child))
    }
    const mutations = new MutationObserver(() => { observeRows(); scheduleFit() })
    mutations.observe(el, { childList: true, subtree: true })
    observeRows()
    fit()
    const timeout = window.setTimeout(scheduleFit, 80)
    return () => {
      observer.disconnect()
      mutations.disconnect()
      window.clearTimeout(timeout)
      window.cancelAnimationFrame(frame)
    }
  }, [routeView, active, title, jobs.length, showLive, lastTranscriptReady,
    oneNoteCanSave, recordingsCanRetry, autoStart, showOneNote, showRetry, showDocs,
    meetingDetected, showMeetingMapping, stopSuggestion, health, showUsage])

  // The idle popup stays above other windows as a presence reminder. While an
  // export dialog is open the user needs to reach Teams, a browser or Explorer,
  // so the window steps back to normal stacking until the dialog closes.
  const dialogOpen = showOneNote || showRetry || showDocs || showUsage
  useEffect(() => {
    if (routeView === 'settings' || routeView === 'transcript' || routeView === 'docs') return
    setPopupOnTop(!dialogOpen)
  }, [routeView, dialogOpen])

  const liveTranscriptPanel = (
    <section className="live-wrap">
      <div className="live-head">
        <span>{headLabel}</span>
        <div className="live-actions">
          <label className="lang">
            Translate
            <select
              value={transTo}
              onChange={(e) => {
                setTransTo(e.target.value)
                fetch(`/api/translate-to/${e.target.value || 'off'}`, { method: 'POST' }).catch(() => {})
              }}
            >
              <option value="">Off</option>
              {TARGETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <button className="ghost" onClick={openFind}>Find</button>
          <button className="ghost" onClick={() => navigator.clipboard?.writeText(display)}>Copy</button>
          <button className="ghost live-toggle" aria-pressed={liveOn} onClick={toggleLiveTranscript}>
            {liveOn ? 'Live on' : 'Start live'}
          </button>
        </div>
      </div>
      {findOpen && (
        <div className="find-bar">
          <input ref={findInputRef} className="find-input" value={findQuery}
            onChange={(e) => setFindQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                moveFind(e.shiftKey ? -1 : 1)
              } else if (e.key === 'Escape') {
                e.preventDefault()
                setFindOpen(false)
              }
            }}
            placeholder="Find" />
          <span className="find-count">
            {findNeedle ? `${findPosition}/${highlightedLive.count}` : ''}
          </span>
          <button className="ghost" onClick={() => moveFind(-1)} disabled={!highlightedLive.count}>Prev</button>
          <button className="ghost" onClick={() => moveFind(1)} disabled={!highlightedLive.count}>Next</button>
          <button className="ghost" onClick={() => setFindOpen(false)}>x</button>
        </div>
      )}
      <div className="live" ref={liveRef} onScroll={onLiveScroll}>{display ? highlightedLive.nodes : <span className="muted">{active ? (liveOn ? 'Listening for speech…' : 'Live transcript is stopped.') : 'No live transcript yet.'}</span>}</div>
    </section>
  )

  if (routeView === 'notes') return <MeetingNotes />
  if (routeView === 'settings') {
    return <div className="app settings-shell"><SettingsPanel onClose={() => closeView('settings')} /></div>
  }
  if (routeView === 'transcript') {
    return <div className="app transcript-shell">{liveTranscriptPanel}</div>
  }
  if (routeView === 'docs') {
    return <div className="app settings-shell"><DocsDialog onClose={() => closeView('docs')} /></div>
  }
  if (showOneNote) {
    return <div className="app popup-shell" ref={popupRef}><OneNoteDialog onClose={() => setShowOneNote(false)} /></div>
  }
  if (showRetry) {
    return <div className="app popup-shell" ref={popupRef}><RetryTranscriptionDialog onClose={() => setShowRetry(false)} /></div>
  }
  if (showDocs) {
    return <div className="app popup-shell" ref={popupRef}><DocsDialog onClose={() => setShowDocs(false)} /></div>
  }
  if (showUsage) {
    return <div className="app popup-shell" ref={popupRef}><UsageDialog onClose={() => setShowUsage(false)} /></div>
  }

  return (
    <div className="app popup-shell" ref={popupRef}>
      <section className={`hero state-${stateTone}`}>
        {health === 'auth' ? (
          <div className="signin">
            <span className="state-pill">Attention</span>
            <h1>Sign in required</h1>
            <p>Microsoft sign-in is needed to detect meetings.</p>
            <button className="primary" onClick={signIn}>Sign in</button>
          </div>
        ) : (
          <>
            <div className="state-row">
              <span className={`state-dot ${stateTone}`} aria-hidden="true" />
              <span className="state-label">{stateLabel}</span>
              <button className={`mode-toggle ${autoStart ? 'auto' : 'pause'}`}
                onClick={() => setAutoMode(!autoStart)}
                title={autoStart ? 'Pause automatic recording' : 'Resume automatic recording'}>
                {autoStart ? 'Auto' : 'Pause'}
              </button>
            </div>
            <h1>{displayTitle}</h1>
            <div className="timer">{active ? formatDuration(elapsed) : stateDetail}</div>
            {active && backgroundTranscriptionStatus && (
              <div className="recording-activity" role="status" aria-live="polite">
                {backgroundTranscriptionStatus}
              </div>
            )}
            <div className="hero-actions">
              {active ? (
                <button className="action-button stop" onClick={stopRec}
                  title={notesEnabled ? "Meeting stoppen – Notizen werden automatisch gespeichert" : "Stop recording and save the transcript now"} aria-label="Stop recording">
                  <span aria-hidden="true">■</span>
                </button>
              ) : (
                <button className="action-button start" onClick={startRec}
                  title={meetingDetected ? 'Start recording this meeting' : 'Start recording manually'}
                  aria-label="Start recording">
                  <MicIcon />
                </button>
              )}
            </div>
          </>
        )}
      </section>

      {active && stopSuggestion && (
        <section className="prompt">
          <div className="prompt-copy">
            <strong>{stopSuggestion.reason}</strong>
            <span>{stopSuggestion.detail}</span>
          </div>
          <div className="prompt-actions">
            <button className="ghost" onClick={keepRecording}>Keep recording</button>
            <button className="rec-btn stop" onClick={stopRec}>Stop</button>
          </div>
        </section>
      )}

      {showMeetingMapping && (
        <section className="prompt meeting-map">
          <div className="prompt-copy">
            <strong>{meetingCandidateStatus === 'matched' && meetingCandidates.length > 1 && !selectedMeetingCandidateId ? 'Which meeting is this?' : 'Calendar match'}</strong>
            {meetingCandidateStatus === 'loading' ? (
              <span>Looking up scheduled Teams meetings…</span>
            ) : meetingCandidateStatus === 'matched' && meetingCandidates.length > 1 ? (
              <select className="candidate-select" value={selectedMeetingCandidateId ?? ''}
                onChange={(e) => setMeetingCandidate(e.target.value)}>
                {!selectedMeetingCandidateId && <option value="" disabled>Choose current meeting…</option>}
                {meetingCandidates.map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>{candidateLabel(candidate)}</option>
                ))}
              </select>
            ) : meetingCandidateStatus === 'matched' && selectedCandidate ? (
              <span>{candidateLabel(selectedCandidate)}</span>
            ) : (
              <span>{meetingCandidateNote ?? 'No calendar meeting selected'}</span>
            )}
          </div>
          <div className="prompt-actions">
            <button className="ghost" onClick={() => setMeetingCandidate('reject')}>Ad-hoc</button>
          </div>
        </section>
      )}

      {jobs.length > 0 && (
        <section className="jobs">
          <div className="jobs-head">
            {active ? 'Background processing' : 'Processing'}{jobs.length > 1 ? ` · ${jobs.length}` : ''}
          </div>
          {jobs.map((j) => <JobRow key={j.id} job={j} />)}
        </section>
      )}

      <section className="prompt">
        <div className="prompt-copy"><strong>{notesEnabled ? 'Meeting-Notizen' : 'Meeting-Notizen testen'}</strong>
          <span>{notesError || (notesEnabled ? 'Notizen und Aufgaben werden automatisch gespeichert.' : 'Azure Mix und bearbeitbare Ergebnisse')}</span></div>
        <button className="ghost" onClick={() => fetch('/api/notes/show', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })}>Öffnen{notesCount ? ` (${notesCount})` : ''}</button>
      </section>
      <section className="quick-actions">
        <div className="action-group docs-group" aria-label="Documentation actions">
          <button className="icon-chip docs-chip" onClick={() => setShowDocs(true)}
            title="Open built-in documentation">
            <span aria-hidden="true">?</span>
            <span>Docs</span>
          </button>
          {usageEnabled && (
            <button className="icon-chip docs-chip" onClick={() => setShowUsage(true)}
              title="Show your managed-service usage">
              <span aria-hidden="true">∑</span>
              <span>Usage</span>
            </button>
          )}
        </div>
        {showLive && !notesEnabled && (
            <div className="action-group live-group">
              <button className="icon-chip live-toggle" aria-pressed={liveOn}
                onClick={toggleLiveTranscript}
                title={liveOn ? 'Stop live transcription' : 'Start live transcription and open the window'}
                aria-label={liveOn ? 'Stop live transcription' : 'Start live transcription and open the window'}>
                <span aria-hidden="true">{liveOn ? '●' : '≡'}</span>
                <span>Live transcript</span>
              </button>
            </div>
          )}
          {(transcriptsCanOpen || recordingsCanRetry) && (
            <div className="action-group local-group" aria-label="Local transcript and recording actions">
              {transcriptsCanOpen && (
                <button className="icon-chip local-chip" onClick={openTranscripts}
                  title="Open the local transcripts folder in File Explorer">
                  <span aria-hidden="true">⌂</span>
                  <span>Transcripts folder</span>
                </button>
              )}
              {recordingsCanRetry && (
              <button className="icon-chip local-chip" onClick={() => setShowRetry(true)}
                title="Retry transcription from a saved recording">
                <span aria-hidden="true">↻</span>
                <span>Retry transcription</span>
              </button>
              )}
            </div>
          )}
          {oneNoteCanSave && (
            <div className="action-group upload-group" aria-label="Export actions">
              {oneNoteCanSave && (
                <button className="icon-chip upload-chip" onClick={() => setShowOneNote(true)}
                  title="Save the last transcript to OneNote">
                  <span aria-hidden="true">＋</span>
                  <span>OneNote page</span>
                </button>
              )}
            </div>
          )}
        </section>

    </div>
  )
}
