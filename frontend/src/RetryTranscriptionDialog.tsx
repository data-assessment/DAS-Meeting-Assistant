import { useEffect, useState } from 'react'

interface RecordingOption {
  id: string
  title: string
  startedAt?: string | null
  durationSeconds?: number | null
  sizeBytes?: number | null
}

function formatRecording(recording: RecordingOption): string {
  const time = recording.startedAt
    ? new Date(recording.startedAt).toLocaleString([], {
        month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
      })
    : ''
  const minutes = recording.durationSeconds
    ? ` · ${Math.max(1, Math.round(recording.durationSeconds / 60))} min`
    : ''
  return `${time ? `${time} · ` : ''}${recording.title}${minutes}`
}

export function RetryTranscriptionDialog({ onClose }: { onClose: () => void }) {
  const [recordings, setRecordings] = useState<RecordingOption[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const load = () => {
    setErr('')
    fetch('/api/retry-transcription/recordings')
      .then((response) => response.json())
      .then((data) => {
        if (!data.ok) { setErr(data.error || 'Could not load recordings'); return }
        const items = Array.isArray(data.recordings) ? data.recordings as RecordingOption[] : []
        setRecordings(items)
        setSelectedId((current) => current || items[0]?.id || '')
      })
      .catch(() => setErr('Could not load recordings'))
  }
  useEffect(load, [])

  const retry = () => {
    if (!selectedId) return
    setBusy(true); setErr(''); setMsg('Starting retry...')
    fetch('/api/retry-transcription/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recordingId: selectedId }),
    })
      .then((response) => response.json())
      .then((data) => {
        setBusy(false)
        if (!data.ok) { setErr(data.error || 'Retry failed'); setMsg(''); return }
        onClose()
      })
      .catch(() => { setBusy(false); setErr('Retry failed'); setMsg('') })
  }

  return (
    <div className="settings retry-dialog">
      <div className="settings-head"><span>Retry transcription</span></div>
      <div className="settings-body">
        {err && <div className="dialog-error"><pre className="dialog-error-text">{err}</pre></div>}
        {msg && <div className="status-message">{msg}</div>}
        {!recordings.length && !err ? (
          <div className="muted">No local WAV recordings found.</div>
        ) : (
          <label className="settings-field field-block compact-field">
            <span className="settings-flabel">Recording</span>
            <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
              {recordings.map((recording) => (
                <option key={recording.id} value={recording.id}>{formatRecording(recording)}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="settings-foot">
        <span className="settings-status">Saved recordings</span>
        <span className="settings-buttons">
          <button className="ghost" onClick={load} disabled={busy}>Refresh</button>
          <button className="ghost" onClick={onClose} disabled={busy}>Close</button>
          <button className="primary" onClick={retry} disabled={busy || !selectedId}>{busy ? 'Starting...' : 'Retry'}</button>
        </span>
      </div>
    </div>
  )
}
