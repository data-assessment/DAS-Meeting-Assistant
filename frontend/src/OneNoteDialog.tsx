import { useEffect, useState } from 'react'
import { SearchableSelect } from './SearchableSelect'

interface Notebook { id: string; name: string; label?: string; sectionsUrl?: string | null; webUrl?: string | null }
interface Section { id: string; name: string }
interface TranscriptOption { id: string; title: string; startedAt?: string | null }
interface Targets {
  defaultNotebook: string
  defaultSection: string
  defaultNotebookId?: string
  transcriptTitle?: string
  selectedTranscriptId?: string
  pageTitle?: string
  uploadedUrl?: string | null
  transcripts?: TranscriptOption[]
  notebooks: Notebook[]
  warning?: string | null
  lastNotebookId?: string
  selectedNotebookId?: string
  selectedNotebookSections?: Section[]
  selectedNotebookSectionWarning?: string | null
  sectionChoices?: Record<string, { sectionId?: string; sectionName?: string }>
  notebookReason?: string
  sectionSuggestion?: { name?: string; reason?: string }
}
interface OneNoteChoice {
  notebookId: string
  notebookName?: string
  sectionsUrl?: string
  sectionId?: string
  sectionName?: string
}
type LoadingState = '' | 'targets' | 'sections'

function uploadToOneNote(payload: object): Promise<{ url?: string }> {
  return fetch('/api/onenote/upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(async (response) => {
    const data = await response.json().catch(() => ({}))
    if (!response.ok || !data.ok) throw new Error(data.error || 'OneNote upload failed')
    return data
  })
}

function transcriptLabel(transcript: TranscriptOption): string {
  const time = transcript.startedAt
    ? new Date(transcript.startedAt).toLocaleString([], {
        month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
      })
    : ''
  return `${time ? `${time} · ` : ''}${transcript.title}`
}

function rememberChoice(payload: OneNoteChoice, transcriptId = '') {
  fetch('/api/onenote/choice', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, transcriptId }),
  }).catch(() => {})
}

export function OneNoteDialog({ onClose }: { onClose: () => void }) {
  const [targets, setTargets] = useState<Targets | null>(null)
  const [sections, setSections] = useState<Section[]>([])
  const [notebookId, setNotebookId] = useState('')
  const [notebookName, setNotebookName] = useState('')
  const [sectionsUrl, setSectionsUrl] = useState('')
  const [sectionName, setSectionName] = useState('')
  const [transcriptId, setTranscriptId] = useState('')
  const [pageTitle, setPageTitle] = useState('')
  const [sectionRefreshKey, setSectionRefreshKey] = useState(0)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState<LoadingState>('')
  const [err, setErr] = useState('')
  const [savedUrl, setSavedUrl] = useState('')
  const [notebookReason, setNotebookReason] = useState('')
  const [sectionReason, setSectionReason] = useState('')

  const saveChoice = (choice: OneNoteChoice) => {
    rememberChoice(choice, transcriptId)
    setTargets((prev) => {
      if (!prev || !choice.notebookId) return prev
      const current = prev.sectionChoices?.[choice.notebookId] ?? {}
      return {
        ...prev,
        lastNotebookId: choice.notebookId,
        sectionChoices: {
          ...(prev.sectionChoices ?? {}),
          [choice.notebookId]: {
            ...current,
            sectionId: choice.sectionId ?? current.sectionId,
            sectionName: choice.sectionName ?? current.sectionName,
          },
        },
      }
    })
  }

  const load = (selectedTranscriptId = '', refresh = false) => {
    setErr(''); setTargets(null); setSavedUrl('')
    setLoading('targets')
    if (refresh) setSectionRefreshKey((key) => key + 1)
    const query = new URLSearchParams()
    if (selectedTranscriptId) query.set('transcript', selectedTranscriptId)
    if (refresh) query.set('refresh', 'true')
    const suffix = query.toString() ? `?${query.toString()}` : ''
    fetch(`/api/onenote/targets${suffix}`)
      .then((response) => response.json())
      .then((data) => {
        if (!data.ok) { setErr(data.error || 'Could not load OneNote notebooks'); return }
        const nextTargets = data as Targets
        setTargets(nextTargets)
        setNotebookReason(nextTargets.notebookReason || '')
        setSectionReason(nextTargets.sectionSuggestion?.reason || '')
        setTranscriptId(nextTargets.selectedTranscriptId || '')
        setPageTitle(nextTargets.pageTitle || nextTargets.transcriptTitle || '')
        const selected = nextTargets.notebooks.find((item) => item.id === nextTargets.lastNotebookId)
          ?? nextTargets.notebooks.find((item) => item.id === nextTargets.defaultNotebookId)
          ?? nextTargets.notebooks.find((item) => item.name.toLowerCase() === nextTargets.defaultNotebook.toLowerCase())
          ?? nextTargets.notebooks[0]
        if (selected) {
          setNotebookId(selected.id)
          setNotebookName(selected.name)
          setSectionsUrl(selected.sectionsUrl || '')
          setSections(Array.isArray(nextTargets.selectedNotebookSections) ? nextTargets.selectedNotebookSections : [])
        } else {
          setNotebookName(nextTargets.defaultNotebook)
          setSectionsUrl('')
          setSections([])
        }
        setSectionName(nextTargets.defaultSection)
      })
      .catch(() => setErr('Could not load OneNote notebooks'))
        .finally(() => setLoading(''))
  }
  useEffect(() => { load() }, [])

  // Both the warmed and the freshly fetched path choose a section the same way,
  // so the order lives in one place: the server's suggestion (learned for this
  // meeting, else the configured default) leads, then the per-notebook saved
  // choice, then the configured name, then whatever is first.
  const pickSection = (list: Section[]): string => {
    const suggested = targets?.sectionSuggestion?.name ?? ''
    const saved = targets?.sectionChoices?.[notebookId]
    const defaultSection = targets?.defaultSection ?? ''
    const byName = (name: string) => (
      name ? list.find((item) => item.name.toLowerCase() === name.toLowerCase()) : undefined
    )
    const preferred = byName(suggested)
      ?? list.find((item) => item.id === saved?.sectionId)
      ?? byName(saved?.sectionName ?? '')
      ?? byName(defaultSection)
      ?? list[0]
    return preferred?.name ?? saved?.sectionName ?? defaultSection
  }

  useEffect(() => {
    if (!notebookId) { setSections([]); setLoading(''); return }
    setErr('')
    const warmedSections = targets?.selectedNotebookId === notebookId && Array.isArray(targets.selectedNotebookSections)
      ? targets.selectedNotebookSections
      : null
    if (warmedSections) {
      setSections(warmedSections)
      if (targets?.selectedNotebookSectionWarning) setErr(targets.selectedNotebookSectionWarning)
      setSectionName(pickSection(warmedSections))
      return
    }
    setLoading('sections')
    const query = new URLSearchParams({ notebookId })
    if (sectionsUrl) query.set('sectionsUrl', sectionsUrl)
    if (sectionRefreshKey) query.set('refresh', 'true')
    fetch(`/api/onenote/sections?${query.toString()}`)
      .then((response) => response.json())
      .then((data) => {
        if (!data.ok) { setErr(data.error || 'Could not load OneNote sections'); return }
        const nextSections = Array.isArray(data.sections) ? data.sections as Section[] : []
        setSections(nextSections)
        setSectionName(pickSection(nextSections))
      })
      .catch(() => setErr('Could not load OneNote sections'))
        .finally(() => setLoading(''))
  }, [notebookId, sectionsUrl, sectionRefreshKey, targets?.defaultSection])

  const chooseCustom = () => {
    setBusy(true); setErr('')
    const section = sectionName.trim()
    const selectedSection = sections.find((item) => item.name.toLowerCase() === section.toLowerCase())
    const target = {
      notebookId,
      notebookName,
      sectionsUrl,
      sectionId: selectedSection?.id ?? '',
      sectionName: selectedSection?.name ?? section,
    }
    saveChoice(target)
    uploadToOneNote({
      action: 'custom',
      transcriptId,
      pageTitle,
      target,
    })
      .then((data) => { setBusy(false); setSavedUrl(data.url ?? '') })
      .catch((error) => { setBusy(false); setErr(error.message) })
  }

  const canChooseCustom = Boolean(
    notebookId && sectionName.trim() && pageTitle.trim(),
  )
  const loadingLabel = loading === 'targets'
    ? 'Loading OneNote notebooks...'
    : loading === 'sections'
      ? 'Loading OneNote sections...'
      : ''

  if (savedUrl) {
    return (
      <div className="settings onenote-dialog">
        <div className="settings-head"><span>Saved to OneNote</span></div>
        <div className="settings-body">
          <div className="onenote-default">
            <span className="settings-flabel">Transcript</span>
            <strong>{targets?.transcriptTitle || 'Last transcript'}</strong>
            <span>OneNote page created.</span>
          </div>
        </div>
        <div className="settings-foot">
          <span className="settings-status">Done</span>
          <span className="settings-buttons">
            <button className="ghost" onClick={() => window.open(savedUrl, '_blank')}>Open page</button>
            <button className="primary" onClick={onClose}>Close</button>
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="settings onenote-dialog">
      <div className="settings-head"><span>Save transcript to OneNote</span></div>
      <div className="settings-body">
        {loadingLabel && (
          <div className="dialog-loading" role="status" aria-live="polite">
            <span className="spinner" aria-hidden="true" />
            <span>{loadingLabel}</span>
          </div>
        )}
        {err && <div className="dialog-error"><pre className="dialog-error-text">{err}</pre></div>}
        {targets?.warning && <div className="status-message">{targets.warning}</div>}
        {(targets?.transcripts?.length ?? 0) > 1 && (
          <>
            <div className="dialog-section">Source</div>
            <label className="settings-field field-block compact-field">
              <span className="settings-flabel">Transcript</span>
              <select value={transcriptId} onChange={(event) => load(event.target.value)}>
                {(targets?.transcripts ?? []).map((transcript) => (
                  <option key={transcript.id} value={transcript.id}>{transcriptLabel(transcript)}</option>
                ))}
              </select>
            </label>
          </>
        )}
        <div className="dialog-section">Destination</div>
        <label className="settings-field field-block compact-field">
          <span className="settings-flabel">Notebook</span>
          <SearchableSelect
            value={notebookId}
            options={(targets?.notebooks ?? []).map((notebook) => ({
              value: notebook.id,
              label: notebook.label || notebook.name,
            }))}
            onChange={(nextNotebookId) => {
              const next = targets?.notebooks.find((item) => item.id === nextNotebookId)
              setNotebookReason('')
              setSectionReason('')
              setNotebookId(nextNotebookId)
              setNotebookName(next?.name ?? '')
              setSectionsUrl(next?.sectionsUrl || '')
              saveChoice({
                notebookId: nextNotebookId,
                notebookName: next?.name ?? '',
                sectionsUrl: next?.sectionsUrl || '',
              })
            }}
            placeholder="Search OneNote notebooks…"
          />
        </label>
        {notebookReason && (
          <span className="field-reason">Suggested — {notebookReason}</span>
        )}
        <div className="status-message">Choose a shared or team notebook for central storage.</div>
        <label className="settings-field field-block compact-field">
          <span className="settings-flabel">Section</span>
          {sections.length ? (
            <select value={sectionName} onChange={(event) => {
              const section = event.target.value
              const selected = sections.find((item) => item.name === section)
              setSectionReason('')
              setSectionName(section)
              saveChoice({
                notebookId,
                notebookName,
                sectionsUrl,
                sectionId: selected?.id ?? '',
                sectionName: selected?.name ?? section,
              })
            }}>
              {sections.map((section) => (
                <option key={section.id} value={section.name}>{section.name}</option>
              ))}
            </select>
          ) : (
            <input type="text" value={sectionName}
              onChange={(event) => setSectionName(event.target.value)}
              onBlur={() => saveChoice({
                notebookId,
                notebookName,
                sectionsUrl,
                sectionId: '',
                sectionName,
              })} />
          )}
        </label>
        {sectionReason && (
          <span className="field-reason">Suggested — {sectionReason}</span>
        )}
        <label className="settings-field field-block compact-field">
          <span className="settings-flabel">Page name</span>
          <input type="text" value={pageTitle} onChange={(event) => setPageTitle(event.target.value)} />
        </label>
      </div>
      <div className="settings-foot">
        <span className="settings-status">{loadingLabel}</span>
        <span className="settings-buttons">
          <button className="ghost" onClick={() => load(transcriptId, true)} disabled={busy || Boolean(loading)}>
            {loading === 'targets' && <span className="spinner button-spinner" aria-hidden="true" />}
            <span>{loading === 'targets' ? 'Refreshing...' : 'Refresh'}</span>
          </button>
          <button className="ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="primary" onClick={chooseCustom} disabled={busy || Boolean(loading) || !canChooseCustom}>
            {busy && <span className="spinner button-spinner" aria-hidden="true" />}
            <span>{busy ? 'Saving...' : 'Save here'}</span>
          </button>
        </span>
      </div>
    </div>
  )
}
