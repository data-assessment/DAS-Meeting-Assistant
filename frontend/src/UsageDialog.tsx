import { useEffect, useState } from 'react'

interface UsageGroup {
  requests: number
  inputTokens: number
  outputTokens: number
  totalTokens: number
  audioSeconds: number
  sessionSeconds: number
}

interface UsageData {
  period: string
  pendingEvents?: number
  operations: Record<'chat' | 'batch_stt' | 'realtime_stt', UsageGroup>
}

const integer = new Intl.NumberFormat()

function minutes(seconds: number): string {
  return `${(Number(seconds || 0) / 60).toFixed(1)} min`
}

function UsageCard({ title, group, kind }: {
  title: string
  group: UsageGroup
  kind: 'tokens' | 'audio' | 'session'
}) {
  const value = kind === 'tokens'
    ? integer.format(group.totalTokens || group.inputTokens + group.outputTokens)
    : minutes(kind === 'audio' ? group.audioSeconds : group.sessionSeconds)
  return (
    <section className="usage-card">
      <span>{title}</span>
      <strong>{value}</strong>
      <small>{integer.format(group.requests)} successful request{group.requests === 1 ? '' : 's'}</small>
      {kind === 'tokens' && (
        <small>{integer.format(group.inputTokens)} input · {integer.format(group.outputTokens)} output</small>
      )}
      {kind !== 'tokens' && group.totalTokens > 0 && (
        <small>{integer.format(group.totalTokens)} tokens</small>
      )}
    </section>
  )
}

export function UsageDialog({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<UsageData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    setError('')
    fetch('/api/usage/summary')
      .then((response) => response.json())
      .then((value) => {
        if (!value.ok) throw new Error(value.error || 'Usage unavailable')
        setData(value)
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Usage unavailable'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const empty: UsageGroup = {
    requests: 0, inputTokens: 0, outputTokens: 0, totalTokens: 0,
    audioSeconds: 0, sessionSeconds: 0,
  }

  return (
    <div className="settings usage-dialog">
      <div className="settings-head"><span>Usage</span></div>
      <div className="settings-body usage-body">
        {loading && !data ? (
          <div className="dialog-loading" role="status"><span className="spinner" />Loading usage…</div>
        ) : error ? (
          <div className="dialog-error"><pre className="dialog-error-text">{error}</pre></div>
        ) : data ? (
          <>
            <div className="usage-period">{data.period} · your usage</div>
            <div className="usage-grid">
              <UsageCard title="LLM" group={data.operations.chat || empty} kind="tokens" />
              <UsageCard title="Batch transcription" group={data.operations.batch_stt || empty} kind="audio" />
              <UsageCard title="Live transcription" group={data.operations.realtime_stt || empty} kind="session" />
            </div>
            <p className="usage-note">Usage metadata only. Audio, prompts, transcripts, and model responses are not recorded.</p>
            {!!data.pendingEvents && <p className="usage-note">{data.pendingEvents} event(s) are waiting to upload.</p>}
          </>
        ) : null}
      </div>
      <div className="settings-foot">
        <span className="settings-status">Current calendar month (UTC)</span>
        <span className="settings-buttons">
          <button className="ghost" onClick={load} disabled={loading}>Refresh</button>
          <button className="ghost" onClick={onClose}>Close</button>
        </span>
      </div>
    </div>
  )
}
